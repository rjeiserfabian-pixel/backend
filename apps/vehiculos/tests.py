from django.test import TestCase
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario
from apps.clientes.models import Cliente
from apps.vehiculos.models import Vehiculo, VehiculoQR
from apps.taller.models import OrdenTrabajo, OrdenServicio


class PermisosVehiculosTests(TestCase):
    """Cubre el bug real: ningún ViewSet de Vehículos exigía permisos (bare IsAuthenticated)."""

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_permisos_vehiculos_test', email='admin_permisos_vehiculos_test@example.com',
            nombres='Admin', apellidos='PermisosTest', password='x',
        )
        self.sin_permisos = Usuario.objects.create_user(
            username='sin_permisos_vehiculos_test', email='sin_permisos_vehiculos_test@example.com',
            nombres='Sin', apellidos='Permisos', password='x', estado='activo',
        )

    def test_admin_puede_listar_vehiculos(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        resp = client.get('/api/vehiculos/')
        self.assertEqual(resp.status_code, 200)

    def test_usuario_sin_permisos_no_puede_listar_vehiculos(self):
        client = APIClient()
        client.force_authenticate(user=self.sin_permisos)
        resp = client.get('/api/vehiculos/')
        self.assertEqual(resp.status_code, 403)


class VincularClienteTests(TestCase):
    """
    Cubre el bug real del flujo de Kiosco: un vehículo puede llegar con
    distintos clientes a lo largo del tiempo (relación muchos-a-muchos).
    Vincular un cliente nuevo NUNCA debe desvincular a los que ya estaban.
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_vincular_test', email='admin_vincular_test@example.com',
            nombres='Admin', apellidos='VincularTest', password='x',
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

        self.vehiculo = Vehiculo.objects.create(placa='VIN-001', marca='Toyota', modelo='Yaris')
        self.cliente1 = Cliente.objects.create(dni='30000001', nombres='Cliente', apellidos='Uno')
        self.cliente2 = Cliente.objects.create(dni='30000002', nombres='Cliente', apellidos='Dos')
        self.vehiculo.clientes.add(self.cliente1)

    def test_vincular_cliente_nuevo_no_desvincula_al_anterior(self):
        resp = self.client_api.post(
            f'/api/vehiculos/{self.vehiculo.id}/vincular-cliente/', {'cliente_id': self.cliente2.id}, format='json'
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        self.vehiculo.refresh_from_db()
        ids_vinculados = set(self.vehiculo.clientes.values_list('id', flat=True))
        self.assertEqual(ids_vinculados, {self.cliente1.id, self.cliente2.id})

    def test_vincular_el_mismo_cliente_dos_veces_no_duplica(self):
        self.client_api.post(
            f'/api/vehiculos/{self.vehiculo.id}/vincular-cliente/', {'cliente_id': self.cliente1.id}, format='json'
        )
        self.vehiculo.refresh_from_db()
        self.assertEqual(self.vehiculo.clientes.count(), 1)

    def test_vincular_sin_cliente_id_se_rechaza(self):
        resp = self.client_api.post(f'/api/vehiculos/{self.vehiculo.id}/vincular-cliente/', {}, format='json')
        self.assertEqual(resp.status_code, 400)


class ConsultaVehiculoQRPublicaTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_superuser(
            username='admin_qr_publico_test',
            email='admin_qr_publico_test@example.com',
            nombres='Admin',
            apellidos='QR',
            password='x',
        )
        self.cliente = Cliente.objects.create(
            dni='39999999',
            nombres='Nombre Privado',
            apellidos='Cliente',
            telefono='999999999',
        )
        self.vehiculo = Vehiculo.objects.create(
            placa='QR-1000',
            marca='Toyota',
            modelo='Corolla',
        )
        self.vehiculo.clientes.add(self.cliente)
        self.codigo_qr = VehiculoQR.objects.create(
            vehiculo=self.vehiculo,
            creado_por=self.usuario,
        )
        self.orden = OrdenTrabajo.objects.create(
            numero='QR-OT-001',
            cliente=self.cliente,
            vehiculo=self.vehiculo,
            recepcionista=self.usuario,
            estado=OrdenTrabajo.Estado.INSPECCION,
            motivo_ingreso='Revision general',
        )
        self.client_api = APIClient()

    def test_qr_activo_devuelve_seguimiento_sin_datos_personales(self):
        resp = self.client_api.get(f'/api/vehiculos/public/qr/{self.codigo_qr.token_publico}/')

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['vehiculo']['placa'], self.vehiculo.placa)
        self.assertEqual(resp.data['orden']['numero'], self.orden.numero)

        contenido = str(resp.data)
        self.assertNotIn(self.cliente.dni, contenido)
        self.assertNotIn(self.cliente.telefono, contenido)
        self.assertNotIn(self.cliente.nombres, contenido)
        self.assertNotIn('precio', contenido.lower())

    def test_qr_desactivado_no_permite_consulta(self):
        self.codigo_qr.activo = False
        self.codigo_qr.save(update_fields=['activo'])

        resp = self.client_api.get(f'/api/vehiculos/public/qr/{self.codigo_qr.token_publico}/')

        self.assertEqual(resp.status_code, 404)

    def test_token_inexistente_no_permite_consulta(self):
        resp = self.client_api.get('/api/vehiculos/public/qr/token-inexistente/')

        self.assertEqual(resp.status_code, 404)

    def test_historial_incluye_tres_visitas_y_solo_trabajos_autorizados(self):
        for numero in ('QR-OT-002', 'QR-OT-003'):
            orden = OrdenTrabajo.objects.create(
                numero=numero, cliente=self.cliente, vehiculo=self.vehiculo,
                recepcionista=self.usuario, estado=OrdenTrabajo.Estado.FACTURADO,
                kilometraje_ingreso=0,
            )
            OrdenServicio.objects.create(orden=orden, descripcion='Trabajo autorizado', aprobado_cliente=True, completado=True, precio_estimado=123)
            OrdenServicio.objects.create(orden=orden, descripcion='Propuesta privada', aprobado_cliente=False)
        resp = self.client_api.get(f'/api/vehiculos/public/qr/{self.codigo_qr.token_publico}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['historial_ingresos']['count'], 3)
        self.assertEqual(len(resp.data['historial_ingresos']['results']), 3)
        self.assertEqual(resp.data['orden']['numero'], self.orden.numero)
        self.assertTrue(resp.data['orden']['atencion_activa'])
        contenido = str(resp.data)
        for privado in ('Propuesta privada', self.cliente.dni, self.cliente.telefono, self.cliente.nombres, 'precio', 'recepcionista'):
            self.assertNotIn(privado, contenido)

    def test_sin_orden_abierta_se_muestra_ultima_finalizada(self):
        self.orden.estado = OrdenTrabajo.Estado.FACTURADO
        self.orden.save(update_fields=['estado'])
        OrdenTrabajo.objects.create(numero='QR-CANCELADA', vehiculo=self.vehiculo, recepcionista=self.usuario, estado=OrdenTrabajo.Estado.CANCELADO)
        resp = self.client_api.get(f'/api/vehiculos/public/qr/{self.codigo_qr.token_publico}/')
        self.assertEqual(resp.data['orden']['numero'], self.orden.numero)
        self.assertFalse(resp.data['orden']['atencion_activa'])
        self.assertEqual(resp.data['historial_ingresos']['count'], 2)

    def test_contador_real_no_inventa_avance(self):
        OrdenServicio.objects.create(orden=self.orden, descripcion='Hecho', aprobado_cliente=True, completado=True)
        OrdenServicio.objects.create(orden=self.orden, descripcion='Pendiente', aprobado_cliente=True)
        OrdenServicio.objects.create(orden=self.orden, descripcion='No autorizado')
        resp = self.client_api.get(f'/api/vehiculos/public/qr/{self.codigo_qr.token_publico}/')
        self.assertEqual(resp.data['orden']['trabajos_totales'], 2)
        self.assertEqual(resp.data['orden']['trabajos_terminados'], 1)
        self.assertEqual(resp.data['orden']['progreso'], 50)

    def test_historial_paginado_mantiene_la_atencion_destacada(self):
        for numero in range(3):
            OrdenTrabajo.objects.create(numero=f'QR-PAG-{numero}', vehiculo=self.vehiculo, recepcionista=self.usuario, estado=OrdenTrabajo.Estado.FACTURADO)
        url = f'/api/vehiculos/public/qr/{self.codigo_qr.token_publico}/'
        primera = self.client_api.get(url, {'page_size': 2})
        segunda = self.client_api.get(url, {'page_size': 2, 'page': 2})
        self.assertEqual(primera.data['historial_ingresos']['count'], 4)
        self.assertEqual(segunda.data['historial_ingresos']['total_pages'], 2)
        self.assertEqual(segunda.data['historial_ingresos']['page'], 2)
        numeros_a = {item['numero'] for item in primera.data['historial_ingresos']['results']}
        numeros_b = {item['numero'] for item in segunda.data['historial_ingresos']['results']}
        self.assertFalse(numeros_a & numeros_b)
        self.assertEqual(primera.data['orden'], segunda.data['orden'])
        self.assertEqual(self.client_api.get(url, {'page': 'incorrecta'}).status_code, 404)
