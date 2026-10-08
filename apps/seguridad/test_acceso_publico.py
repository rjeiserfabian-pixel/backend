from django.test import TestCase
from rest_framework.test import APIClient

from apps.seguridad.acceso_publico import ConfiguracionAccesoPublicoSerializer
from apps.seguridad.models import ConfiguracionAccesoPublico, Usuario
from apps.vehiculos.models import Vehiculo, VehiculoQR


class ConfiguracionAccesoPublicoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_superuser(
            username='admin_acceso_publico', email='publico@example.com',
            nombres='Admin', apellidos='Publico', password='x',
        )
        cls.usuario = Usuario.objects.create_user(
            username='sin_acceso_publico', email='sin_publico@example.com',
            nombres='Sin', apellidos='Permiso', password='x', estado='activo',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='PUB123', marca='KIA', modelo='Sportage')
        cls.qr = VehiculoQR.objects.create(vehiculo=cls.vehiculo, creado_por=cls.admin)

    def setUp(self):
        self.client = APIClient()
        self.url = '/api/seguridad/acceso-publico/'

    def test_configuracion_no_es_publica(self):
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
        self.assertIn(self.client.put(self.url, {'url_base': 'https://taller.example.com'}).status_code, (401, 403))
        self.assertEqual(ConfiguracionAccesoPublico.objects.count(), 0)

    def test_usuario_sin_permiso_no_puede_leer_ni_modificar(self):
        self.client.force_authenticate(user=self.usuario)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.put(self.url, {'url_base': 'https://taller.example.com'}).status_code, 403)

    def test_guardar_y_actualizar_no_modifica_empresa_ni_qr(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get(self.url).data['url_base'], '')
        for url in ('https://taller.example.com/', 'https://nuevo.example.com'):
            response = self.client.put(self.url, {'url_base': url}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['url_base'], url.rstrip('/'))
        self.assertEqual(ConfiguracionAccesoPublico.objects.count(), 1)
        response = self.client.get(f'/api/vehiculos/{self.vehiculo.pk}/qr/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url_base_publica'], 'https://nuevo.example.com')
        self.assertEqual(response.data['qr']['token_publico'], self.qr.token_publico)

    def test_sin_configurar_se_conserva_el_qr_local(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f'/api/vehiculos/{self.vehiculo.pk}/qr/')
        self.assertEqual(response.data['url_base_publica'], '')
        self.assertEqual(response.data['qr']['ruta_publica'], f'/historial-vehiculo/{self.qr.token_publico}')

    def test_rechaza_direcciones_locales_credenciales_y_rutas(self):
        for url in (
            'http://taller.example.com', 'https://localhost', 'https://127.0.0.1',
            'https://192.168.1.10', 'https://10.0.0.1', 'https://[::1]',
            'https://usuario:clave@taller.example.com', 'https://taller.example.com/ruta',
            'https://taller.example.com?x=1', 'https://taller.example.com#qr',
        ):
            with self.subTest(url=url):
                serializer = ConfiguracionAccesoPublicoSerializer(data={'url_base': url})
                self.assertFalse(serializer.is_valid())

    def test_permite_desactivar_url_para_volver_a_enlaces_locales(self):
        ConfiguracionAccesoPublico.objects.create(pk=1, url_base='https://taller.example.com')
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.put(self.url, {'url_base': ''}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url).data['url_base'], '')
