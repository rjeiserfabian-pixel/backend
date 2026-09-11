from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario
from apps.clientes.models import Proveedor
from apps.compras.models import Compra, CuentaPorPagar, TipoComprobanteCompra
from apps.inventario.models import (
    Categoria, MarcaRepuesto, Repuesto, Sucursal, Almacen, UbicacionFisica, InventarioStock
)


class CompraAnularTests(TestCase):
    """
    Cubre el flujo de anulación de compras: revierte stock/kardex y respeta
    las guardas de negocio (no se puede anular dos veces, ni con pagos ya
    aplicados, ni si el stock comprado ya se consumió).
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_compras_test', email='admin_compras_test@example.com',
            nombres='Admin', apellidos='ComprasTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

        self.proveedor = Proveedor.objects.create(
            numero_documento='10000001', nombre_o_razon_social='Proveedor Test'
        )
        self.tipo_comprobante = TipoComprobanteCompra.objects.create(nombre='Boleta Test')

        categoria = Categoria.objects.create(nombre='Categoria Compras Test')
        marca = MarcaRepuesto.objects.create(nombre='Marca Compras Test')
        self.repuesto = Repuesto.objects.create(
            codigo='REP-COMPRAS-TEST', nombre='Repuesto Compras Test',
            categoria=categoria, marca=marca,
            precio_compra=Decimal('5.00'), precio_por_mayor=Decimal('8.00'),
            precio_cash=Decimal('9.00'), precio_lista=Decimal('10.00'),
        )
        sucursal = Sucursal.objects.create(nombre='Sucursal Compras Test')
        self.almacen = Almacen.objects.create(sucursal=sucursal, nombre='Almacen Compras Test')
        UbicacionFisica.objects.create(almacen=self.almacen, codigo='GENERAL')

    def _crear_compra(self, tipo_pago='Contado', numero='0001'):
        payload = {
            'proveedor': self.proveedor.id,
            'fecha_emision': '2026-01-01',
            'tipo_comprobante_fk': self.tipo_comprobante.id,
            'serie': 'TEST',
            'numero_comprobante': numero,
            'tipo_pago': tipo_pago,
            'subtotal': '10.00',
            'igv': '1.80',
            'total': '11.80',
            'almacen_id': self.almacen.id,
            'detalles': [
                {'repuesto': self.repuesto.id, 'cantidad': '2', 'precio_unitario': '5.00'}
            ],
        }
        if tipo_pago == 'Credito':
            payload['fecha_vencimiento'] = '2026-02-01'
        return self.client.post('/api/compras/compras/', payload, format='json')

    def _stock_disponible(self):
        return InventarioStock.objects.filter(repuesto=self.repuesto).first().stock_disponible

    def test_anular_revierte_stock_y_cambia_estado(self):
        resp = self._crear_compra(numero='0001')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(self._stock_disponible(), Decimal('2.00'))

        compra_id = resp.data['id']
        resp_anular = self.client.post(f'/api/compras/compras/{compra_id}/anular/')
        self.assertEqual(resp_anular.status_code, 200, resp_anular.data)

        compra = Compra.objects.get(id=compra_id)
        self.assertEqual(compra.estado, 'Anulada')
        self.assertEqual(self._stock_disponible(), Decimal('0.00'))

    def test_no_se_puede_anular_dos_veces(self):
        resp = self._crear_compra(numero='0002')
        compra_id = resp.data['id']
        self.client.post(f'/api/compras/compras/{compra_id}/anular/')

        resp2 = self.client.post(f'/api/compras/compras/{compra_id}/anular/')
        self.assertEqual(resp2.status_code, 400)

    def test_no_se_puede_anular_con_pago_aplicado(self):
        resp = self._crear_compra(tipo_pago='Credito', numero='0003')
        compra_id = resp.data['id']
        cuenta = CuentaPorPagar.objects.get(compra_id=compra_id)

        resp_pago = self.client.post('/api/compras/pagos-cuenta/', {
            'cuenta_por_pagar': cuenta.id,
            'monto_abonado': '5.00',
            'fecha_pago': '2026-01-02',
            'metodo_pago': 'Efectivo',
        }, format='json')
        self.assertEqual(resp_pago.status_code, 201, resp_pago.data)

        resp_anular = self.client.post(f'/api/compras/compras/{compra_id}/anular/')
        self.assertEqual(resp_anular.status_code, 400)

    def test_no_se_puede_anular_con_stock_insuficiente(self):
        resp = self._crear_compra(numero='0004')
        compra_id = resp.data['id']

        inv = InventarioStock.objects.filter(repuesto=self.repuesto).first()
        inv.stock_disponible = Decimal('0')
        inv.save()

        resp_anular = self.client.post(f'/api/compras/compras/{compra_id}/anular/')
        self.assertEqual(resp_anular.status_code, 400)
