"""
Pruebas de CARACTERIZACIÓN del cobro del POS (POST /api/ventas/transacciones/directa/).

Este es el cobro que usan el POS y el Registro Manual de ventas. Estas pruebas fijan lo que HOY
hace el sistema, para poder reorganizar su código (moverlo a un servicio) sin cambiar su
comportamiento: si una prueba falla después de un cambio de código, el cambio alteró un cobro.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.facturacion.models import ComprobanteElectronico
from apps.inventario.models import (
    Almacen, Categoria, InventarioStock, MarcaRepuesto, MovimientoInventario, Repuesto, Sucursal, UbicacionFisica,
)
from apps.seguridad.models import Usuario
from apps.taller.models import OrdenTrabajo
from apps.vehiculos.models import Vehiculo
from apps.ventas.models import (
    Caja, CuentaPorCobrar, DetalleVenta, MetodoPago, MovimientoCaja, PagoVenta, SerieComprobante, SesionCaja,
    TipoComprobante, Venta,
)

URL = '/api/ventas/transacciones/directa/'


class CobroDirectoBase(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_cobro_test', email='admin_cobro_test@example.com',
            nombres='Admin', apellidos='CobroTest', password='x',
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)

        self.sucursal = Sucursal.objects.create(nombre='Sucursal Cobro')
        self.almacen = Almacen.objects.create(sucursal=self.sucursal, nombre='Almacen Cobro')
        self.ubicacion = UbicacionFisica.objects.create(almacen=self.almacen, codigo='GENERAL')
        self.caja = Caja.objects.create(sucursal=self.sucursal, nombre='Caja Cobro')
        self.sesion = SesionCaja.objects.create(caja=self.caja, usuario=self.admin, saldo_inicial=Decimal('0'))

        self.tipo = TipoComprobante.objects.create(nombre='Boleta Cobro', codigo_sunat='03')
        self.serie = SerieComprobante.objects.create(sucursal=self.sucursal, tipo_comprobante=self.tipo, serie='B001')
        self.efectivo = MetodoPago.objects.create(nombre='Efectivo Cobro')
        self.yape = MetodoPago.objects.create(nombre='Yape Cobro', requiere_referencia=True)
        self.cliente = Cliente.objects.create(dni='75000001', nombres='Cliente', apellidos='Cobro')

        self.repuesto = Repuesto.objects.create(
            codigo='COB-1', nombre='Filtro Cobro', categoria=Categoria.objects.create(nombre='Cat Cobro'),
            marca=MarcaRepuesto.objects.create(nombre='Marca Cobro'),
            precio_compra=Decimal('5.00'), precio_por_mayor=Decimal('8.00'),
            precio_cash=Decimal('9.00'), precio_lista=Decimal('10.00'),
        )
        self.stock = InventarioStock.objects.create(
            repuesto=self.repuesto, ubicacion=self.ubicacion, stock_disponible=Decimal('10.00'),
        )

    # --- ayudas ---
    def _payload(self, cantidad=2, precio='10.00', pagos='auto', **extra):
        total = Decimal(str(cantidad)) * Decimal(precio)
        if pagos == 'auto':
            pagos = [{'metodo_pago_id': self.efectivo.id, 'monto': str(total), 'referencia': ''}]
        datos = {
            'cliente_id': self.cliente.id, 'sucursal_id': self.sucursal.id,
            'tipo_comprobante_id': self.tipo.id, 'serie_id': self.serie.id,
            'detalles': [{'repuesto_id': self.repuesto.id, 'cantidad': cantidad, 'precio_venta': precio}],
            'pagos': pagos, 'condicion_pago': 'CONTADO',
        }
        datos.update(extra)
        return datos

    def _cobrar(self, payload):
        return self.api.post(URL, payload, format='json')

    def _stock_actual(self):
        self.stock.refresh_from_db()
        return self.stock.stock_disponible

    def _nada_quedo_guardado(self):
        """Un cobro rechazado no debe dejar ventas, pagos, números de comprobante ni stock tocado."""
        self.serie.refresh_from_db()
        self.assertEqual(Venta.objects.count(), 0)
        self.assertEqual(MovimientoCaja.objects.count(), 0)
        self.assertEqual(self.serie.correlativo_actual, 0)
        self.assertEqual(self._stock_actual(), Decimal('10.00'))
        self.assertEqual(MovimientoInventario.objects.count(), 0)


class CobroContadoTests(CobroDirectoBase):
    def test_venta_al_contado_registra_venta_caja_stock_y_comprobante(self):
        resp = self._cobrar(self._payload())

        self.assertEqual(resp.status_code, 201, resp.data)
        venta = Venta.objects.get()
        self.assertEqual(venta.estado, Venta.Estado.PAGADA)
        self.assertEqual(venta.total, Decimal('20.00'))
        self.assertEqual((venta.subtotal + venta.igv), Decimal('20.00'))
        self.assertEqual(venta.serie_correlativo, 'B001-000001')
        self.assertEqual(venta.sesion_caja, self.sesion)
        self.assertEqual(venta.tipo_comprobante, self.tipo)
        self.assertTrue(venta.ticket_kiosko.startswith('POS-'))
        self.assertIsNotNone(venta.fecha_emision)

        detalle = DetalleVenta.objects.get()
        self.assertEqual((detalle.repuesto, detalle.cantidad, detalle.precio_unitario, detalle.costo_unitario),
                         (self.repuesto, Decimal('2'), Decimal('10.00'), Decimal('5.00')))

        movimiento = MovimientoCaja.objects.get()
        self.assertEqual((movimiento.tipo, movimiento.concepto, movimiento.monto, movimiento.venta_origen),
                         ('INGRESO', 'VENTA', Decimal('20.00'), venta))
        self.assertEqual(PagoVenta.objects.get().monto, Decimal('20.00'))

        self.assertEqual(self._stock_actual(), Decimal('8.00'))
        salida = MovimientoInventario.objects.get()
        self.assertEqual((salida.tipo_movimiento, salida.cantidad, salida.referencia_tipo, salida.referencia_id),
                         ('SALIDA', Decimal('-2.00'), 'VENTA', venta.id))

        self.serie.refresh_from_db()
        self.assertEqual(self.serie.correlativo_actual, 1)
        # El comprobante electrónico solo se PREPARA (queda pendiente); nunca se envía solo a SUNAT.
        comprobante = ComprobanteElectronico.objects.get(venta=venta)
        self.assertEqual(comprobante.estado, ComprobanteElectronico.Estado.PENDIENTE_ENVIO)

    def test_correlativos_consecutivos(self):
        self._cobrar(self._payload())
        self._cobrar(self._payload(cantidad=1))
        self.assertEqual(
            list(Venta.objects.order_by('id').values_list('serie_correlativo', flat=True)),
            ['B001-000001', 'B001-000002'],
        )
        self.serie.refresh_from_db()
        self.assertEqual(self.serie.correlativo_actual, 2)

    def test_con_vuelto_la_caja_registra_solo_el_total_de_la_venta(self):
        pagos = [{'metodo_pago_id': self.efectivo.id, 'monto': '50.00', 'referencia': ''}]
        resp = self._cobrar(self._payload(pagos=pagos))

        self.assertEqual(resp.status_code, 201, resp.data)
        venta = Venta.objects.get()
        self.assertEqual((venta.monto_recibido, venta.vuelto), (Decimal('50.00'), Decimal('30.00')))
        self.assertEqual(MovimientoCaja.objects.get().monto, Decimal('20.00'))

    def test_pago_mixto_efectivo_y_billetera_con_referencia(self):
        pagos = [
            {'metodo_pago_id': self.efectivo.id, 'monto': '5.00', 'referencia': ''},
            {'metodo_pago_id': self.yape.id, 'monto': '15.00', 'referencia': 'OP-998877'},
        ]
        resp = self._cobrar(self._payload(pagos=pagos))

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(MovimientoCaja.objects.count(), 2)
        self.assertEqual(MovimientoCaja.objects.get(metodo_pago=self.yape).referencia, 'OP-998877')

    def test_billetera_sin_numero_de_operacion_se_rechaza(self):
        pagos = [{'metodo_pago_id': self.yape.id, 'monto': '20.00', 'referencia': ''}]
        resp = self._cobrar(self._payload(pagos=pagos))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('requiere referencia', resp.data['error'])
        self._nada_quedo_guardado()


class CobroRechazadoNoDejaRastroTests(CobroDirectoBase):
    def test_pago_menor_al_total(self):
        pagos = [{'metodo_pago_id': self.efectivo.id, 'monto': '10.00', 'referencia': ''}]
        resp = self._cobrar(self._payload(pagos=pagos))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('menor al total', resp.data['error'])
        self._nada_quedo_guardado()

    def test_sin_sesion_de_caja_abierta(self):
        self.sesion.estado = SesionCaja.Estado.CERRADA
        self.sesion.save()
        resp = self._cobrar(self._payload())
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Sesión de caja abierta', resp.data['error'])
        self._nada_quedo_guardado()

    def test_stock_insuficiente(self):
        resp = self._cobrar(self._payload(cantidad=50, precio='10.00'))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Stock insuficiente', resp.data['error'])
        self._nada_quedo_guardado()

    def test_una_venta_al_contado_sin_ningun_pago_se_rechaza(self):
        """Antes el servidor la aceptaba y quedaba PAGADA sin dinero en caja (la pantalla del POS ya lo impedía)."""
        resp = self._cobrar(self._payload(pagos=[]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('al menos un metodo de pago', resp.data['error'])
        self._nada_quedo_guardado()

    def test_serie_inexistente(self):
        resp = self._cobrar(self._payload(serie_id=999999))
        self.assertEqual(resp.status_code, 400)
        self._nada_quedo_guardado()


class CobroAlCreditoTests(CobroDirectoBase):
    def test_credito_genera_cuenta_por_cobrar_sin_ingreso_en_caja(self):
        manana = (timezone.localdate() + timedelta(days=1)).isoformat()
        resp = self._cobrar(self._payload(pagos=[], condicion_pago='CREDITO', fecha_limite=manana))

        self.assertEqual(resp.status_code, 201, resp.data)
        venta = Venta.objects.get()
        self.assertEqual(venta.estado, Venta.Estado.AL_CREDITO)
        cuenta = CuentaPorCobrar.objects.get(venta=venta)
        self.assertEqual(cuenta.monto_financiado, Decimal('20.00'))
        self.assertEqual(cuenta.cuotas.count(), 1)
        self.assertEqual(MovimientoCaja.objects.count(), 0)
        self.assertEqual(self._stock_actual(), Decimal('8.00'))  # el stock sí sale al vender a crédito

    def test_credito_con_vencimiento_hoy_se_rechaza_sin_dejar_rastro(self):
        hoy = timezone.localdate().isoformat()
        resp = self._cobrar(self._payload(pagos=[], condicion_pago='CREDITO', fecha_limite=hoy))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('posterior a hoy', resp.data['error'])
        self._nada_quedo_guardado()
        self.assertEqual(CuentaPorCobrar.objects.count(), 0)


class CobroDeUnPedidoExistenteTests(CobroDirectoBase):
    def _pedido(self, ticket='TK-ABC123', sucursal=None):
        venta = Venta.objects.create(
            cliente=self.cliente, sucursal=sucursal or self.sucursal, estado=Venta.Estado.PRE_VENTA,
            ticket_kiosko=ticket, total=Decimal('20.00'),
        )
        DetalleVenta.objects.create(venta=venta, repuesto=self.repuesto, cantidad=Decimal('2'),
                                    precio_unitario=Decimal('10.00'), subtotal_linea=Decimal('20.00'))
        return venta

    def _cobrar_pedido(self, venta):
        payload = self._payload(venta_id=venta.id)
        payload.pop('detalles')  # los ítems del pedido no se reenvían: son los que ya tiene
        return self._cobrar(payload)

    def test_cobra_el_ticket_del_kiosko_sin_duplicar_sus_items(self):
        venta = self._pedido()
        resp = self._cobrar_pedido(venta)

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Venta.objects.count(), 1)
        venta.refresh_from_db()
        self.assertEqual(venta.estado, Venta.Estado.PAGADA)
        self.assertEqual(venta.serie_correlativo, 'B001-000001')
        self.assertEqual(venta.detalles.count(), 1)
        self.assertEqual(self._stock_actual(), Decimal('8.00'))

    def test_un_ticket_de_otra_sucursal_no_se_puede_cobrar(self):
        otra = Sucursal.objects.create(nombre='Otra Sucursal Cobro')
        venta = self._pedido(sucursal=otra)
        resp = self._cobrar_pedido(venta)

        self.assertEqual(resp.status_code, 400)
        self.assertIn('pertenece a la sucursal', resp.data['error'])
        venta.refresh_from_db()
        self.assertEqual(venta.estado, Venta.Estado.PRE_VENTA)
        self.assertEqual(MovimientoCaja.objects.count(), 0)
        self.assertEqual(self._stock_actual(), Decimal('10.00'))

    def test_una_orden_de_trabajo_no_descuenta_stock_otra_vez_y_queda_facturada(self):
        vehiculo = Vehiculo.objects.create(placa='COB-001', marca='Toyota', modelo='Yaris')
        orden = OrdenTrabajo.objects.create(
            numero='OT-COB1', cliente=self.cliente, vehiculo=vehiculo, recepcionista=self.admin,
            estado=OrdenTrabajo.Estado.FINALIZADO,
        )
        venta = self._pedido(ticket=f'OT-{orden.id}-ABC123')
        resp = self._cobrar_pedido(venta)

        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(self._stock_actual(), Decimal('10.00'))  # el stock ya salió en el taller
        self.assertEqual(MovimientoInventario.objects.count(), 0)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, OrdenTrabajo.Estado.FACTURADO)


class RegistroManualTests(CobroDirectoBase):
    def test_registro_manual_no_toca_la_caja_y_usa_la_fecha_indicada(self):
        self.sesion.estado = SesionCaja.Estado.CERRADA  # el registro manual no exige caja abierta
        self.sesion.save()
        fecha = '2026-01-15T10:30:00-05:00'
        resp = self._cobrar(self._payload(es_registro_manual=True, fecha_manual=fecha))

        self.assertEqual(resp.status_code, 201, resp.data)
        venta = Venta.objects.get()
        self.assertEqual(venta.estado, Venta.Estado.PAGADA)
        self.assertIsNone(venta.sesion_caja)
        self.assertEqual(MovimientoCaja.objects.count(), 0)
        self.assertEqual(PagoVenta.objects.count(), 0)
        self.assertEqual(timezone.localtime(venta.fecha_emision).date().isoformat(), '2026-01-15')
        self.assertEqual(self._stock_actual(), Decimal('8.00'))
