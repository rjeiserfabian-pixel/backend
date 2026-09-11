from django.test import TestCase
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario, Rol, Modulo, Permiso, RolPermiso


class AsignarPermisosRolTests(TestCase):
    """Cubre el guardado de permisos de un rol (POST /roles/{id}/permisos/)."""

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_permisos_test', email='admin_permisos_test@example.com',
            nombres='Admin', apellidos='PermisosTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

        self.rol = Rol.objects.create(codigo='ROL_TEST', nombre='Rol de Prueba')
        modulo = Modulo.objects.create(codigo='MOD_TEST', nombre='Modulo Test', orden=1)
        self.permiso1 = Permiso.objects.create(
            id_modulo=modulo, codigo='MOD_TEST.VER', nombre='Ver', accion='VER'
        )
        self.permiso2 = Permiso.objects.create(
            id_modulo=modulo, codigo='MOD_TEST.CREAR', nombre='Crear', accion='CREAR'
        )

    def test_asignar_permisos_a_rol(self):
        resp = self.client.post(f'/api/seguridad/roles/{self.rol.id_rol}/permisos/', {
            'permisos': [
                {'id_permiso': self.permiso1.id_permiso, 'alcance': 'GLOBAL'},
                {'id_permiso': self.permiso2.id_permiso, 'alcance': 'PROPIO'},
            ]
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        asignados = RolPermiso.objects.filter(id_rol=self.rol)
        self.assertEqual(asignados.count(), 2)
        self.assertTrue(asignados.filter(id_permiso=self.permiso1, alcance='GLOBAL').exists())

    def test_reasignar_permisos_reemplaza_los_anteriores(self):
        RolPermiso.objects.create(id_rol=self.rol, id_permiso=self.permiso1, alcance='GLOBAL')

        resp = self.client.post(f'/api/seguridad/roles/{self.rol.id_rol}/permisos/', {
            'permisos': [{'id_permiso': self.permiso2.id_permiso, 'alcance': 'PROPIO'}]
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        asignados = RolPermiso.objects.filter(id_rol=self.rol)
        self.assertEqual(asignados.count(), 1)
        self.assertEqual(asignados.first().id_permiso_id, self.permiso2.id_permiso)


class PermisoListViewTests(TestCase):
    """
    Cubre el bug real ya corregido en esta sesión: el catálogo de permisos
    debe devolverse completo, sin truncarse por la paginación por defecto.
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_catalogo_test', email='admin_catalogo_test@example.com',
            nombres='Admin', apellidos='CatalogoTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

        modulo = Modulo.objects.create(codigo='MOD_CATALOGO', nombre='Modulo Catalogo', orden=1)
        # Más permisos que el tamaño de página global (25), para detectar
        # una regresión si alguien vuelve a activar la paginación por defecto.
        for i in range(30):
            Permiso.objects.create(
                id_modulo=modulo, codigo=f'MOD_CATALOGO.PERM_{i}', nombre=f'Permiso {i}', accion='VER'
            )

    def test_permiso_list_devuelve_catalogo_completo_sin_paginar(self):
        resp = self.client.get('/api/seguridad/permisos/')
        self.assertEqual(resp.status_code, 200)

        data = resp.data['data']
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 30)
