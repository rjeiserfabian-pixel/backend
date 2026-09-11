from django.test import TestCase
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario
from apps.clientes.models import Cliente
from apps.vehiculos.models import Vehiculo


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
