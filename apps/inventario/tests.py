from decimal import Decimal

from django.test import TestCase

from apps.seguridad.models import Usuario
from apps.inventario.models import (
    Categoria, MarcaRepuesto, Repuesto, Sucursal, Almacen, UbicacionFisica, MovimientoInventario
)


class MovimientoInventarioStrTests(TestCase):
    """
    Cubre el bug real: __str__ usaba un formato de entero ({:+d}) sobre un
    campo Decimal, y truena apenas la cantidad es fraccionaria (ej. 5.5
    litros de aceite en el Kiosco) — o incluso con cantidades enteras, porque
    Decimal no soporta el presentador 'd' en absoluto.
    """

    def setUp(self):
        self.usuario = Usuario.objects.create_superuser(
            username='admin_inventario_test', email='admin_inventario_test@example.com',
            nombres='Admin', apellidos='InventarioTest', password='x',
        )
        categoria = Categoria.objects.create(nombre='Categoria Inventario Test')
        marca = MarcaRepuesto.objects.create(nombre='Marca Inventario Test')
        self.repuesto = Repuesto.objects.create(
            codigo='REP-INV-TEST', nombre='Aceite de prueba',
            categoria=categoria, marca=marca,
            precio_compra=Decimal('5.00'), precio_por_mayor=Decimal('8.00'),
            precio_cash=Decimal('9.00'), precio_lista=Decimal('10.00'),
        )
        sucursal = Sucursal.objects.create(nombre='Sucursal Inventario Test')
        almacen = Almacen.objects.create(sucursal=sucursal, nombre='Almacen Inventario Test')
        self.ubicacion = UbicacionFisica.objects.create(almacen=almacen, codigo='GENERAL')

    def _crear_movimiento(self, cantidad, tipo_movimiento=MovimientoInventario.TipoMovimiento.ENTRADA):
        return MovimientoInventario.objects.create(
            repuesto=self.repuesto,
            ubicacion=self.ubicacion,
            tipo_movimiento=tipo_movimiento,
            cantidad=cantidad,
            stock_resultante=cantidad,
            motivo='Prueba de str',
            usuario=self.usuario,
        )

    def test_str_con_cantidad_fraccionaria_no_lanza_excepcion(self):
        movimiento = self._crear_movimiento(Decimal('5.50'))
        texto = str(movimiento)
        self.assertIn('+5.50', texto)
        self.assertIn(self.repuesto.codigo, texto)

    def test_str_con_cantidad_negativa_fraccionaria(self):
        movimiento = self._crear_movimiento(Decimal('-2.75'), tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA)
        texto = str(movimiento)
        self.assertIn('-2.75', texto)

    def test_str_con_cantidad_entera_no_lanza_excepcion(self):
        # Antes tampoco funcionaba con enteros: Decimal no soporta el formato 'd'.
        movimiento = self._crear_movimiento(Decimal('10.00'))
        texto = str(movimiento)
        self.assertIn('+10.00', texto)
