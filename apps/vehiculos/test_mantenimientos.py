from datetime import date, timedelta

from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario
from .mantenimientos import calcular_proximo_mantenimiento
from .models import MantenimientoVehiculo, Vehiculo, VehiculoQR


class CalculoMantenimientoTests(SimpleTestCase):
    def registro(self, **kwargs):
        return MantenimientoVehiculo(
            tipo='ACEITE', fecha_realizado=date(2024, 1, 31),
            kilometraje_realizado=20000, **kwargs,
        )

    def test_mes_calendario_y_vencimiento_por_fecha(self):
        resumen = calcular_proximo_mantenimiento(self.registro(intervalo_meses=1), 20000, date(2024, 2, 29))
        self.assertEqual(resumen['proxima_fecha'], date(2024, 2, 29))
        self.assertTrue(resumen['vencido_fecha'])

    def test_limite_km_exactamente_y_fecha_no_vencida(self):
        resumen = calcular_proximo_mantenimiento(self.registro(intervalo_km=10000, intervalo_meses=6), 30000, date(2024, 2, 1))
        self.assertEqual(resumen['estado'], 'VENCIDO')
        self.assertTrue(resumen['vencido_km'])
        self.assertFalse(resumen['vencido_fecha'])

    def test_lectura_inferior_o_ausente_no_inventa_km_restantes(self):
        for km in (None, 0, 19999):
            resumen = calcular_proximo_mantenimiento(self.registro(intervalo_km=10000), km, date(2024, 2, 1))
            self.assertEqual(resumen['estado'], 'VERIFICAR_KILOMETRAJE')
            self.assertIsNone(resumen['km_restantes'])

    def test_kilometraje_cero_es_valido(self):
        registro = self.registro(intervalo_km=10000)
        registro.kilometraje_realizado = 0
        resumen = calcular_proximo_mantenimiento(registro, 0, date(2024, 2, 1))
        self.assertEqual(resumen['km_restantes'], 10000)


class MantenimientoVehiculoAPITests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_superuser(
            username='admin_mantenimiento', email='mantenimiento@example.com',
            nombres='Admin', apellidos='Mantenimiento', password='x',
        )
        self.vehiculo = Vehiculo.objects.create(placa='MANT001', marca='Toyota', modelo='Yaris', kilometraje_actual=30000)
        self.otro = Vehiculo.objects.create(placa='MANT002', marca='Kia', modelo='Rio')
        self.qr = VehiculoQR.objects.create(vehiculo=self.vehiculo)
        self.client = APIClient()
        self.client.force_authenticate(self.usuario)
        self.url = f'/api/vehiculos/{self.vehiculo.pk}/mantenimientos/'
        self.payload = {
            'tipo': 'ACEITE', 'fecha_realizado': str(timezone.localdate()),
            'kilometraje_realizado': 20000, 'intervalo_km': 10000, 'intervalo_meses': 6,
        }

    def test_registrar_y_consultar_qr_sin_cambiar_vehiculo(self):
        resp = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.vehiculo.refresh_from_db()
        self.assertEqual(self.vehiculo.kilometraje_actual, 30000)
        self.client.force_authenticate(None)
        resp = self.client.get(f'/api/vehiculos/public/qr/{self.qr.token_publico}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNone(resp.data['orden'])
        self.assertEqual(resp.data['mantenimientos'][0]['estado'], 'VENCIDO')
        self.assertNotIn('creado_por', str(resp.data))

    def test_nuevo_ingreso_actualiza_distancia_sin_reiniciar_mantenimiento(self):
        self.client.post(self.url, self.payload, format='json')
        self.vehiculo.kilometraje_actual = 25000
        self.vehiculo.save(update_fields=['kilometraje_actual'])
        resp = self.client.get(self.url)
        self.assertEqual(resp.data['proximos'][0]['km_restantes'], 5000)
        self.vehiculo.kilometraje_actual = 30000
        self.vehiculo.save(update_fields=['kilometraje_actual'])
        resp = self.client.get(self.url)
        self.assertEqual(resp.data['proximos'][0]['estado'], 'VENCIDO')
        self.assertEqual(resp.data['proximos'][0]['kilometraje_realizado'], 20000)

    def test_ultimo_por_fecha_y_anulacion_recupera_anterior(self):
        anterior = self.client.post(self.url, self.payload, format='json').data
        nuevo_payload = {**self.payload, 'kilometraje_realizado': 30000}
        ultimo = self.client.post(self.url, nuevo_payload, format='json').data
        resp = self.client.get(self.url)
        self.assertEqual(resp.data['proximos'][0]['proximo_km'], 40000)
        respuesta = self.client.delete(f'{self.url}{ultimo["id"]}/')
        self.assertEqual(respuesta.status_code, 204)
        resp = self.client.get(self.url)
        self.assertEqual(resp.data['proximos'][0]['proximo_km'], 30000)
        self.assertTrue(MantenimientoVehiculo.objects.filter(pk=anterior['id'], activo=True).exists())
        self.assertTrue(MantenimientoVehiculo.objects.filter(pk=ultimo['id'], activo=False).exists())

    def test_invalidos_son_rechazados(self):
        for overrides in (
            {'intervalo_km': None, 'intervalo_meses': None},
            {'intervalo_km': 0}, {'kilometraje_realizado': -1},
            {'fecha_realizado': str(timezone.localdate() + timedelta(days=1))},
        ):
            respuesta = self.client.post(self.url, {**self.payload, **overrides}, format='json')
            self.assertEqual(respuesta.status_code, 400, respuesta.data)

    def test_registro_otro_vehiculo_no_se_puede_editar(self):
        registro = MantenimientoVehiculo.objects.create(vehiculo=self.otro, tipo='ACEITE', fecha_realizado=timezone.localdate(), kilometraje_realizado=0, intervalo_km=5000)
        respuesta = self.client.patch(f'{self.url}{registro.id}/', {'intervalo_km': 1}, format='json')
        self.assertEqual(respuesta.status_code, 404)

    def test_edicion_valida_y_edicion_sin_intervalos(self):
        registro = self.client.post(self.url, self.payload, format='json').data
        url = f'{self.url}{registro["id"]}/'
        self.assertEqual(self.client.patch(url, {'intervalo_km': 5000}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(url, {'intervalo_km': None, 'intervalo_meses': None}, format='json').status_code, 400)

    def test_escritura_anonima_y_sin_permisos_denegada(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(self.url, self.payload, format='json').status_code, (401, 403))
        usuario = Usuario.objects.create_user(username='sin_permiso_mantenimiento', email='sin_permiso_mantenimiento@example.com', nombres='Usuario', apellidos='Prueba', password='x', estado='activo')
        self.client.force_authenticate(usuario)
        self.assertEqual(self.client.post(self.url, self.payload, format='json').status_code, 403)
