from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.seguridad.models import Modulo, Permiso, Rol, RolPermiso, Usuario


class RolDeSistemaTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_rol_sistema', email='admin_rol_sistema@example.com',
            nombres='Admin', apellidos='RolSistema', password='x',
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)
        modulo = Modulo.objects.create(codigo='MOD_RS', nombre='Mod RS', orden=1)
        self.permiso = Permiso.objects.create(id_modulo=modulo, codigo='MOD_RS.VER', nombre='Ver', accion='VER')

    def test_los_permisos_de_un_rol_de_sistema_no_se_pueden_cambiar_ni_por_la_api(self):
        soporte = Rol.objects.create(codigo='SOPORTE_TEST', nombre='Soporte Test', es_sistema=True)
        RolPermiso.objects.create(id_rol=soporte, id_permiso=self.permiso, alcance='GLOBAL')

        resp = self.api.post(f'/api/seguridad/roles/{soporte.id_rol}/permisos/', {'permisos': []}, format='json')

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(RolPermiso.objects.filter(id_rol=soporte).count(), 1)  # sigue igual

    def test_un_rol_normal_si_se_puede_editar(self):
        normal = Rol.objects.create(codigo='NORMAL_TEST', nombre='Normal Test')
        resp = self.api.post(
            f'/api/seguridad/roles/{normal.id_rol}/permisos/',
            {'permisos': [{'id_permiso': self.permiso.id_permiso, 'alcance': 'GLOBAL'}]}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(RolPermiso.objects.filter(id_rol=normal).count(), 1)

    def test_los_datos_iniciales_dan_todo_a_soporte_y_no_tocan_al_administrador(self):
        administrador = Rol.objects.create(codigo='ADMINISTRADOR', nombre='Administrador')  # rol normal
        call_command('cargar_datos_iniciales', stdout=StringIO())

        total = Permiso.objects.filter(estado=True).count()
        soporte = Rol.objects.get(codigo='SOPORTE')
        self.assertTrue(soporte.es_sistema)
        self.assertEqual(RolPermiso.objects.filter(id_rol=soporte, alcance='GLOBAL').count(), total)
        self.assertEqual(RolPermiso.objects.filter(id_rol=administrador).count(), 0)

        # Si más adelante aparece un permiso nuevo, Soporte lo recibe solo al volver a cargar.
        nuevo = Permiso.objects.create(id_modulo=self.permiso.id_modulo, codigo='MOD_RS.NUEVO', nombre='Nuevo', accion='VER')
        call_command('cargar_datos_iniciales', stdout=StringIO())
        self.assertTrue(RolPermiso.objects.filter(id_rol=soporte, id_permiso=nuevo, alcance='GLOBAL').exists())
        self.assertEqual(RolPermiso.objects.filter(id_rol=administrador).count(), 0)
