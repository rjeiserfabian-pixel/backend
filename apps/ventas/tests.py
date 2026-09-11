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
