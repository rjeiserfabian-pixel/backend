from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.avisos.models import AvisoCliente
from apps.avisos.servicios import generar_avisos, normalizar_telefono, whatsapp_url
from apps.clientes.models import Cliente
from apps.documentos.models import Documento
from apps.inventario.models import Sucursal
from apps.seguridad.models import Usuario
from apps.taller.models import Cita, OrdenTrabajo
from apps.vehiculos.models import MantenimientoVehiculo, Vehiculo

URL = '/api/avisos/'


class NormalizarTelefonoTests(TestCase):
    def test_formatos_validos_de_peru(self):
        self.assertEqual(normalizar_telefono('987 654 321'), '51987654321')
        self.assertEqual(normalizar_telefono('+51 987-654-321'), '51987654321')
        self.assertEqual(normalizar_telefono('51987654321'), '51987654321')

    def test_numeros_invalidos_devuelven_vacio(self):
        for invalido in ('', None, '12345', '014567890', '88765432'):
            self.assertEqual(normalizar_telefono(invalido), '', invalido)

    def test_whatsapp_url_codifica_el_mensaje_y_exige_telefono(self):
        self.assertEqual(whatsapp_url('51987654321', 'Hola ñ & más'), 'https://wa.me/51987654321?text=Hola%20%C3%B1%20%26%20m%C3%A1s')
        self.assertIsNone(whatsapp_url('', 'Hola'))


class GenerarAvisosTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_avisos_test', email='admin_avisos_test@example.com',
            nombres='Admin', apellidos='AvisosTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.hoy = timezone.localdate()
        self.sucursal = Sucursal.objects.create(nombre='Sucursal Avisos Test')
        self.cliente = Cliente.objects.create(dni='71000001', nombres='Carlos', apellidos='Pérez', telefono='987654321')
        self.vehiculo = Vehiculo.objects.create(placa='AVS-001', marca='Toyota', modelo='Yaris', kilometraje_actual=10000)
        self.vehiculo.clientes.add(self.cliente)

    def _aviso(self, tipo):
        return AvisoCliente.objects.filter(tipo=tipo)

    def test_recordatorio_de_cita_de_manana_y_se_descarta_si_se_cancela(self):
        inicio = timezone.now() + timedelta(days=1)
        cita = Cita.objects.create(
            numero='C-1', cliente=self.cliente, vehiculo=self.vehiculo, sucursal=self.sucursal,
            fecha_inicio=inicio, fecha_fin=inicio + timedelta(hours=1), estado=Cita.Estado.CONFIRMADA,
        )
        generar_avisos()
        aviso = self._aviso('CITA').get()
        self.assertEqual(aviso.telefono, '51987654321')
        self.assertIn('AVS-001', aviso.mensaje)
        self.assertIn('Carlos', aviso.mensaje)

        cita.estado = Cita.Estado.CANCELADA
        cita.save()
        generar_avisos()
        aviso.refresh_from_db()
        self.assertEqual(aviso.estado, AvisoCliente.Estado.DESCARTADO)
        self.assertTrue(aviso.descartado_automatico)

    def test_generar_es_idempotente_y_no_duplica(self):
        inicio = timezone.now() + timedelta(days=1)
        Cita.objects.create(
            numero='C-2', cliente=self.cliente, vehiculo=self.vehiculo, sucursal=self.sucursal,
            fecha_inicio=inicio, fecha_fin=inicio + timedelta(hours=1),
        )
        self.assertEqual(generar_avisos()['creados'], 1)
        self.assertEqual(generar_avisos()['creados'], 0)
        self.assertEqual(AvisoCliente.objects.count(), 1)

    def test_cita_lejana_no_genera_aviso(self):
        inicio = timezone.now() + timedelta(days=10)
        Cita.objects.create(
            numero='C-3', cliente=self.cliente, vehiculo=self.vehiculo, sucursal=self.sucursal,
            fecha_inicio=inicio, fecha_fin=inicio + timedelta(hours=1),
        )
        generar_avisos()
        self.assertFalse(self._aviso('CITA').exists())

    def test_vehiculo_listo_solo_mientras_esta_finalizado(self):
        orden = OrdenTrabajo.objects.create(
            numero='OT-T1', cliente=self.cliente, vehiculo=self.vehiculo, recepcionista=self.admin,
            estado=OrdenTrabajo.Estado.FINALIZADO, fecha_finalizacion=timezone.now(),
        )
        generar_avisos()
        self.assertEqual(self._aviso('VEHICULO_LISTO').get().estado, 'PENDIENTE')

        orden.estado = OrdenTrabajo.Estado.FACTURADO
        orden.save()
        generar_avisos()
        self.assertEqual(self._aviso('VEHICULO_LISTO').get().estado, 'DESCARTADO')

    def test_mantenimiento_por_fecha_y_por_kilometraje(self):
        MantenimientoVehiculo.objects.create(
            vehiculo=self.vehiculo, tipo='ACEITE', fecha_realizado=self.hoy - timedelta(days=170),
            kilometraje_realizado=5000, intervalo_meses=6, creado_por=self.admin,
        )
        MantenimientoVehiculo.objects.create(
            vehiculo=self.vehiculo, tipo='FILTRO_AIRE', fecha_realizado=self.hoy,
            kilometraje_realizado=6000, intervalo_km=4300, creado_por=self.admin,  # próximo a 10300 km
        )
        MantenimientoVehiculo.objects.create(
            vehiculo=self.vehiculo, tipo='FILTRO_ACEITE', fecha_realizado=self.hoy,
            kilometraje_realizado=9000, intervalo_km=10000, creado_por=self.admin,  # faltan 9000 km
        )
        generar_avisos()
        self.assertEqual(self._aviso('MANTENIMIENTO').count(), 2)

    def test_documento_por_vencer_avisa_al_cliente_del_vehiculo(self):
        Documento.objects.create(
            vehiculo=self.vehiculo, tipo='SOAT', fecha_vencimiento=self.hoy + timedelta(days=10),
        )
        Documento.objects.create(  # muy lejano: no avisa
            vehiculo=self.vehiculo, tipo='REVISION_TECNICA', fecha_vencimiento=self.hoy + timedelta(days=200),
        )
        generar_avisos()
        aviso = self._aviso('DOCUMENTO').get()
        self.assertIn('soat', aviso.mensaje.lower())
        self.assertEqual(aviso.cliente, self.cliente)

    def test_cliente_sin_telefono_igual_genera_aviso_marcado_sin_numero(self):
        self.cliente.telefono = ''
        self.cliente.save()
        Documento.objects.create(vehiculo=self.vehiculo, tipo='SOAT', fecha_vencimiento=self.hoy + timedelta(days=3))
        generar_avisos()
        aviso = self._aviso('DOCUMENTO').get()
        self.assertEqual(aviso.telefono, '')

        # Al registrar el teléfono, el aviso pendiente se actualiza solo.
        self.cliente.telefono = '912345678'
        self.cliente.save()
        generar_avisos()
        aviso.refresh_from_db()
        self.assertEqual(aviso.telefono, '51912345678')

    # --- API ---
    def test_api_lista_pendientes_marca_enviado_y_reabre(self):
        Documento.objects.create(vehiculo=self.vehiculo, tipo='SOAT', fecha_vencimiento=self.hoy + timedelta(days=3))
        self.assertEqual(self.client.post(URL + 'generar/').data['creados'], 1)

        lista = self.client.get(URL).data
        self.assertEqual(lista['count'], 1)
        fila = lista['results'][0]
        self.assertTrue(fila['whatsapp_url'].startswith('https://wa.me/51987654321?text='))

        enviado = self.client.post(f"{URL}{fila['id']}/marcar-enviado/")
        self.assertEqual(enviado.data['estado'], 'ENVIADO')
        self.assertEqual(self.client.get(URL).data['count'], 0)
        self.assertEqual(self.client.get(URL, {'estado': 'ENVIADO'}).data['count'], 1)

        reabierto = self.client.post(f"{URL}{fila['id']}/reabrir/")
        self.assertEqual(reabierto.data['estado'], 'PENDIENTE')

    def test_enviado_no_se_recrea_al_generar_de_nuevo(self):
        Documento.objects.create(vehiculo=self.vehiculo, tipo='SOAT', fecha_vencimiento=self.hoy + timedelta(days=3))
        generar_avisos()
        aviso = AvisoCliente.objects.get()
        aviso.estado = AvisoCliente.Estado.ENVIADO
        aviso.save()
        self.assertEqual(generar_avisos()['creados'], 0)
        self.assertEqual(AvisoCliente.objects.get().estado, 'ENVIADO')

    def test_usuario_sin_permiso_recibe_403(self):
        sin_permiso = Usuario.objects.create_user(
            username='sin_permiso_avisos', email='sin_permiso_avisos@example.com',
            nombres='Sin', apellidos='Permiso', password='x',
        )
        api = APIClient()
        api.force_authenticate(user=sin_permiso)
        self.assertEqual(api.get(URL).status_code, 403)
        self.assertEqual(api.post(URL + 'generar/').status_code, 403)

    def test_cuota_vencida_genera_aviso_de_cobranza(self):
        from apps.ventas.models import CuentaPorCobrar, CuotaCredito, Venta
        venta = Venta.objects.create(cliente=self.cliente, sucursal=self.sucursal, estado=Venta.Estado.AL_CREDITO, total=Decimal('100.00'))
        cuenta = CuentaPorCobrar.objects.create(
            venta=venta, codigo_credito='CR-AV-1', frecuencia_pago='MENSUAL',
            monto_financiado=Decimal('100.00'), saldo_pendiente=Decimal('100.00'),
        )
        CuotaCredito.objects.create(
            cuenta_cobrar=cuenta, numero_cuota=1, monto=Decimal('100.00'), saldo_pendiente=Decimal('100.00'),
            fecha_vencimiento=self.hoy - timedelta(days=2),
        )
        generar_avisos()
        aviso = self._aviso('CUOTA').get()
        self.assertIn('venció', aviso.mensaje)
        self.assertIn('100.00', aviso.mensaje)
