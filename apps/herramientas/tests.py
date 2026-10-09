from django.test import TestCase
from rest_framework.test import APIClient

from apps.inventario.models import Almacen, Sucursal
from apps.seguridad.models import Usuario

from .models import CategoriaHerramienta, Herramienta, HistorialHerramienta


class HerramientasBaseTestCase(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_herr', email='admin_herr@example.com',
            nombres='Admin', apellidos='Herr', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.sucursal = Sucursal.objects.create(nombre='Principal')
        self.otra_sucursal = Sucursal.objects.create(nombre='Norte')
        self.almacen = Almacen.objects.create(sucursal=self.sucursal, nombre='Herramientas')
        self.categoria = CategoriaHerramienta.objects.create(nombre='Amoladoras')

    def payload(self, **extra):
        data = {
            'nombre': 'Amoladora 7"', 'categoria': self.categoria.id,
            'sucursal': self.sucursal.id, 'marca': 'Bosch',
        }
        data.update(extra)
        return data


class CategoriaTests(HerramientasBaseTestCase):
    def test_crear_y_listar_sin_paginar(self):
        resp = self.client.post('/api/herramientas/categorias/', {'nombre': 'Sierras'}, format='json')
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get('/api/herramientas/categorias/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 2)

    def test_nombre_duplicado_case_insensitive(self):
        resp = self.client.post('/api/herramientas/categorias/', {'nombre': 'amoladoras'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_no_elimina_categoria_con_herramientas(self):
        self.client.post('/api/herramientas/herramientas/', self.payload(), format='json')
        resp = self.client.delete(f'/api/herramientas/categorias/{self.categoria.id}/')
        self.assertEqual(resp.status_code, 400)
        self.categoria.refresh_from_db()
        self.assertTrue(self.categoria.estado)

    def test_eliminar_categoria_vacia_es_soft_delete(self):
        resp = self.client.delete(f'/api/herramientas/categorias/{self.categoria.id}/')
        self.assertEqual(resp.status_code, 204)
        self.categoria.refresh_from_db()
        self.assertFalse(self.categoria.estado)


class HerramientaTests(HerramientasBaseTestCase):
    def crear(self, **extra):
        resp = self.client.post('/api/herramientas/herramientas/', self.payload(**extra), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data

    def test_codigo_autogenerado_secuencial(self):
        self.assertEqual(self.crear()['codigo'], 'HER-0001')
        self.assertEqual(self.crear(nombre='Sierra')['codigo'], 'HER-0002')

    def test_crear_registra_historial_y_creador(self):
        data = self.crear()
        herramienta = Herramienta.objects.get(pk=data['id'])
        self.assertEqual(herramienta.creado_por, self.admin)
        self.assertEqual(herramienta.estado_operativo, 'DISPONIBLE')
        self.assertTrue(herramienta.historial.filter(accion='CREACION').exists())

    def test_estado_operativo_y_horas_no_se_editan_por_crear_ni_patch(self):
        data = self.crear(estado_operativo='PERDIDA', horas_uso_acumuladas='99')
        self.assertEqual(data['estado_operativo'], 'DISPONIBLE')
        self.assertEqual(float(data['horas_uso_acumuladas']), 0)
        resp = self.client.patch(
            f"/api/herramientas/herramientas/{data['id']}/",
            {'estado_operativo': 'PERDIDA', 'nombre': 'Nueva'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['estado_operativo'], 'DISPONIBLE')

    def test_almacen_debe_pertenecer_a_la_sucursal(self):
        resp = self.client.post(
            '/api/herramientas/herramientas/',
            self.payload(sucursal=self.otra_sucursal.id, almacen=self.almacen.id), format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('almacen', resp.data['errores'])

    def test_garantia_no_anterior_a_compra_y_costo_no_negativo(self):
        resp = self.client.post(
            '/api/herramientas/herramientas/',
            self.payload(fecha_compra='2026-05-10', garantia_hasta='2026-01-01'), format='json',
        )
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(
            '/api/herramientas/herramientas/', self.payload(costo_adquisicion='-5'), format='json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_categoria_inactiva_rechazada(self):
        self.categoria.estado = False
        self.categoria.save()
        resp = self.client.post('/api/herramientas/herramientas/', self.payload(), format='json')
        self.assertEqual(resp.status_code, 400)

    def test_listado_paginado_filtros_y_busqueda(self):
        self.crear(nombre='Amoladora grande')
        self.crear(nombre='Soldadora inverter', marca='Lincoln')
        resp = self.client.get('/api/herramientas/herramientas/')
        self.assertEqual(resp.data['count'], 2)
        resp = self.client.get('/api/herramientas/herramientas/', {'search': 'Lincoln'})
        self.assertEqual(resp.data['count'], 1)
        resp = self.client.get('/api/herramientas/herramientas/', {'sucursal': self.otra_sucursal.id})
        self.assertEqual(resp.data['count'], 0)
        resp = self.client.get('/api/herramientas/herramientas/', {'estado_operativo': 'DISPONIBLE', 'page_size': 1})
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['count'], 2)

    def test_eliminar_es_soft_delete_con_historial(self):
        data = self.crear()
        resp = self.client.delete(f"/api/herramientas/herramientas/{data['id']}/")
        self.assertEqual(resp.status_code, 204)
        herramienta = Herramienta.objects.get(pk=data['id'])
        self.assertFalse(herramienta.estado)
        self.assertTrue(herramienta.historial.filter(accion='BAJA').exists())
        resp = self.client.get('/api/herramientas/herramientas/')
        self.assertEqual(resp.data['count'], 0)

    def test_no_elimina_herramienta_asignada(self):
        data = self.crear()
        Herramienta.objects.filter(pk=data['id']).update(estado_operativo='ASIGNADA')
        resp = self.client.delete(f"/api/herramientas/herramientas/{data['id']}/")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Herramienta.objects.get(pk=data['id']).estado)

    def test_historial_endpoint(self):
        data = self.crear()
        resp = self.client.get(f"/api/herramientas/herramientas/{data['id']}/historial/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data[0]['accion'], 'CREACION')


class CambiarEstadoTests(HerramientasBaseTestCase):
    def setUp(self):
        super().setUp()
        resp = self.client.post('/api/herramientas/herramientas/', self.payload(), format='json')
        self.pk = resp.data['id']
        self.url = f'/api/herramientas/herramientas/{self.pk}/cambiar-estado/'

    def test_cambio_valido_registra_historial(self):
        resp = self.client.post(
            self.url, {'estado_operativo': 'EN_REPARACION', 'motivo': 'Falla de motor'}, format='json'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Herramienta.objects.get(pk=self.pk).estado_operativo, 'EN_REPARACION')
        self.assertTrue(
            HistorialHerramienta.objects.filter(herramienta_id=self.pk, accion='CAMBIO_ESTADO').exists()
        )

    def test_motivo_obligatorio(self):
        resp = self.client.post(self.url, {'estado_operativo': 'EN_REPARACION', 'motivo': '  '}, format='json')
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(self.url, {'estado_operativo': 'EN_REPARACION'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_no_se_puede_fijar_asignada_manualmente(self):
        resp = self.client.post(self.url, {'estado_operativo': 'ASIGNADA', 'motivo': 'x'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_herramienta_asignada_o_de_baja_es_inmutable(self):
        Herramienta.objects.filter(pk=self.pk).update(estado_operativo='ASIGNADA')
        resp = self.client.post(self.url, {'estado_operativo': 'DISPONIBLE', 'motivo': 'x'}, format='json')
        self.assertEqual(resp.status_code, 409)
        Herramienta.objects.filter(pk=self.pk).update(estado_operativo='DADA_DE_BAJA')
        resp = self.client.post(self.url, {'estado_operativo': 'DISPONIBLE', 'motivo': 'x'}, format='json')
        self.assertEqual(resp.status_code, 409)

    def test_mismo_estado_rechazado(self):
        resp = self.client.post(self.url, {'estado_operativo': 'DISPONIBLE', 'motivo': 'x'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_estado_inexistente_rechazado(self):
        resp = self.client.post(self.url, {'estado_operativo': 'XYZ', 'motivo': 'x'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_herramienta_inexistente_404(self):
        resp = self.client.post(
            '/api/herramientas/herramientas/99999/cambiar-estado/',
            {'estado_operativo': 'EN_REPARACION', 'motivo': 'x'}, format='json',
        )
        self.assertEqual(resp.status_code, 404)


class PermisosHerramientasTests(HerramientasBaseTestCase):
    def setUp(self):
        super().setUp()
        self.sin_permisos = Usuario.objects.create_user(
            username='sin_permisos_herr', email='sin_permisos_herr@example.com',
            nombres='Sin', apellidos='Permisos', password='x', estado='activo',
        )
        self.client_sin = APIClient()
        self.client_sin.force_authenticate(user=self.sin_permisos)
        resp = self.client.post('/api/herramientas/herramientas/', self.payload(), format='json')
        self.pk = resp.data['id']

    def test_sin_permisos_recibe_403(self):
        base = '/api/herramientas/herramientas/'
        self.assertEqual(self.client_sin.get(base).status_code, 403)
        self.assertEqual(self.client_sin.post(base, self.payload(), format='json').status_code, 403)
        self.assertEqual(self.client_sin.patch(f'{base}{self.pk}/', {'nombre': 'x'}, format='json').status_code, 403)
        self.assertEqual(self.client_sin.delete(f'{base}{self.pk}/').status_code, 403)
        self.assertEqual(self.client_sin.get(f'{base}{self.pk}/historial/').status_code, 403)
        self.assertEqual(
            self.client_sin.post(
                f'{base}{self.pk}/cambiar-estado/',
                {'estado_operativo': 'EN_REPARACION', 'motivo': 'x'}, format='json',
            ).status_code, 403,
        )
        self.assertEqual(self.client_sin.get('/api/herramientas/categorias/').status_code, 403)
        self.assertEqual(
            self.client_sin.post('/api/herramientas/categorias/', {'nombre': 'X'}, format='json').status_code, 403
        )

    def test_sin_autenticar_recibe_401(self):
        self.assertEqual(APIClient().get('/api/herramientas/herramientas/').status_code, 401)

    def test_editar_sin_permiso_de_baja_no_puede_dar_de_baja(self):
        from apps.seguridad.models import Modulo, Permiso, Rol, RolPermiso, UsuarioRol

        modulo = Modulo.objects.create(codigo='HERR_TEST', nombre='Herr test')
        rol = Rol.objects.create(codigo='ROL_HERR_TEST', nombre='Rol herr test')
        for codigo in ('HERRAMIENTAS.INVENTARIO.EDITAR',):
            permiso = Permiso.objects.create(id_modulo=modulo, codigo=codigo, nombre=codigo, accion='EDITAR')
            RolPermiso.objects.create(id_rol=rol, id_permiso=permiso, alcance='GLOBAL')
        UsuarioRol.objects.create(id_usuario=self.sin_permisos, id_rol=rol)

        url = f'/api/herramientas/herramientas/{self.pk}/cambiar-estado/'
        resp = self.client_sin.post(url, {'estado_operativo': 'EN_REPARACION', 'motivo': 'x'}, format='json')
        self.assertEqual(resp.status_code, 200)
        resp = self.client_sin.post(url, {'estado_operativo': 'PERDIDA', 'motivo': 'x'}, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Herramienta.objects.get(pk=self.pk).estado_operativo, 'EN_REPARACION')
