from django.test import TestCase
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario
from apps.clientes.models import Cliente, Proveedor, Transportista


class SoftDeleteTests(TestCase):
    """
    Cubre el bug real: el DELETE de Cliente/Proveedor/Transportista borraba la
    fila físicamente pese a que el modelo ya tiene un campo `estado` pensado
    para soft-delete (nunca se usaba). Ahora debe ser soft-delete: la fila
    sigue en la base de datos con estado=False, y deja de aparecer en el
    listado (que ya filtra por estado=True).
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_clientes_test', email='admin_clientes_test@example.com',
            nombres='Admin', apellidos='ClientesTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_eliminar_cliente_es_soft_delete(self):
        cliente = Cliente.objects.create(dni='11111111', nombres='Cliente', apellidos='Test')

        resp = self.client.delete(f'/api/clientes/{cliente.id}/')
        self.assertIn(resp.status_code, (200, 202, 204))

        cliente.refresh_from_db()
        self.assertFalse(cliente.estado)
        self.assertTrue(Cliente.objects.filter(id=cliente.id).exists())

        resp_list = self.client.get('/api/clientes/')
        ids_listados = [c['id'] for c in resp_list.data.get('results', resp_list.data)]
        self.assertNotIn(cliente.id, ids_listados)

    def test_eliminar_proveedor_es_soft_delete(self):
        proveedor = Proveedor.objects.create(numero_documento='20123456789', nombre_o_razon_social='Proveedor Test')

        resp = self.client.delete(f'/api/clientes/proveedores/{proveedor.id}/')
        self.assertIn(resp.status_code, (200, 202, 204))

        proveedor.refresh_from_db()
        self.assertFalse(proveedor.estado)
        self.assertTrue(Proveedor.objects.filter(id=proveedor.id).exists())

    def test_eliminar_transportista_es_soft_delete(self):
        transportista = Transportista.objects.create(numero_documento='20987654321', nombre_o_razon_social='Transportista Test')

        resp = self.client.delete(f'/api/clientes/transportistas/{transportista.id}/')
        self.assertIn(resp.status_code, (200, 202, 204))

        transportista.refresh_from_db()
        self.assertFalse(transportista.estado)
        self.assertTrue(Transportista.objects.filter(id=transportista.id).exists())


class PermisosClientesTests(TestCase):
    """Cubre el bug real: ningún ViewSet de Clientes exigía permisos (bare IsAuthenticated)."""

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_permisos_clientes_test', email='admin_permisos_clientes_test@example.com',
            nombres='Admin', apellidos='PermisosTest', password='x',
        )
        self.sin_permisos = Usuario.objects.create_user(
            username='sin_permisos_clientes_test', email='sin_permisos_clientes_test@example.com',
            nombres='Sin', apellidos='Permisos', password='x', estado='activo',
        )

    def test_admin_puede_listar_clientes(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        resp = client.get('/api/clientes/')
        self.assertEqual(resp.status_code, 200)

    def test_usuario_sin_permisos_no_puede_listar_clientes(self):
        client = APIClient()
        client.force_authenticate(user=self.sin_permisos)
        resp = client.get('/api/clientes/')
        self.assertEqual(resp.status_code, 403)
