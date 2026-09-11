from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario
from apps.inventario.models import (
    Categoria, MarcaRepuesto, Repuesto, Sucursal, Almacen, UbicacionFisica, InventarioStock,
)
from apps.clientes.models import Cliente, Proveedor
from apps.ventas.models import Venta, Caja, SesionCaja, MovimientoCaja, MetodoPago
from apps.compras.models import Compra, DetalleCompra
from apps.vehiculos.models import Vehiculo
from apps.taller.models import OrdenTrabajo


class ReporteProductosTests(TestCase):
    """
    Cubre el bug real: el Reporte de Productos reventaba con 500 en cada
    petición (`prefetch_related("stocks")` apuntaba a una relación
    inexistente y `s.cantidad` a un campo que no existe en InventarioStock).
    Además, el filtro `stock_estado` se aplicaba en Python después de que
    la BD ya había paginado, así que `total`/`total_pages` no coincidían
    con lo realmente filtrado.
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_reportes_productos_test', email='admin_reportes_productos_test@example.com',
            nombres='Admin', apellidos='ReportesTest', password='x',
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

        categoria = Categoria.objects.create(nombre='Filtros RPT')
        marca = MarcaRepuesto.objects.create(nombre='Marca RPT')
        sucursal = Sucursal.objects.create(nombre='Sucursal RPT')
        almacen = Almacen.objects.create(sucursal=sucursal, nombre='Almacen RPT')
        ubicacion = UbicacionFisica.objects.create(almacen=almacen, codigo='A-1')

        def crear_repuesto(codigo, nombre, stock_disponible):
            repuesto = Repuesto.objects.create(
                codigo=codigo, nombre=nombre, categoria=categoria, marca=marca,
                precio_compra=Decimal('10.00'), precio_por_mayor=Decimal('12.00'),
                precio_cash=Decimal('15.00'), precio_lista=Decimal('18.00'),
            )
            InventarioStock.objects.create(
                repuesto=repuesto, ubicacion=ubicacion, stock_disponible=Decimal(stock_disponible),
            )
            return repuesto

        self.repuesto_agotado = crear_repuesto('RPT-AGOTADO', 'Repuesto Agotado', 0)
        self.repuesto_bajo = crear_repuesto('RPT-BAJO', 'Repuesto Bajo', 3)
        self.repuesto_normal = crear_repuesto('RPT-NORMAL', 'Repuesto Normal', 50)

    def test_reporte_productos_no_revienta(self):
        resp = self.client_api.get('/api/reportes/productos/')
        self.assertEqual(resp.status_code, 200, resp.content)
        nombres = {d['nombre'] for d in resp.data['data']}
        self.assertEqual(
            nombres, {'Repuesto Agotado', 'Repuesto Bajo', 'Repuesto Normal'},
        )

    def test_filtro_stock_estado_agotado_coincide_con_total(self):
        resp = self.client_api.get('/api/reportes/productos/', {'stock_estado': 'agotado'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['total'], 1)
        self.assertEqual(len(resp.data['data']), 1)
        self.assertEqual(resp.data['data'][0]['nombre'], 'Repuesto Agotado')

    def test_filtro_stock_estado_bajo(self):
        resp = self.client_api.get('/api/reportes/productos/', {'stock_estado': 'bajo'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['total'], 1)
        self.assertEqual(resp.data['data'][0]['nombre'], 'Repuesto Bajo')

    def test_filtro_stock_estado_normal(self):
        resp = self.client_api.get('/api/reportes/productos/', {'stock_estado': 'normal'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['total'], 1)
        self.assertEqual(resp.data['data'][0]['nombre'], 'Repuesto Normal')


class ReporteVentasTests(TestCase):
    """
    Cubre el bug real: el Reporte de Ventas nunca excluía ventas ANULADA
    (seguían sumando al reporte) y el bloque `totales` sumaba montos de
    ventas en soles, dólares y euros directamente, sin convertir a una
    sola moneda con el tipo_cambio ya guardado en cada venta.
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_reportes_ventas_test', email='admin_reportes_ventas_test@example.com',
            nombres='Admin', apellidos='ReportesTest', password='x',
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

        self.sucursal = Sucursal.objects.create(nombre='Sucursal Ventas RPT')
        self.cliente = Cliente.objects.create(dni='40000001', nombres='Cliente', apellidos='Ventas')
        caja = Caja.objects.create(sucursal=self.sucursal, nombre='Caja Ventas RPT')
        self.sesion_caja = SesionCaja.objects.create(caja=caja, usuario=self.admin, saldo_inicial=Decimal('0'))
        hoy = timezone.now()

        def crear_venta(estado, moneda, tipo_cambio, total, sesion_caja=None):
            return Venta.objects.create(
                cliente=self.cliente, sucursal=self.sucursal, estado=estado, moneda=moneda,
                tipo_cambio=Decimal(tipo_cambio), fecha_emision=hoy, sesion_caja=sesion_caja,
                subtotal=Decimal(total), igv=Decimal('0'), total=Decimal(total),
            )

        self.venta_pagada_pen = crear_venta(Venta.Estado.PAGADA, Venta.Moneda.PEN, '1.0000', '100.00', self.sesion_caja)
        self.venta_pagada_usd = crear_venta(Venta.Estado.PAGADA, Venta.Moneda.USD, '3.7500', '10.00')
        self.venta_anulada = crear_venta(Venta.Estado.ANULADA, Venta.Moneda.PEN, '1.0000', '500.00')
        self.venta_pre_venta = crear_venta(Venta.Estado.PRE_VENTA, Venta.Moneda.PEN, '1.0000', '999.00')

    def test_excluye_ventas_anuladas_y_pre_venta(self):
        resp = self.client_api.get('/api/reportes/ventas/')
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {d['id'] for d in resp.data['data']}
        self.assertEqual(ids, {self.venta_pagada_pen.id, self.venta_pagada_usd.id})

    def test_totales_convierten_a_soles_usando_tipo_cambio(self):
        resp = self.client_api.get('/api/reportes/ventas/')
        self.assertEqual(resp.status_code, 200, resp.content)
        # 100.00 (PEN, tc=1.0000) + 10.00*3.7500 (USD) = 137.50
        self.assertAlmostEqual(resp.data['totales']['total'], 137.50, places=2)

    def test_vendedor_se_arma_con_nombres_del_usuario(self):
        """
        Bug real encontrado de paso: se usaba `usuario.nombre` (no existe en
        el modelo Usuario, el campo real es `nombres`), lo que reventaba con
        500 apenas una venta tenía sesión de caja asignada.
        """
        resp = self.client_api.get('/api/reportes/ventas/')
        self.assertEqual(resp.status_code, 200, resp.content)
        fila = next(d for d in resp.data['data'] if d['id'] == self.venta_pagada_pen.id)
        self.assertEqual(fila['vendedor'], f"{self.admin.nombres} {self.admin.apellidos}")


class ReporteComprasTests(TestCase):
    """
    Cubre el bug real: ni el Reporte de Compras ni el sub-reporte
    'compras_detalladas' del Reporte Avanzado excluían compras con
    estado='Anulada' — una compra anulada seguía sumando al reporte.
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_reportes_compras_test', email='admin_reportes_compras_test@example.com',
            nombres='Admin', apellidos='ReportesTest', password='x',
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

        self.proveedor = Proveedor.objects.create(numero_documento='20000000001', nombre_o_razon_social='Proveedor RPT')
        categoria = Categoria.objects.create(nombre='Categoria Compras RPT')
        marca = MarcaRepuesto.objects.create(nombre='Marca Compras RPT')
        self.repuesto = Repuesto.objects.create(
            codigo='RPT-COMPRA', nombre='Repuesto Compra', categoria=categoria, marca=marca,
            precio_compra=Decimal('10.00'), precio_por_mayor=Decimal('12.00'),
            precio_cash=Decimal('15.00'), precio_lista=Decimal('18.00'),
        )
        hoy = timezone.now().date()

        def crear_compra(estado, total):
            compra = Compra.objects.create(
                proveedor=self.proveedor, fecha_emision=hoy, serie='F001', numero_comprobante='1',
                subtotal=Decimal(total), igv=Decimal('0'), total=Decimal(total), estado=estado,
                usuario=self.admin,
            )
            DetalleCompra.objects.create(
                compra=compra, repuesto=self.repuesto, cantidad=Decimal('1'),
                precio_unitario=Decimal(total), subtotal=Decimal(total),
            )
            return compra

        self.compra_completada = crear_compra('Completada', '80.00')
        self.compra_anulada = crear_compra('Anulada', '999.00')

    def test_reporte_compras_excluye_anuladas(self):
        resp = self.client_api.get('/api/reportes/compras/')
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {d['id'] for d in resp.data['data']}
        self.assertEqual(ids, {self.compra_completada.id})
        self.assertAlmostEqual(resp.data['totales']['total'], 80.00, places=2)

    def test_avanzado_compras_detalladas_excluye_anuladas(self):
        resp = self.client_api.get('/api/reportes/avanzado/', {'tipo': 'compras_detalladas'})
        self.assertEqual(resp.status_code, 200, resp.content)
        comprobantes = [d['comprobante'] for d in resp.data['data']]
        self.assertEqual(len(comprobantes), 1)


class ReporteCajaTests(TestCase):
    """
    Cubre el bug real: el Reporte de Caja sumaba TODOS los movimientos de
    ingreso sin filtrar por estado_movimiento, así que un movimiento
    manual pendiente de aprobación (o rechazado) inflaba el efectivo
    mostrado en el cierre. También era el único reporte del módulo sin
    paginación (traía todas las sesiones siempre).
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_reportes_caja_test', email='admin_reportes_caja_test@example.com',
            nombres='Admin', apellidos='ReportesTest', password='x',
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

        sucursal = Sucursal.objects.create(nombre='Sucursal Caja RPT')
        caja = Caja.objects.create(sucursal=sucursal, nombre='Caja RPT')
        self.metodo_efectivo = MetodoPago.objects.create(nombre='Efectivo')
        self.sesion = SesionCaja.objects.create(caja=caja, usuario=self.admin, saldo_inicial=Decimal('0'))

        def crear_movimiento(monto, estado_movimiento):
            return MovimientoCaja.objects.create(
                sesion=self.sesion, tipo=MovimientoCaja.Tipo.INGRESO, concepto=MovimientoCaja.Concepto.OTROS_INGRESOS,
                metodo_pago=self.metodo_efectivo, monto=Decimal(monto), estado_movimiento=estado_movimiento,
                creado_por=self.admin,
            )

        self.mov_aprobado = crear_movimiento('100.00', MovimientoCaja.EstadoMovimiento.APROBADO)
        self.mov_pendiente = crear_movimiento('500.00', MovimientoCaja.EstadoMovimiento.PENDIENTE)
        self.mov_rechazado = crear_movimiento('900.00', MovimientoCaja.EstadoMovimiento.RECHAZADO)

    def test_solo_suma_movimientos_aprobados(self):
        resp = self.client_api.get('/api/reportes/caja/')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['total'], 1)
        sesion_data = resp.data['data'][0]
        self.assertEqual(sesion_data['id_sesion'], self.sesion.id)
        self.assertAlmostEqual(sesion_data['efectivo'], 100.00, places=2)

    def test_respuesta_esta_paginada(self):
        resp = self.client_api.get('/api/reportes/caja/', {'page_size': 1})
        self.assertEqual(resp.status_code, 200, resp.content)
        for campo in ('total', 'page', 'page_size', 'total_pages'):
            self.assertIn(campo, resp.data)


class ReporteAvanzadoOrdenesServicioTests(TestCase):
    """
    Cubre el bug real: el sub-reporte 'ordenes_servicio' del Reporte
    Avanzado no excluía órdenes con estado CANCELADO, a diferencia de sus
    sub-reportes hermanos de ventas (que sí excluyen ANULADA).
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_reportes_ot_test', email='admin_reportes_ot_test@example.com',
            nombres='Admin', apellidos='ReportesTest', password='x',
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

        self.vehiculo = Vehiculo.objects.create(placa='RPT-001', marca='Toyota', modelo='Yaris')
        self.cliente = Cliente.objects.create(dni='50000001', nombres='Cliente', apellidos='Taller')

        def crear_orden(numero, estado):
            return OrdenTrabajo.objects.create(
                numero=numero, cliente=self.cliente, vehiculo=self.vehiculo,
                recepcionista=self.admin, estado=estado,
            )

        self.orden_activa = crear_orden('OT-RPT-1', OrdenTrabajo.Estado.FINALIZADO)
        self.orden_cancelada = crear_orden('OT-RPT-2', OrdenTrabajo.Estado.CANCELADO)

    def test_excluye_ordenes_canceladas(self):
        resp = self.client_api.get('/api/reportes/avanzado/', {'tipo': 'ordenes_servicio'})
        self.assertEqual(resp.status_code, 200, resp.content)
        numeros = {d['numero'] for d in resp.data['data']}
        self.assertEqual(numeros, {self.orden_activa.numero})


class ParseDateRangeTests(TestCase):
    """
    Cubre el bug real: si se enviaba fecha_inicio/fecha_fin con formato
    inválido, _parse_date_range lo ignoraba en silencio y usaba el mes
    actual — el usuario nunca se enteraba de que su filtro fue descartado.
    Ahora debe rechazar la petición con 400 (solo usa el default cuando
    el parámetro no se envía).
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_reportes_fechas_test', email='admin_reportes_fechas_test@example.com',
            nombres='Admin', apellidos='ReportesTest', password='x',
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

    def test_fecha_invalida_enviada_se_rechaza_con_400(self):
        resp = self.client_api.get('/api/reportes/ventas/', {'fecha_inicio': '31-13-2026'})
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_sin_fechas_usa_default_del_mes_actual(self):
        resp = self.client_api.get('/api/reportes/ventas/')
        self.assertEqual(resp.status_code, 200, resp.content)


class ReporteVehiculosTests(TestCase):
    """
    Cubre 2 bugs reales encontrados de paso mientras se corregía el N+1:
    - Se leía `vehiculo.anio` (no existe en el modelo, el campo real es
      `anio_fabricacion`), lo que reventaba con 500 en cuanto había algún
      vehículo en el resultado.
    - `ordenes.order_by(...).first()` y `ordenes.count()` re-encadenaban
      sobre la relación ya prefetcheada, dañando el prefetch_related y
      generando 2 queries extra por cada vehículo del listado.
    """

    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_reportes_vehiculos_test', email='admin_reportes_vehiculos_test@example.com',
            nombres='Admin', apellidos='ReportesTest', password='x',
        )
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.admin)

        self.cliente = Cliente.objects.create(dni='60000001', nombres='Cliente', apellidos='Vehiculo')
        self.vehiculo1 = Vehiculo.objects.create(placa='VRP-001', marca='Toyota', modelo='Yaris', anio_fabricacion=2020)
        self.vehiculo2 = Vehiculo.objects.create(placa='VRP-002', marca='Kia', modelo='Rio', anio_fabricacion=2021)

        OrdenTrabajo.objects.create(
            numero='OT-VRP-1', cliente=self.cliente, vehiculo=self.vehiculo1,
            recepcionista=self.admin, estado='FINALIZADO',
        )
        OrdenTrabajo.objects.create(
            numero='OT-VRP-2', cliente=self.cliente, vehiculo=self.vehiculo1,
            recepcionista=self.admin, estado='FACTURADO',
        )

    def test_no_revienta_y_devuelve_anio_fabricacion(self):
        resp = self.client_api.get('/api/reportes/vehiculos/')
        self.assertEqual(resp.status_code, 200, resp.content)
        por_placa = {d['placa']: d for d in resp.data['data']}
        self.assertEqual(por_placa['VRP-001']['anio'], 2020)
        self.assertEqual(por_placa['VRP-001']['total_ordenes'], 2)
        self.assertEqual(por_placa['VRP-002']['anio'], 2021)
        self.assertEqual(por_placa['VRP-002']['total_ordenes'], 0)

    def test_no_hace_queries_extra_por_vehiculo(self):
        # count + select vehiculos + prefetch ordenes_trabajo + prefetch cliente:
        # 4 queries fijas sin importar cuántos vehículos/órdenes haya (antes:
        # +2 queries por cada vehículo vía .order_by()/.count() re-encadenados).
        with self.assertNumQueries(4):
            resp = self.client_api.get('/api/reportes/vehiculos/')
        self.assertEqual(resp.status_code, 200, resp.content)
