import uuid
from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.avisos.servicios import generar_avisos
from apps.clientes.models import Cliente
from apps.facturacion.models import ComprobanteElectronico
from apps.facturacion.services import FacturacionService
from apps.inventario.models import Sucursal, MovimientoInventario
from apps.seguridad.models import Usuario
from apps.taller.models import OrdenTrabajo
from apps.vehiculos.models import Vehiculo
from .models import Caja, CuentaPorCobrar, CuotaCredito, MetodoPago, MovimientoCaja, PagoCuota, SesionCaja, Venta

BASE = '/api/ventas/cuentas-por-cobrar/'


class CreditosAnterioresTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(username='creditos', email='creditos@example.com', nombres='Admin', apellidos='Prueba', password='x')
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.cliente = Cliente.objects.create(dni='12345678', nombres='Omega')
        self.sucursal = Sucursal.objects.create(nombre='Principal')
        self.vehiculo = Vehiculo.objects.create(placa='ABC-123', marca='Toyota', modelo='Hilux')
        self.vehiculo.clientes.add(self.cliente)
        self.metodo = MetodoPago.objects.create(nombre='Efectivo')
        self.billetera = MetodoPago.objects.create(nombre='Billetera', requiere_referencia=True)
        self.sesion = SesionCaja.objects.create(caja=Caja.objects.create(sucursal=self.sucursal, nombre='Caja'), usuario=self.admin)
        self.hoy = timezone.localdate()

    def payload(self, **extra):
        data = {'cliente': self.cliente.pk, 'vehiculo': self.vehiculo.pk, 'sucursal': self.sucursal.pk,
                'sistema_origen': '  anterior ', 'documento_origen': ' f001-10 ', 'concepto': 'Reparación anterior',
                'fecha_original': str(self.hoy - timedelta(days=100)), 'fecha_corte': str(self.hoy),
                'importe_original': '1500.00', 'abonos_anteriores': '500.00',
                'detalles': [{'tipo': 'SERVICIO', 'descripcion': 'Mano de obra', 'cantidad': '1', 'precio_unitario': '600'},
                             {'tipo': 'REPUESTO', 'descripcion': 'Freno anterior', 'cantidad': '2', 'precio_unitario': '450', 'codigo_original': 'X-OLD'}],
                'cronograma': [{'monto': '1000', 'fecha_vencimiento': str(self.hoy - timedelta(days=30))}],
                'solicitud_id': str(uuid.uuid4())}
        data.update(extra)
        return data

    def crear(self, **extra):
        response = self.api.post(BASE + 'registrar-anterior/', self.payload(**extra), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return CuentaPorCobrar.objects.get(pk=response.data['id'])

    def pagar(self, cuenta, monto='300', **extra):
        data = {'pagos': [{'metodo_pago_id': self.metodo.pk, 'monto': monto}], **extra}
        return self.api.post(BASE + f'pagar-cuota/{cuenta.cuotas.first().pk}/', data, format='json')

    def test_alta_mixta_no_procesa_operaciones_anteriores(self):
        cuenta = self.crear()
        self.assertIsNone(cuenta.venta_id)
        self.assertEqual(cuenta.documento_origen, 'F001-10')
        self.assertEqual(cuenta.placa_original, 'ABC-123')
        self.assertEqual(cuenta.saldo_pendiente, Decimal('1000'))
        self.assertEqual(cuenta.detalles_anteriores.count(), 2)
        for model in (Venta, OrdenTrabajo, ComprobanteElectronico, MovimientoInventario, MovimientoCaja, PagoCuota):
            self.assertEqual(model.objects.count(), 0, model.__name__)

    def test_solo_repuestos_sin_catalogo_ni_vehiculo(self):
        cuenta = self.crear(vehiculo=None, sin_vehiculo=True, detalles=[{'tipo': 'REPUESTO', 'descripcion': 'Artículo descontinuado', 'cantidad': '1', 'precio_unitario': '1500'}])
        self.assertIsNone(cuenta.vehiculo_id)
        self.assertIsNone(cuenta.detalles_anteriores.first().repuesto_id)

    def test_solo_servicios(self):
        self.crear(detalles=[{'tipo': 'SERVICIO', 'descripcion': 'Reparación', 'cantidad': '1', 'precio_unitario': '1500'}])

    def test_sin_desglose_con_sustento(self):
        self.crear(detalles=[], sin_detalle=True, observaciones='Saldo confirmado según documento externo.')

    def test_validaciones_no_dejan_datos(self):
        casos = [dict(abonos_anteriores='1500'), dict(importe_original='1499'), dict(vehiculo=None), dict(sin_vehiculo=True, vehiculo=None), dict(sin_detalle=True, detalles=[]), dict(moneda='USD'), dict(fecha_corte=str(self.hoy + timedelta(days=1))), dict(cronograma=[{'monto': '999', 'fecha_vencimiento': str(self.hoy)}]), dict(detalles=[{'tipo': 'REPUESTO', 'descripcion': 'Error', 'cantidad': '-1', 'precio_unitario': '1500'}])]
        for caso in casos:
            with self.subTest(caso=caso):
                r = self.api.post(BASE + 'registrar-anterior/', self.payload(**caso), format='json')
                self.assertEqual(r.status_code, 400, r.data)
                self.assertEqual(CuentaPorCobrar.objects.count(), 0)

    def test_placa_de_otro_cliente_se_rechaza(self):
        self.vehiculo.clientes.clear()
        self.assertEqual(self.api.post(BASE + 'registrar-anterior/', self.payload(), format='json').status_code, 400)

    def test_ajuste_explicito_respeta_importe_original(self):
        self.crear(ajuste_documento='-100', motivo_ajuste='Descuento del documento', importe_original='1400', abonos_anteriores='400')

    def test_documento_duplicado_y_reintento(self):
        data = self.payload()
        a = self.api.post(BASE + 'registrar-anterior/', data, format='json')
        b = self.api.post(BASE + 'registrar-anterior/', data, format='json')
        self.assertEqual((a.status_code, b.status_code), (201, 200))
        self.assertEqual(a.data['id'], b.data['id'])
        data['solicitud_id'] = str(uuid.uuid4())
        data['documento_origen'] = 'F001-10'
        self.assertEqual(self.api.post(BASE + 'registrar-anterior/', data, format='json').status_code, 400)
        self.assertEqual(CuentaPorCobrar.objects.count(), 1)

    def test_pago_parcial_total_snapshot_y_reintento(self):
        cuenta = self.crear()
        token = str(uuid.uuid4())
        r = self.pagar(cuenta, solicitud_id=token)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['cuenta']['saldo_pendiente'], '700.00')
        self.assertEqual(r.data['pagos'][0]['recibo_datos']['cuota_estado'], 'PARCIAL')
        self.assertEqual(self.pagar(cuenta, solicitud_id=token).status_code, 200)
        self.assertEqual(PagoCuota.objects.count(), 1)
        self.assertEqual(self.pagar(cuenta, monto='700', solicitud_id=str(uuid.uuid4())).status_code, 200)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, 'PAGADO')
        self.assertEqual(PagoCuota.objects.order_by('pk').first().recibo_datos['saldo_pendiente'], '700.00')
        self.assertEqual(MovimientoCaja.objects.aggregate(s=Sum('monto'))['s'], Decimal('1000'))
        self.assertEqual(MovimientoInventario.objects.count(), 0)

    def test_pago_mixto_y_validacion_de_referencia(self):
        cuenta = self.crear()
        url = BASE + f'pagar-cuota/{cuenta.cuotas.first().pk}/'
        pagos = [{'metodo_pago_id': self.metodo.pk, 'monto': '100'}, {'metodo_pago_id': self.billetera.pk, 'monto': '200'}]
        self.assertEqual(self.api.post(url, {'pagos': pagos}, format='json').status_code, 400)
        self.assertEqual(MovimientoCaja.objects.count(), 0)
        pagos[1]['referencia'] = 'OP-123'
        r = self.api.post(url, {'pagos': pagos}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len({p['operacion_id'] for p in r.data['pagos']}), 1)

    def test_pago_invalido_sin_cambios(self):
        cuenta = self.crear()
        for monto in ('0', '-1', 'NaN', 'Infinity', '1001', '0.001'):
            self.assertEqual(self.pagar(cuenta, monto=monto).status_code, 400)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo_pendiente, Decimal('1000'))
        self.assertEqual(PagoCuota.objects.count(), 0)

    def test_pago_sin_caja_abierta(self):
        cuenta = self.crear()
        self.sesion.estado = 'CERRADA'
        self.sesion.save()
        self.assertEqual(self.pagar(cuenta).status_code, 400)

    def test_resumen_no_duplica_cuentas_con_cuotas_ni_importes_iguales(self):
        cronograma = [{'monto': '500', 'fecha_vencimiento': str(self.hoy - timedelta(days=1))}] * 2
        self.crear(cronograma=cronograma)
        self.crear(documento_origen='OTRO', cronograma=cronograma)
        r = self.api.get(BASE + 'resumen-clientes/')
        self.assertEqual(r.status_code, 200, r.data)
        row = r.data['results'][0]
        self.assertEqual(row['total_deuda'], Decimal('2000'))
        self.assertEqual(row['tiene_atrasos'], 2)

    def test_paginacion_totales_y_moneda_separada(self):
        for i in range(11):
            self.crear(documento_origen=f'DOC-{i}')
        venta = Venta.objects.create(cliente=self.cliente, sucursal=self.sucursal, moneda='USD', total=10)
        CuentaPorCobrar.objects.create(venta=venta, codigo_credito='USD-1', frecuencia_pago='MENSUAL', monto_financiado=10, saldo_pendiente=10)
        r = self.api.get(BASE, {'cliente_id': self.cliente.pk})
        self.assertEqual(r.data['count'], 12)
        self.assertEqual(len(r.data['results']), 10)
        self.assertEqual({t['moneda']: t['saldo_pendiente'] for t in r.data['totales_por_moneda']}, {'PEN': Decimal('11000'), 'USD': Decimal('10')})
        self.assertEqual(len(self.api.get(BASE, {'cliente_id': self.cliente.pk, 'page': 2}).data['results']), 2)

    def test_vencidas_avisos_y_anulacion_sin_stock(self):
        cuenta = self.crear()
        self.assertEqual(self.api.get(BASE + 'cuotas-vencidas/', {'sucursal_id': self.sucursal.pk}).data['total'], 1)
        generar_avisos()
        r = self.api.post(BASE + f'{cuenta.pk}/anular-anterior/', {'motivo': 'Duplicado en fuente'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.api.get(BASE + 'cuotas-vencidas/').data['total'], 0)
        self.assertEqual(self.api.get(BASE + 'resumen-clientes/').data['count'], 0)
        self.assertEqual(self.pagar(cuenta).status_code, 400)
        generar_avisos()
        self.assertEqual(MovimientoInventario.objects.count(), 0)

    def test_corregir_antes_y_despues_del_cobro(self):
        cuenta = self.crear()
        data = self.payload(documento_origen='CORREGIDO', motivo='Referencia incorrecta')
        r = self.api.post(BASE + f'{cuenta.pk}/editar-anterior/', data, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.pagar(cuenta)
        self.assertEqual(self.api.post(BASE + f'{cuenta.pk}/editar-anterior/', data, format='json').status_code, 400)
        self.assertEqual(self.api.post(BASE + f'{cuenta.pk}/editar-anterior/', {'concepto': 'Detalle aclarado', 'motivo': 'Aclaración'}, format='json').status_code, 200)
        self.assertEqual(self.api.post(BASE + f'{cuenta.pk}/anular-anterior/', {'motivo': 'Error'}, format='json').status_code, 400)

    def test_reporte_cliente_incluye_saldo_sin_compras(self):
        self.crear()
        r = self.api.get('/api/reportes/clientes/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['data'][0]['saldo_pendiente'], 1000)
        self.assertEqual(r.data['data'][0]['total_comprado'], 0)

    def test_completar_desglose_despues_de_cobrar_no_cambia_saldos(self):
        cuenta = self.crear(sin_detalle=True, detalles=[], observaciones='Documento resumido')
        self.pagar(cuenta)
        r = self.api.post(BASE + f'{cuenta.pk}/editar-anterior/', {'motivo': 'Se recibió el detalle', 'detalles': self.payload()['detalles']}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        cuenta.refresh_from_db()
        self.assertFalse(cuenta.sin_detalle)
        self.assertEqual(cuenta.detalles_anteriores.count(), 2)
        self.assertEqual(cuenta.saldo_pendiente, Decimal('700'))
        self.assertEqual(PagoCuota.objects.count(), 1)

    def test_reintento_alta_con_importe_distinto_no_reutiliza_solicitud(self):
        data = self.payload()
        self.assertEqual(self.api.post(BASE + 'registrar-anterior/', data, format='json').status_code, 201)
        data['importe_original'] = '2000'
        self.assertEqual(self.api.post(BASE + 'registrar-anterior/', data, format='json').status_code, 409)

    def test_reintento_pago_mixto_no_duplica_movimientos(self):
        cuenta = self.crear()
        payload = {'solicitud_id': str(uuid.uuid4()), 'pagos': [{'metodo_pago_id': self.metodo.pk, 'monto': '100'}, {'metodo_pago_id': self.billetera.pk, 'monto': '200', 'referencia': '123'}]}
        url = BASE + f'pagar-cuota/{cuenta.cuotas.first().pk}/'
        self.assertEqual(self.api.post(url, payload, format='json').status_code, 200)
        r = self.api.post(url, payload, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data['pagos']), 2)
        self.assertEqual(MovimientoCaja.objects.count(), 2)

    def test_cuenta_de_venta_y_nota_credito_conservadas(self):
        venta = Venta.objects.create(cliente=self.cliente, sucursal=self.sucursal, vehiculo=self.vehiculo, total=100)
        cuenta = CuentaPorCobrar.objects.create(venta=venta, codigo_credito='V-1', frecuencia_pago='MENSUAL', monto_financiado=100, saldo_pendiente=100)
        cuota = CuotaCredito.objects.create(cuenta_cobrar=cuenta, numero_cuota=1, monto=100, saldo_pendiente=100, fecha_vencimiento=self.hoy)
        self.assertEqual(cuenta.cliente_id, self.cliente.pk)
        from django.db import transaction
        with transaction.atomic():
            FacturacionService._reducir_cuenta_por_cobrar(venta, Decimal('20'))
        cuota.refresh_from_db(); cuenta.refresh_from_db()
        self.assertEqual(cuota.saldo_pendiente, Decimal('80'))
        self.assertEqual(cuenta.saldo_pendiente, Decimal('80'))

    def test_permisos_y_escrituras_genericas(self):
        cuenta = self.crear()
        self.assertEqual(self.api.patch(BASE + f'{cuenta.pk}/', {'saldo_pendiente': 0}, format='json').status_code, 405)
        self.assertEqual(self.api.delete(BASE + f'{cuenta.pk}/').status_code, 405)
        user = Usuario.objects.create_user(username='sin_permiso', email='sin@example.com', nombres='Sin', apellidos='Permiso', password='x')
        self.api.force_authenticate(user)
        self.assertEqual(self.api.post(BASE + 'registrar-anterior/', self.payload(documento_origen='SIN'), format='json').status_code, 403)
        self.assertEqual(self.pagar(cuenta).status_code, 403)


from django.db import connection, close_old_connections, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, skipUnlessDBFeature


class MigracionCreditosTests(TransactionTestCase):
    def test_copia_cliente_placa_moneda_sin_recalcular_saldos(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('ventas', '0022_metodopago_qr')])
        old = executor.loader.project_state([('ventas', '0022_metodopago_qr')]).apps
        cliente = old.get_model('clientes', 'Cliente').objects.create(dni='98765432', nombres='Migración')
        sucursal = old.get_model('inventario', 'Sucursal').objects.create(nombre='Migración')
        vehiculo = old.get_model('vehiculos', 'Vehiculo').objects.create(placa='MIG-123', marca='Marca', modelo='Modelo')
        venta = old.get_model('ventas', 'Venta').objects.create(cliente=cliente, sucursal=sucursal, vehiculo=vehiculo, moneda='USD', total='100.00')
        cuenta = old.get_model('ventas', 'CuentaPorCobrar').objects.create(venta=venta, codigo_credito='LEGADO-1', frecuencia_pago='MENSUAL', monto_financiado='100.00', saldo_pendiente='75.00')
        cuota = old.get_model('ventas', 'CuotaCredito').objects.create(cuenta_cobrar=cuenta, numero_cuota=1, monto='100.00', saldo_pendiente='75.00', estado='PARCIAL', fecha_vencimiento=timezone.localdate())
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            actual = CuentaPorCobrar.objects.get(pk=cuenta.pk)
            self.assertEqual((actual.cliente_id, actual.sucursal_id, actual.vehiculo_id, actual.moneda, actual.placa_original), (cliente.pk, sucursal.pk, vehiculo.pk, 'USD', 'MIG-123'))
            self.assertEqual(actual.saldo_pendiente, Decimal('75'))
            self.assertEqual(actual.monto_financiado, Decimal('100'))
            self.assertEqual(actual.creado_en, cuenta.creado_en)
            self.assertEqual(actual.cuotas.get().pk, cuota.pk)
            self.assertEqual(actual.cuotas.get().saldo_pendiente, Decimal('75'))
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())


class ConcurrenciaCreditosTests(TransactionTestCase):
    setUp = CreditosAnterioresTests.setUp
    payload = CreditosAnterioresTests.payload
    crear = CreditosAnterioresTests.crear

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_cuotas_cobradas_simultaneamente_actualizan_el_saldo_padre(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        cuenta = self.crear(cronograma=[{'monto': '500', 'fecha_vencimiento': str(self.hoy)}] * 2)
        cuotas = list(cuenta.cuotas.order_by('pk'))
        barrera = Barrier(2)

        def cobrar(cuota):
            close_old_connections()
            try:
                api = APIClient()
                api.force_authenticate(self.admin)
                barrera.wait(timeout=10)
                return api.post(BASE + f'pagar-cuota/{cuota.pk}/', {'pagos': [{'metodo_pago_id': self.metodo.pk, 'monto': '300'}], 'solicitud_id': str(uuid.uuid4())}, format='json').status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(cobrar, cuotas))
        self.assertEqual(resultados, [200, 200])
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo_pendiente, Decimal('400'))
        self.assertEqual(sum(cuenta.cuotas.values_list('saldo_pendiente', flat=True)), Decimal('400'))
        self.assertEqual(MovimientoCaja.objects.aggregate(s=Sum('monto'))['s'], Decimal('600'))
