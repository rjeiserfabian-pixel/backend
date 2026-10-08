from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario
from apps.inventario.models import Sucursal
from apps.ventas.models import Impuesto, Caja, SesionCaja, MetodoPago, MovimientoCaja
from apps.ventas.services import VentasService


class DescomponerTotalConImpuestoTests(TestCase):
    """
    Cubre el bug real: el IGV estaba hardcodeado al 18% (Decimal('1.18')) en
    3 sitios distintos en vez de leerse del catálogo Impuesto ya existente.
    """

    def test_usa_la_tasa_configurada_del_catalogo(self):
        Impuesto.objects.create(nombre='IGV', tasa=Decimal('18.00'), estado=True)

        subtotal, igv = VentasService.descomponer_total_con_impuesto(Decimal('118.00'))

        self.assertEqual(subtotal, Decimal('100.00'))
        self.assertEqual(igv, Decimal('18.00'))

    def test_no_esta_fijo_en_18_por_ciento(self):
        Impuesto.objects.create(nombre='IGV', tasa=Decimal('10.00'), estado=True)

        subtotal, igv = VentasService.descomponer_total_con_impuesto(Decimal('110.00'))

        self.assertEqual(subtotal, Decimal('100.00'))
        self.assertEqual(igv, Decimal('10.00'))

    def test_ignora_impuestos_inactivos(self):
        Impuesto.objects.create(nombre='IGV', tasa=Decimal('18.00'), estado=False)
        Impuesto.objects.create(nombre='IGV Vigente', tasa=Decimal('18.00'), estado=True)

        tasa = VentasService.obtener_tasa_impuesto()

        self.assertEqual(tasa, Decimal('18.00'))

    def test_usa_respaldo_del_18_por_ciento_si_no_hay_impuesto_configurado(self):
        self.assertFalse(Impuesto.objects.exists())

        subtotal, igv = VentasService.descomponer_total_con_impuesto(Decimal('118.00'))

        self.assertEqual(subtotal, Decimal('100.00'))
        self.assertEqual(igv, Decimal('18.00'))


class SesionCajaCierreIgnoraMovimientosPendientesTests(TestCase):
    """
    Cubre el bug real de la duplicación Caja/Ventas vs Caja/Cajas: un movimiento
    manual PENDIENTE de aprobación (registrado desde el módulo Cajas) no debe
    contarse en el saldo al cerrar la sesión desde /caja (módulo Ventas) — antes
    se sumaba igual porque no se filtraba por estado_movimiento.
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_cierre_test', email='admin_cierre_test@example.com',
            nombres='Admin', apellidos='CierreTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

        sucursal = Sucursal.objects.create(nombre='Sucursal Cierre Test')
        self.caja = Caja.objects.create(sucursal=sucursal, nombre='Caja Cierre Test')
        self.sesion = SesionCaja.objects.create(
            caja=self.caja, usuario=self.admin, saldo_inicial=Decimal('100.00')
        )
        self.metodo_pago = MetodoPago.objects.create(nombre='Efectivo Test')

        MovimientoCaja.objects.create(
            sesion=self.sesion, tipo=MovimientoCaja.Tipo.INGRESO,
            concepto=MovimientoCaja.Concepto.OTROS_INGRESOS, metodo_pago=self.metodo_pago,
            monto=Decimal('50.00'), estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO,
            creado_por=self.admin,
        )
        MovimientoCaja.objects.create(
            sesion=self.sesion, tipo=MovimientoCaja.Tipo.INGRESO,
            concepto=MovimientoCaja.Concepto.INGRESO_MANUAL, metodo_pago=self.metodo_pago,
            monto=Decimal('30.00'), estado_movimiento=MovimientoCaja.EstadoMovimiento.PENDIENTE,
            creado_por=self.admin,
        )

    def test_detalle_activa_no_suma_movimiento_pendiente(self):
        resp = self.client.get(f'/api/ventas/sesiones/{self.sesion.id}/detalle-activa/')
        self.assertEqual(resp.status_code, 200, resp.data)
        # Solo el ingreso APROBADO (50.00) debe contar; el PENDIENTE (30.00) no.
        self.assertEqual(resp.data['ingresos'], 50.0)
        self.assertEqual(resp.data['saldo_actual'], 150.0)

    def test_cerrar_no_suma_movimiento_pendiente(self):
        resp = self.client.post(f'/api/ventas/sesiones/{self.sesion.id}/cerrar/', {
            'saldo_cierre_real': '150.00'
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)

        self.sesion.refresh_from_db()
        self.assertEqual(self.sesion.saldo_cierre_esperado, Decimal('150.00'))

    def test_no_se_puede_cerrar_dos_veces(self):
        self.client.post(f'/api/ventas/sesiones/{self.sesion.id}/cerrar/', {
            'saldo_cierre_real': '150.00'
        }, format='json')

        resp2 = self.client.post(f'/api/ventas/sesiones/{self.sesion.id}/cerrar/', {
            'saldo_cierre_real': '150.00'
        }, format='json')
        self.assertEqual(resp2.status_code, 400)

    def test_patch_directo_a_sesion_ya_no_esta_permitido(self):
        # SesionCajaViewSet ahora es de solo lectura (ReadOnlyModelViewSet);
        # solo se puede mutar vía las acciones aperturar/cerrar.
        resp = self.client.patch(f'/api/ventas/sesiones/{self.sesion.id}/', {
            'saldo_inicial': '999.00'
        }, format='json')
        self.assertEqual(resp.status_code, 405)


class CancelarPedidoPendienteTests(TestCase):
    """
    Cancelar un pedido (Kiosko/Taller/POS) pendiente: solo PRE_VENTA, con motivo,
    y sin posibilidad de borrar ventas por la API genérica.
    """

    def setUp(self):
        from apps.clientes.models import Cliente
        self.admin = Usuario.objects.create_superuser(
            username='admin_cancelar_test', email='admin_cancelar_test@example.com',
            nombres='Admin', apellidos='CancelarTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.sucursal = Sucursal.objects.create(nombre='Sucursal Cancelar Test')
        self.cliente = Cliente.objects.create(dni='80000001', nombres='Cliente', apellidos='Cancelar')

    def _venta(self, estado):
        from apps.ventas.models import Venta
        return Venta.objects.create(
            cliente=self.cliente, sucursal=self.sucursal, estado=estado, ticket_kiosko='TK-CANC01'
        )

    def test_cancela_pre_venta_con_motivo(self):
        from apps.ventas.models import Venta
        venta = self._venta(Venta.Estado.PRE_VENTA)

        resp = self.client.post(f'/api/ventas/transacciones/{venta.id}/cancelar/', {'motivo': 'Cliente desistió'}, format='json')

        self.assertEqual(resp.status_code, 200, resp.data)
        venta.refresh_from_db()
        self.assertEqual(venta.estado, Venta.Estado.ANULADA)
        self.assertIsNotNone(venta.anulado_en)

    def test_exige_motivo(self):
        from apps.ventas.models import Venta
        venta = self._venta(Venta.Estado.PRE_VENTA)

        resp = self.client.post(f'/api/ventas/transacciones/{venta.id}/cancelar/', {}, format='json')

        self.assertEqual(resp.status_code, 400)
        venta.refresh_from_db()
        self.assertEqual(venta.estado, Venta.Estado.PRE_VENTA)

    def test_no_cancela_venta_ya_pagada(self):
        from apps.ventas.models import Venta
        venta = self._venta(Venta.Estado.PAGADA)

        resp = self.client.post(f'/api/ventas/transacciones/{venta.id}/cancelar/', {'motivo': 'x'}, format='json')

        self.assertEqual(resp.status_code, 400)
        venta.refresh_from_db()
        self.assertEqual(venta.estado, Venta.Estado.PAGADA)

    def test_no_se_puede_borrar_una_venta_por_la_api(self):
        from apps.ventas.models import Venta
        venta = self._venta(Venta.Estado.PAGADA)

        resp = self.client.delete(f'/api/ventas/transacciones/{venta.id}/')

        self.assertEqual(resp.status_code, 405)
        self.assertTrue(Venta.objects.filter(id=venta.id).exists())


class AnularVentaCobradaTests(TestCase):
    """
    Anulación de ventas ya cobradas: revierte stock (misma ubicación) y caja,
    y se rechaza cuando el comprobante ya fue enviado a SUNAT, viene de una OT
    o el crédito ya tiene cobros.
    """

    def setUp(self):
        from apps.clientes.models import Cliente
        from apps.inventario.models import (
            Categoria, MarcaRepuesto, Repuesto, Almacen, UbicacionFisica, InventarioStock,
        )
        self.admin = Usuario.objects.create_superuser(
            username='admin_anular_test', email='admin_anular_test@example.com',
            nombres='Admin', apellidos='AnularTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.sucursal = Sucursal.objects.create(nombre='Sucursal Anular Test')
        self.cliente = Cliente.objects.create(dni='80000002', nombres='Cliente', apellidos='Anular')
        self.caja = Caja.objects.create(sucursal=self.sucursal, nombre='Caja Anular Test')
        self.sesion = SesionCaja.objects.create(caja=self.caja, usuario=self.admin, saldo_inicial=Decimal('0.00'))
        self.metodo = MetodoPago.objects.create(nombre='Efectivo Anular Test')

        categoria = Categoria.objects.create(nombre='Cat Anular Test')
        marca = MarcaRepuesto.objects.create(nombre='Marca Anular Test')
        self.repuesto = Repuesto.objects.create(
            codigo='REP-ANUL-TEST', nombre='Filtro Anular Test', categoria=categoria, marca=marca,
            precio_compra=Decimal('5.00'), precio_por_mayor=Decimal('8.00'),
            precio_cash=Decimal('9.00'), precio_lista=Decimal('10.00'),
        )
        almacen = Almacen.objects.create(sucursal=self.sucursal, nombre='Almacen Anular Test')
        self.ubicacion = UbicacionFisica.objects.create(almacen=almacen, codigo='GENERAL')
        self.stock = InventarioStock.objects.create(
            repuesto=self.repuesto, ubicacion=self.ubicacion, stock_disponible=Decimal('10.00')
        )

    def _venta_cobrada(self, ticket='POS-ANUL01', estado=None):
        """Crea una venta pagada con su detalle, pago en caja y salida de stock de 2 unidades."""
        from apps.ventas.models import Venta, DetalleVenta, PagoVenta
        from apps.inventario.models import MovimientoInventario
        venta = Venta.objects.create(
            cliente=self.cliente, sucursal=self.sucursal, sesion_caja=self.sesion,
            estado=estado or Venta.Estado.PAGADA, ticket_kiosko=ticket,
            serie_correlativo='B001-00000001', total=Decimal('20.00'),
        )
        DetalleVenta.objects.create(
            venta=venta, repuesto=self.repuesto, cantidad=Decimal('2'),
            precio_unitario=Decimal('10.00'), subtotal_linea=Decimal('20.00'),
        )
        mov = MovimientoCaja.objects.create(
            sesion=self.sesion, tipo=MovimientoCaja.Tipo.INGRESO, concepto=MovimientoCaja.Concepto.VENTA,
            metodo_pago=self.metodo, monto=Decimal('20.00'), venta_origen=venta, creado_por=self.admin,
        )
        PagoVenta.objects.create(venta=venta, movimiento_caja=mov, monto=Decimal('20.00'))
        self.stock.stock_disponible -= Decimal('2.00')
        self.stock.save()
        MovimientoInventario.objects.create(
            repuesto=self.repuesto, ubicacion=self.ubicacion,
            tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA, cantidad=Decimal('-2.00'),
            stock_resultante=self.stock.stock_disponible, motivo='Venta test',
            usuario=self.admin, referencia_id=venta.id, referencia_tipo='VENTA',
        )
        return venta

    def _anular(self, venta, motivo='Registrada por error'):
        return self.client.post(f'/api/ventas/transacciones/{venta.id}/anular/', {'motivo': motivo}, format='json')

    def test_anula_revierte_stock_y_registra_egreso_en_caja(self):
        from apps.ventas.models import Venta
        venta = self._venta_cobrada()

        resp = self._anular(venta)

        self.assertEqual(resp.status_code, 200, resp.data)
        venta.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(venta.estado, Venta.Estado.ANULADA)
        self.assertEqual(venta.motivo_anulacion, 'Registrada por error')
        self.assertEqual(venta.anulado_por, self.admin)
        self.assertEqual(self.stock.stock_disponible, Decimal('10.00'))
        egreso = MovimientoCaja.objects.get(tipo=MovimientoCaja.Tipo.EGRESO, venta_origen=venta)
        self.assertEqual(egreso.monto, Decimal('20.00'))
        self.assertEqual(egreso.concepto, MovimientoCaja.Concepto.DEVOLUCION)

    def test_exige_motivo(self):
        venta = self._venta_cobrada()
        resp = self.client.post(f'/api/ventas/transacciones/{venta.id}/anular/', {}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_no_se_anula_dos_veces(self):
        venta = self._venta_cobrada()
        self.assertEqual(self._anular(venta).status_code, 200)
        self.assertEqual(self._anular(venta).status_code, 400)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.stock_disponible, Decimal('10.00'))

    def test_requiere_caja_abierta(self):
        venta = self._venta_cobrada()
        self.sesion.estado = SesionCaja.Estado.CERRADA
        self.sesion.save()

        resp = self._anular(venta)

        self.assertEqual(resp.status_code, 400)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.stock_disponible, Decimal('8.00'))

    def test_rechaza_venta_de_orden_de_trabajo(self):
        venta = self._venta_cobrada(ticket='OT-1-ABC123')
        self.assertEqual(self._anular(venta).status_code, 400)

    def test_rechaza_si_comprobante_ya_fue_enviado_a_sunat(self):
        from apps.facturacion.models import ComprobanteElectronico
        venta = self._venta_cobrada()
        ComprobanteElectronico.objects.create(
            tipo_documento='03', serie='B001', numero='00000001', sucursal=self.sucursal,
            venta=venta, total=venta.total, estado=ComprobanteElectronico.Estado.ACEPTADO,
            creado_por=self.admin,
        )

        resp = self._anular(venta)

        self.assertEqual(resp.status_code, 400)
        self.assertIn('Nota de Crédito', resp.data['error'])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.stock_disponible, Decimal('8.00'))

    def test_elimina_comprobante_pendiente_nunca_enviado(self):
        from apps.facturacion.models import ComprobanteElectronico
        venta = self._venta_cobrada()
        ComprobanteElectronico.objects.create(
            tipo_documento='03', serie='B001', numero='00000001', sucursal=self.sucursal,
            venta=venta, total=venta.total, creado_por=self.admin,
        )

        resp = self._anular(venta)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(ComprobanteElectronico.objects.filter(venta=venta).exists())

    def test_venta_al_credito_elimina_cuenta_por_cobrar(self):
        from apps.ventas.models import Venta, CuentaPorCobrar
        venta = self._venta_cobrada(estado=Venta.Estado.AL_CREDITO)
        venta.pagos.all().delete()
        CuentaPorCobrar.objects.create(
            venta=venta, codigo_credito='CR-ANUL-1', frecuencia_pago='MENSUAL',
            monto_financiado=Decimal('20.00'), saldo_pendiente=Decimal('20.00'),
        )

        resp = self._anular(venta)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(CuentaPorCobrar.objects.filter(venta=venta).exists())
        self.assertFalse(MovimientoCaja.objects.filter(tipo=MovimientoCaja.Tipo.EGRESO).exists())
