from decimal import Decimal
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.clientes.models import Cliente
from apps.inventario.models import Categoria, MarcaRepuesto, Repuesto, Sucursal
from apps.seguridad.auditoria import diferencias, limpiar
from apps.seguridad.models import Auditoria, Permiso, Modulo, Rol, Usuario
from apps.ventas.models import MetodoPago, Venta


def _api_con_token(usuario):
    """Cliente autenticado con un JWT real (como en el sistema): force_authenticate no pasa por el middleware."""
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(usuario).access_token}')
    return api


class AuditoriaBase(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_auditoria_test', email='admin_auditoria_test@example.com',
            nombres='Admin', apellidos='AuditoriaTest', password='x',
        )
        self.api = _api_con_token(self.admin)


class AuditoriaAutomaticaTests(AuditoriaBase):
    def test_una_escritura_exitosa_queda_registrada_con_usuario_y_dato(self):
        resp = self.api.post('/api/ventas/metodos-pago/', {'nombre': 'Plin Test', 'requiere_referencia': True}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

        registro = Auditoria.objects.get(accion='CREAR', modulo='VENTAS')
        self.assertEqual(registro.id_usuario, self.admin)
        self.assertEqual(registro.tabla_afectada, 'metodos-pago')
        self.assertEqual(registro.registro_id, str(resp.data['id']))
        self.assertEqual(registro.datos_nuevos['datos']['nombre'], 'Plin Test')

    def test_editar_y_eliminar_se_registran(self):
        metodo = MetodoPago.objects.create(nombre='Metodo Test')
        self.api.put(f'/api/ventas/metodos-pago/{metodo.id}/', {'nombre': 'Metodo Test 2', 'requiere_referencia': False}, format='json')
        self.api.delete(f'/api/ventas/metodos-pago/{metodo.id}/')
        acciones = set(Auditoria.objects.filter(registro_id=str(metodo.id)).values_list('accion', flat=True))
        self.assertEqual(acciones, {'EDITAR', 'ELIMINAR'})

    def test_las_lecturas_y_los_errores_no_se_registran(self):
        self.api.get('/api/ventas/metodos-pago/')
        self.api.post('/api/ventas/metodos-pago/', {}, format='json')  # falla la validación (400)
        self.assertEqual(Auditoria.objects.count(), 0)

    def test_una_accion_sobre_un_registro_usa_el_nombre_de_la_accion(self):
        cliente = Cliente.objects.create(dni='73000001', nombres='Cli', apellidos='Aud')
        sucursal = Sucursal.objects.create(nombre='Suc Aud')
        venta = Venta.objects.create(cliente=cliente, sucursal=sucursal, estado=Venta.Estado.PRE_VENTA)
        # Sin motivo responde 400 y no se registra; con motivo, la acción es explícita (no duplicada).
        self.api.post(f'/api/ventas/transacciones/{venta.id}/cancelar/', {}, format='json')
        self.assertEqual(Auditoria.objects.count(), 0)
        self.api.post(f'/api/ventas/transacciones/{venta.id}/cancelar/', {'motivo': 'Desistió'}, format='json')
        registros = Auditoria.objects.filter(registro_id=str(venta.id))
        self.assertEqual(registros.count(), 1)  # solo el detallado, sin duplicado automático
        self.assertEqual(registros.get().accion, 'PEDIDO_CANCELADO')
        self.assertEqual(registros.get().datos_nuevos['motivo'], 'Desistió')

    def test_login_exitoso_y_fallido_sin_guardar_la_contrasena(self):
        Usuario.objects.create_user(
            username='cajero_aud', email='cajero_aud@example.com', nombres='Caje', apellidos='Ro', password='ClaveSegura123',
        )
        anonimo = APIClient()
        anonimo.post('/api/seguridad/login/', {'username': 'cajero_aud', 'password': 'incorrecta999'}, format='json')
        anonimo.post('/api/seguridad/login/', {'username': 'cajero_aud', 'password': 'ClaveSegura123'}, format='json')

        self.assertEqual(
            list(Auditoria.objects.order_by('id_auditoria').values_list('accion', flat=True)), ['LOGIN_FALLIDO', 'LOGIN'],
        )
        self.assertEqual(Auditoria.objects.get(accion='LOGIN').id_usuario.username, 'cajero_aud')
        todo = ' '.join(str(r.datos_nuevos) for r in Auditoria.objects.all())
        self.assertNotIn('incorrecta999', todo)
        self.assertNotIn('ClaveSegura123', todo)

    def test_si_falla_la_auditoria_la_operacion_del_usuario_continua(self):
        with mock.patch.object(Auditoria.objects, 'create', side_effect=RuntimeError('base caída')):
            resp = self.api.post('/api/ventas/metodos-pago/', {'nombre': 'Resiste', 'requiere_referencia': False}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(MetodoPago.objects.filter(nombre='Resiste').exists())


class AuditoriaDetalladaTests(AuditoriaBase):
    def test_cambio_de_precio_guarda_antes_y_despues_solo_de_lo_que_cambio(self):
        repuesto = Repuesto.objects.create(
            codigo='AUD-1', nombre='Filtro Aud', categoria=Categoria.objects.create(nombre='C Aud'),
            marca=MarcaRepuesto.objects.create(nombre='M Aud'),
            precio_compra=Decimal('10.00'), precio_por_mayor=Decimal('12.00'),
            precio_cash=Decimal('14.00'), precio_lista=Decimal('15.00'),
        )
        resp = self.api.patch(f'/api/inventario/repuestos/{repuesto.id}/', {'precio_lista': '18.50'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        registro = Auditoria.objects.get(accion='CAMBIO_PRECIO')
        self.assertEqual(registro.datos_anteriores['precio_lista'], '15.00')
        self.assertEqual(registro.datos_nuevos['precio_lista'], '18.50')
        self.assertNotIn('precio_compra', registro.datos_nuevos)  # no cambió: no aparece
        self.assertEqual(Auditoria.objects.filter(registro_id=str(repuesto.id)).count(), 1)

    def test_editar_sin_cambiar_precios_no_genera_cambio_de_precio(self):
        repuesto = Repuesto.objects.create(
            codigo='AUD-2', nombre='Aceite Aud', categoria=Categoria.objects.create(nombre='C Aud2'),
            marca=MarcaRepuesto.objects.create(nombre='M Aud2'),
            precio_compra=Decimal('10.00'), precio_por_mayor=Decimal('12.00'),
            precio_cash=Decimal('14.00'), precio_lista=Decimal('15.00'),
        )
        self.api.patch(f'/api/inventario/repuestos/{repuesto.id}/', {'nombre': 'Aceite Aud nuevo', 'precio_lista': '15'}, format='json')
        self.assertFalse(Auditoria.objects.filter(accion='CAMBIO_PRECIO').exists())

    def test_cambio_de_permisos_de_un_rol_lista_agregados_y_quitados(self):
        modulo = Modulo.objects.create(codigo='MOD_AUD', nombre='Mod Aud', orden=1)
        p1 = Permiso.objects.create(id_modulo=modulo, codigo='MOD_AUD.UNO', nombre='Uno', accion='VER')
        p2 = Permiso.objects.create(id_modulo=modulo, codigo='MOD_AUD.DOS', nombre='Dos', accion='VER')
        rol = Rol.objects.create(codigo='ROL_AUD', nombre='Rol Aud')
        url = f'/api/seguridad/roles/{rol.id_rol}/permisos/'
        self.api.post(url, {'permisos': [{'id_permiso': p1.id_permiso, 'alcance': 'GLOBAL'}]}, format='json')
        self.api.post(url, {'permisos': [{'id_permiso': p2.id_permiso, 'alcance': 'GLOBAL'}]}, format='json')

        segundo = Auditoria.objects.filter(accion='CAMBIO_PERMISOS_ROL').order_by('-id_auditoria').first()
        self.assertEqual(segundo.datos_anteriores['quitados'], ['MOD_AUD.UNO (GLOBAL)'])
        self.assertEqual(segundo.datos_nuevos['agregados'], ['MOD_AUD.DOS (GLOBAL)'])

    def test_limpiar_oculta_secretos_y_diferencias_compara_numeros_por_valor(self):
        limpio = limpiar({'usuario': 'ana', 'password': 'abc', 'datos': {'api_token': 'xyz', 'nota': 'ok'}})
        self.assertEqual(limpio['password'], '***')
        self.assertEqual(limpio['datos']['api_token'], '***')
        self.assertEqual(limpio['datos']['nota'], 'ok')
        antes, despues = diferencias({'p': Decimal('10.00'), 'q': 1}, {'p': Decimal('10'), 'q': 2})
        self.assertEqual((antes, despues), ({'q': 1}, {'q': 2}))


class AuditoriaConsultaTests(AuditoriaBase):
    def test_lista_filtra_por_modulo_y_expone_opciones(self):
        self.api.post('/api/ventas/metodos-pago/', {'nombre': 'Consulta 1', 'requiere_referencia': False}, format='json')
        lista = self.api.get('/api/seguridad/auditoria/', {'modulo': 'VENTAS'}).data
        self.assertEqual(lista['count'], 1)
        self.assertEqual(lista['results'][0]['usuario_username'], 'admin_auditoria_test')
        self.assertEqual(self.api.get('/api/seguridad/auditoria/', {'modulo': 'OTRO'}).data['count'], 0)
        opciones = self.api.get('/api/seguridad/auditoria/opciones/').data
        self.assertIn('VENTAS', opciones['modulos'])

    def test_es_de_solo_lectura_y_exige_permiso(self):
        self.assertEqual(self.api.post('/api/seguridad/auditoria/', {}, format='json').status_code, 405)
        sin_permiso = Usuario.objects.create_user(
            username='sin_permiso_aud', email='sin_permiso_aud@example.com', nombres='Sin', apellidos='Permiso', password='x',
        )
        self.assertEqual(_api_con_token(sin_permiso).get('/api/seguridad/auditoria/').status_code, 403)
