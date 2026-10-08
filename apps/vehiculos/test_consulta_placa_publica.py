from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.vehiculos.models import Vehiculo


class ConsultaPlacaPublicaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.url = '/api/vehiculos/public/consultar-placa/'
        self.vehiculo = Vehiculo.objects.create(placa='PUB123', marca='KIA', modelo='Sportage', kilometraje_actual=12345)
        self.vehiculo.clientes.add(Cliente.objects.create(dni='47777777', nombres='Privado', apellidos='Cliente'))

    def test_consulta_local_no_expone_propietarios_ids_ni_kilometraje(self):
        response = self.client.get(self.url, {'placa': 'pub-123'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data['data']), {'placa', 'marca', 'modelo', 'anio_fabricacion', 'tipo_combustible'})
        self.assertEqual(response.data['data']['placa'], 'PUB123')
        self.assertNotIn('47777777', str(response.data))
        self.assertNotIn('Privado', str(response.data))

    @patch('apps.vehiculos.public_views.ConsultaVehicularService')
    def test_fuente_externa_tambien_filtra_datos_privados(self, servicio):
        servicio.return_value.consultar_placa.return_value = {
            'placa': 'ABC999', 'marca': 'Toyota', 'modelo': 'Corolla',
            'numero_motor': 'MOTOR-PRIVADO', 'numero_serie': 'SERIE-PRIVADA', 'propietario': 'Persona privada',
        }
        response = self.client.get(self.url, {'placa': 'ABC999'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['origen'], 'api')
        self.assertNotIn('PRIVAD', str(response.data))
        self.assertNotIn('propietario', response.data['data'])

    @patch('apps.vehiculos.public_views.ConsultaVehicularService')
    def test_placa_invalida_no_consulta_fuentes_externas(self, servicio):
        for placa in ('', 'ABC', '../ABC123', 'A' * 11):
            self.assertEqual(self.client.get(self.url, {'placa': placa}).status_code, 400)
        servicio.assert_not_called()

    @patch('apps.vehiculos.public_views.ConsultaVehicularService')
    def test_error_externo_no_expone_tokens_ni_detalles(self, servicio):
        servicio.return_value.consultar_placa.side_effect = ConnectionError('token-secreto')
        response = self.client.get(self.url, {'placa': 'ABC999'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('token-secreto', str(response.data))
