from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario
from apps.clientes.models import Cliente
from apps.vehiculos.models import Vehiculo
from apps.taller.models import OrdenTrabajo, OrdenRepuesto
from apps.inventario.models import (
    Categoria, MarcaRepuesto, Repuesto, Sucursal, Almacen, UbicacionFisica, InventarioStock
)


class OrdenTrabajoTests(TestCase):
    """
    Cubre el candado de la máquina de estados (transiciones manuales permitidas
    vs. saltos directos rechazados) y el flujo de anulación de una orden
    (libera reservas de stock, respeta sus guardas de negocio).
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_taller_test', email='admin_taller_test@example.com',
            nombres='Admin', apellidos='TallerTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

        self.cliente = Cliente.objects.create(dni='90000001', nombres='Cliente', apellidos='Test')
        self.vehiculo = Vehiculo.objects.create(placa='TEST-TLR1', marca='Toyota', modelo='Yaris')

        categoria = Categoria.objects.create(nombre='Categoria Taller Test')
        marca = MarcaRepuesto.objects.create(nombre='Marca Taller Test')
        self.repuesto = Repuesto.objects.create(
            codigo='REP-TALLER-TEST', nombre='Filtro Taller Test',
            categoria=categoria, marca=marca,
            precio_compra=Decimal('5.00'), precio_por_mayor=Decimal('8.00'),
            precio_cash=Decimal('9.00'), precio_lista=Decimal('10.00'),
        )
        sucursal = Sucursal.objects.create(nombre='Sucursal Taller Test')
        almacen = Almacen.objects.create(sucursal=sucursal, nombre='Almacen Taller Test')
        self.ubicacion = UbicacionFisica.objects.create(almacen=almacen, codigo='GENERAL')
        InventarioStock.objects.create(
            repuesto=self.repuesto, ubicacion=self.ubicacion, stock_disponible=Decimal('20')
        )

    def _crear_orden(self):
        return self.client.post('/api/taller/ordenes/', {
            'vehiculo': self.vehiculo.id,
            'cliente': self.cliente.id,
        }, format='json')

    def _stock(self):
        return InventarioStock.objects.filter(repuesto=self.repuesto, ubicacion=self.ubicacion).first()

    # --- Candado de estados ---

    def test_patch_directo_a_facturado_se_rechaza(self):
        resp = self._crear_orden()
        orden_id = resp.data['id']

        resp_patch = self.client.patch(f'/api/taller/ordenes/{orden_id}/', {'estado': 'FACTURADO'}, format='json')
        self.assertEqual(resp_patch.status_code, 400)
        self.assertEqual(OrdenTrabajo.objects.get(id=orden_id).estado, 'RECEPCIONADO')

    def test_transicion_legitima_recepcionado_a_inspeccion(self):
        resp = self._crear_orden()
        orden_id = resp.data['id']

        resp_patch = self.client.patch(f'/api/taller/ordenes/{orden_id}/', {'estado': 'INSPECCION'}, format='json')
        self.assertEqual(resp_patch.status_code, 200, resp_patch.data)
        self.assertEqual(OrdenTrabajo.objects.get(id=orden_id).estado, 'INSPECCION')

    def test_transicion_legitima_inspeccion_a_esperando_aprobacion(self):
        resp = self._crear_orden()
        orden_id = resp.data['id']
        self.client.patch(f'/api/taller/ordenes/{orden_id}/', {'estado': 'INSPECCION'}, format='json')

        resp_patch = self.client.patch(
            f'/api/taller/ordenes/{orden_id}/', {'estado': 'ESPERANDO_APROBACION'}, format='json'
        )
        self.assertEqual(resp_patch.status_code, 200, resp_patch.data)
        self.assertEqual(OrdenTrabajo.objects.get(id=orden_id).estado, 'ESPERANDO_APROBACION')

    # --- Anulación ---

    def test_anular_libera_reserva_de_stock(self):
        resp = self._crear_orden()
        orden_id = resp.data['id']

        resp_rep = self.client.post('/api/taller/repuestos/', {
            'orden': orden_id, 'repuesto': self.repuesto.id,
            'cantidad': '2', 'precio_unitario': '10.00',
        }, format='json')
        self.assertEqual(resp_rep.status_code, 201, resp_rep.data)
        orp_id = resp_rep.data['id']

        resp_aprobar = self.client.post(f'/api/taller/ordenes/{orden_id}/aprobar_servicios/', {
            'servicios_aprobados': [], 'repuestos_aprobados': [orp_id],
        }, format='json')
        self.assertEqual(resp_aprobar.status_code, 200, resp_aprobar.data)

        stock = self._stock()
        self.assertEqual(stock.stock_disponible, Decimal('18.00'))
        self.assertEqual(stock.stock_reservado, Decimal('2.00'))

        resp_anular = self.client.post(
            f'/api/taller/ordenes/{orden_id}/anular/', {'motivo': 'Prueba automatizada'}, format='json'
        )
        self.assertEqual(resp_anular.status_code, 200, resp_anular.data)

        stock.refresh_from_db()
        self.assertEqual(stock.stock_disponible, Decimal('20.00'))
        self.assertEqual(stock.stock_reservado, Decimal('0.00'))
        self.assertEqual(OrdenTrabajo.objects.get(id=orden_id).estado, 'CANCELADO')

    def test_anular_sin_motivo_se_rechaza(self):
        resp = self._crear_orden()
        orden_id = resp.data['id']

        resp_anular = self.client.post(f'/api/taller/ordenes/{orden_id}/anular/', {}, format='json')
        self.assertEqual(resp_anular.status_code, 400)
        self.assertEqual(OrdenTrabajo.objects.get(id=orden_id).estado, 'RECEPCIONADO')

    def test_anular_con_repuesto_instalado_se_rechaza(self):
        resp = self._crear_orden()
        orden_id = resp.data['id']

        resp_rep = self.client.post('/api/taller/repuestos/', {
            'orden': orden_id, 'repuesto': self.repuesto.id,
            'cantidad': '1', 'precio_unitario': '10.00',
        }, format='json')
        orp_id = resp_rep.data['id']

        self.client.post(f'/api/taller/ordenes/{orden_id}/aprobar_servicios/', {
            'servicios_aprobados': [], 'repuestos_aprobados': [orp_id],
        }, format='json')
        resp_instalar = self.client.patch(f'/api/taller/repuestos/{orp_id}/marcar_instalado/', {}, format='json')
        self.assertEqual(resp_instalar.status_code, 200, resp_instalar.data)

        resp_anular = self.client.post(
            f'/api/taller/ordenes/{orden_id}/anular/', {'motivo': 'x'}, format='json'
        )
        self.assertEqual(resp_anular.status_code, 400)

    # --- Bug real detectado y corregido: reservas de stock desincronizadas ---

    def test_marcar_instalado_es_simetrico_no_duplica_descuento(self):
        """
        Antes, activar/desactivar 'instalado' varias veces descontaba
        stock_reservado de más (solo activar tenía efecto). Ahora debe ser
        simétrico: activar descuenta, desactivar devuelve, sin acumular error.
        """
        resp = self._crear_orden()
        orden_id = resp.data['id']

        resp_rep = self.client.post('/api/taller/repuestos/', {
            'orden': orden_id, 'repuesto': self.repuesto.id,
            'cantidad': '1', 'precio_unitario': '10.00',
        }, format='json')
        orp_id = resp_rep.data['id']

        self.client.post(f'/api/taller/ordenes/{orden_id}/aprobar_servicios/', {
            'servicios_aprobados': [], 'repuestos_aprobados': [orp_id],
        }, format='json')

        stock = self._stock()
        self.assertEqual(stock.stock_disponible, Decimal('19.00'))
        self.assertEqual(stock.stock_reservado, Decimal('1.00'))

        # Instalar
        r1 = self.client.patch(f'/api/taller/repuestos/{orp_id}/marcar_instalado/', {}, format='json')
        self.assertEqual(r1.status_code, 200, r1.data)
        stock.refresh_from_db()
        self.assertEqual(stock.stock_reservado, Decimal('0.00'))

        # Des-instalar: debe devolver la reserva (antes no lo hacía)
        r2 = self.client.patch(f'/api/taller/repuestos/{orp_id}/marcar_instalado/', {}, format='json')
        self.assertEqual(r2.status_code, 200, r2.data)
        stock.refresh_from_db()
        self.assertEqual(stock.stock_reservado, Decimal('1.00'))

        # Instalar de nuevo: no debe descontar de más
        r3 = self.client.patch(f'/api/taller/repuestos/{orp_id}/marcar_instalado/', {}, format='json')
        self.assertEqual(r3.status_code, 200, r3.data)
        stock.refresh_from_db()
        self.assertEqual(stock.stock_reservado, Decimal('0.00'))
        self.assertGreaterEqual(stock.stock_reservado, Decimal('0.00'))

    def test_desaprobar_repuesto_libera_reserva_sin_duplicar_al_reaprobar(self):
        """
        Antes, reenviar aprobar_servicios sin un repuesto previamente aprobado
        lo desaprobaba sin liberar su reserva; volver a aprobarlo lo reservaba
        una segunda vez sobre la misma cantidad física.
        """
        resp = self._crear_orden()
        orden_id = resp.data['id']

        resp_rep = self.client.post('/api/taller/repuestos/', {
            'orden': orden_id, 'repuesto': self.repuesto.id,
            'cantidad': '1', 'precio_unitario': '10.00',
        }, format='json')
        orp_id = resp_rep.data['id']

        r1 = self.client.post(f'/api/taller/ordenes/{orden_id}/aprobar_servicios/', {
            'servicios_aprobados': [], 'repuestos_aprobados': [orp_id],
        }, format='json')
        self.assertEqual(r1.status_code, 200, r1.data)
        stock = self._stock()
        self.assertEqual(stock.stock_reservado, Decimal('1.00'))

        # Reenviar la aprobación sin el repuesto: debe liberar la reserva
        r2 = self.client.post(f'/api/taller/ordenes/{orden_id}/aprobar_servicios/', {
            'servicios_aprobados': [], 'repuestos_aprobados': [],
        }, format='json')
        self.assertEqual(r2.status_code, 200, r2.data)
        stock.refresh_from_db()
        self.assertEqual(stock.stock_reservado, Decimal('0.00'))
        self.assertEqual(stock.stock_disponible, Decimal('20.00'))

        # Reaprobar: no debe reservar por segunda vez
        r3 = self.client.post(f'/api/taller/ordenes/{orden_id}/aprobar_servicios/', {
            'servicios_aprobados': [], 'repuestos_aprobados': [orp_id],
        }, format='json')
        self.assertEqual(r3.status_code, 200, r3.data)
        stock.refresh_from_db()
        self.assertEqual(stock.stock_reservado, Decimal('1.00'))
        self.assertEqual(stock.stock_disponible, Decimal('19.00'))

    def test_patch_directo_a_aprobado_o_instalado_no_tiene_efecto(self):
        """
        aprobado_cliente/instalado deben ser de solo lectura por PATCH directo;
        solo cambian vía aprobar_servicios/marcar_instalado (que sincronizan stock).
        """
        resp = self._crear_orden()
        orden_id = resp.data['id']
        resp_rep = self.client.post('/api/taller/repuestos/', {
            'orden': orden_id, 'repuesto': self.repuesto.id,
            'cantidad': '1', 'precio_unitario': '10.00',
        }, format='json')
        orp_id = resp_rep.data['id']

        resp_patch = self.client.patch(
            f'/api/taller/repuestos/{orp_id}/',
            {'instalado': True, 'aprobado_cliente': True},
            format='json',
        )
        self.assertEqual(resp_patch.status_code, 200, resp_patch.data)

        orp = OrdenRepuesto.objects.get(id=orp_id)
        self.assertFalse(orp.instalado)
        self.assertFalse(orp.aprobado_cliente)
