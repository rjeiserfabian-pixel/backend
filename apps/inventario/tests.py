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


class ReposicionStockTests(TestCase):
    """Reposición: repuestos bajo el mínimo, con sugerido a 2x el mínimo y sin falsos positivos."""

    def setUp(self):
        from rest_framework.test import APIClient
        from apps.inventario.models import InventarioStock
        self.InventarioStock = InventarioStock
        self.admin = Usuario.objects.create_superuser(
            username='admin_reposicion_test', email='admin_reposicion_test@example.com',
            nombres='Admin', apellidos='ReposicionTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.categoria = Categoria.objects.create(nombre='Cat Reposicion Test')
        self.marca = MarcaRepuesto.objects.create(nombre='Marca Reposicion Test')
        self.sucursal = Sucursal.objects.create(nombre='Sucursal Reposicion Test')
        self.almacen = Almacen.objects.create(sucursal=self.sucursal, nombre='Almacen Reposicion Test')
        self.ubicacion = UbicacionFisica.objects.create(almacen=self.almacen, codigo='GENERAL')

    def _repuesto(self, codigo, disponible, minimo):
        repuesto = Repuesto.objects.create(
            codigo=codigo, nombre=f'Repuesto {codigo}', categoria=self.categoria, marca=self.marca,
            precio_compra=Decimal('10.00'), precio_por_mayor=Decimal('12.00'),
            precio_cash=Decimal('14.00'), precio_lista=Decimal('15.00'),
        )
        self.InventarioStock.objects.create(
            repuesto=repuesto, ubicacion=self.ubicacion,
            stock_disponible=Decimal(disponible), stock_minimo=Decimal(minimo),
        )
        return repuesto

    def _get(self, **params):
        return self.client.get('/api/inventario/reposicion/', params)

    def test_lista_solo_los_que_estan_en_o_bajo_el_minimo(self):
        self._repuesto('R-BAJO', '2', '5')
        self._repuesto('R-AGOTADO', '0', '5')
        self._repuesto('R-OK', '10', '5')
        self._repuesto('R-SIN-MINIMO', '0', '0')

        resp = self._get()

        self.assertEqual(resp.status_code, 200, resp.data)
        codigos = {f['codigo']: f for f in resp.data['results']}
        self.assertEqual(set(codigos), {'R-BAJO', 'R-AGOTADO'})
        self.assertEqual(codigos['R-BAJO']['nivel'], 'BAJO')
        self.assertEqual(codigos['R-AGOTADO']['nivel'], 'AGOTADO')

    def test_sugerido_lleva_a_dos_veces_el_minimo_y_calcula_costo(self):
        self._repuesto('R-BAJO', '2', '5')

        fila = self._get().data['results'][0]

        self.assertEqual(Decimal(str(fila['sugerido'])), Decimal('8.00'))
        self.assertEqual(Decimal(str(fila['costo_estimado'])), Decimal('80.00'))
        resumen = self._get().data['resumen']
        self.assertEqual(resumen['total_alertas'], 1)
        self.assertEqual(resumen['agotados'], 0)

    def test_agotados_primero_y_filtro_solo_agotados(self):
        self._repuesto('R-BAJO', '4', '5')
        self._repuesto('R-AGOTADO', '0', '5')

        todos = [f['codigo'] for f in self._get().data['results']]
        self.assertEqual(todos[0], 'R-AGOTADO')

        solo = [f['codigo'] for f in self._get(solo_agotados=1).data['results']]
        self.assertEqual(solo, ['R-AGOTADO'])

    def test_usuario_sin_permiso_recibe_403(self):
        from rest_framework.test import APIClient
        sin_permiso = Usuario.objects.create_user(
            username='sin_permiso_reposicion', email='sin_permiso_reposicion@example.com',
            nombres='Sin', apellidos='Permiso', password='x',
        )
        cliente = APIClient()
        cliente.force_authenticate(user=sin_permiso)
        self.assertEqual(cliente.get('/api/inventario/reposicion/').status_code, 403)
