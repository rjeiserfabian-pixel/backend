"""
Red de seguridad de rendimiento: el NÚMERO de consultas SQL de un listado no debe crecer con la
cantidad de filas (señal clásica de consultas repetidas "N+1"). Se compara el listado con pocas
y con muchas filas: debe hacer exactamente las mismas consultas.
"""
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente, Proveedor
from apps.compras.models import Compra, DetalleCompra
from apps.inventario.models import Categoria, MarcaRepuesto, Repuesto, Sucursal
from apps.seguridad.models import Usuario
from apps.taller.models import Hallazgo, OrdenHistorialEstado, OrdenRepuesto, OrdenServicio, OrdenTrabajo
from apps.vehiculos.models import Vehiculo
from apps.ventas.models import (
    Caja, DetalleVenta, MetodoPago, MovimientoCaja, PagoVenta, SesionCaja, Venta,
)

POCAS, MUCHAS = 3, 12


class ConsultasNoCrecenConLasFilasTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            username='admin_rendimiento_test', email='admin_rendimiento_test@example.com',
            nombres='Admin', apellidos='Rendimiento', password='x',
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.admin)
        self.sucursal = Sucursal.objects.create(nombre='Sucursal Rendimiento')
        self.cliente = Cliente.objects.create(dni='74000001', nombres='Cliente', apellidos='Rendimiento')
        self.proveedor = Proveedor.objects.create(numero_documento='20999999991', nombre_o_razon_social='Prov Rendimiento')
        categoria = Categoria.objects.create(nombre='Cat Rendimiento')
        marca = MarcaRepuesto.objects.create(nombre='Marca Rendimiento')
        self.repuestos = [
            Repuesto.objects.create(
                codigo=f'RND-{i}', nombre=f'Repuesto {i}', categoria=categoria, marca=marca,
                precio_compra=Decimal('5.00'), precio_por_mayor=Decimal('8.00'),
                precio_cash=Decimal('9.00'), precio_lista=Decimal('10.00'),
            ) for i in range(4)
        ]
        caja = Caja.objects.create(sucursal=self.sucursal, nombre='Caja Rendimiento')
        self.sesion = SesionCaja.objects.create(caja=caja, usuario=self.admin, saldo_inicial=Decimal('0'))
        self.metodo = MetodoPago.objects.create(nombre='Efectivo Rendimiento')
        self.contador = 0

    # --- generadores de datos ---
    def _venta(self):
        self.contador += 1
        vehiculo = Vehiculo.objects.create(placa=f'RN-{self.contador:04d}', marca='Toyota', modelo='Yaris')
        venta = Venta.objects.create(
            cliente=self.cliente, vehiculo=vehiculo, sucursal=self.sucursal, sesion_caja=self.sesion,
            estado=Venta.Estado.PAGADA, serie_correlativo=f'B001-{self.contador:08d}', total=Decimal('30.00'),
            fecha_emision=timezone.now(),
        )
        for repuesto in self.repuestos[:3]:
            DetalleVenta.objects.create(venta=venta, repuesto=repuesto, cantidad=Decimal('1'),
                                        precio_unitario=Decimal('10'), subtotal_linea=Decimal('10'))
        DetalleVenta.objects.create(venta=venta, descripcion_servicio='Mano de obra', cantidad=Decimal('1'),
                                    precio_unitario=Decimal('20'), subtotal_linea=Decimal('20'))
        for _ in range(2):
            mov = MovimientoCaja.objects.create(
                sesion=self.sesion, tipo='INGRESO', concepto='VENTA', metodo_pago=self.metodo,
                monto=Decimal('25'), venta_origen=venta, creado_por=self.admin,
            )
            PagoVenta.objects.create(venta=venta, movimiento_caja=mov, monto=Decimal('25'))

    def _compra(self):
        self.contador += 1
        compra = Compra.objects.create(
            proveedor=self.proveedor, fecha_emision=timezone.localdate(), serie='F001',
            numero_comprobante=str(self.contador), subtotal=Decimal('10'), total=Decimal('10'), usuario=self.admin,
        )
        for repuesto in self.repuestos[:3]:
            DetalleCompra.objects.create(compra=compra, repuesto=repuesto, cantidad=Decimal('2'),
                                         precio_unitario=Decimal('5'), subtotal=Decimal('10'))

    def _orden(self):
        self.contador += 1
        vehiculo = Vehiculo.objects.create(placa=f'OT-{self.contador:04d}', marca='Kia', modelo='Rio')
        orden = OrdenTrabajo.objects.create(
            numero=f'OT-R{self.contador}', cliente=self.cliente, vehiculo=vehiculo, recepcionista=self.admin,
            mecanico_asignado=self.admin,
        )
        for i in range(2):
            OrdenServicio.objects.create(orden=orden, descripcion=f'Servicio {i}', precio_estimado=Decimal('50'))
            OrdenRepuesto.objects.create(orden=orden, repuesto=self.repuestos[i], cantidad=Decimal('1'),
                                         precio_unitario=Decimal('10'))
        Hallazgo.objects.create(orden=orden, descripcion='Fuga', registrado_por=self.admin)
        OrdenHistorialEstado.objects.create(orden=orden, estado='RECEPCIONADO', usuario=self.admin)

    # --- medición ---
    def _consultas(self, url):
        with CaptureQueriesContext(connection) as capturadas:
            resp = self.api.get(url)
        self.assertEqual(resp.status_code, 200, f'{url}: {getattr(resp, "data", resp.content)}')
        return len(capturadas)

    def _comprobar(self, nombre, crear, url):
        for _ in range(POCAS):
            crear()
        con_pocas = self._consultas(url)
        for _ in range(MUCHAS - POCAS):
            crear()
        con_muchas = self._consultas(url)
        self.assertEqual(
            con_pocas, con_muchas,
            f'{nombre}: con {POCAS} filas hace {con_pocas} consultas y con {MUCHAS} hace {con_muchas} '
            f'(consultas repetidas por fila).',
        )

    def test_listado_de_ventas(self):
        self._comprobar('Ventas', self._venta, '/api/ventas/transacciones/?page_size=100')

    def test_listado_de_compras(self):
        self._comprobar('Compras', self._compra, '/api/compras/compras/?page_size=100')

    def test_listado_de_ordenes_de_trabajo(self):
        self._comprobar('Órdenes de trabajo', self._orden, '/api/taller/ordenes/?page_size=100')
