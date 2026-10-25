from decimal import Decimal
from django.test import TestCase
from apps.seguridad.models import Usuario
from apps.inventario.models import Sucursal, Categoria, MarcaRepuesto, Repuesto
from apps.clientes.models import Cliente
from apps.ventas.models import Impuesto, Proforma, ProformaDetalle
from apps.ventas.serializers import ProformaSerializer


class ProformaTributacionTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_superuser(
            username='admin_test',
            email='admin@test.com',
            password='secretpassword123',
            nombres='Admin',
            apellidos='Test'
        )
        self.sucursal = Sucursal.objects.create(nombre='Central', direccion='Av. Principal 123')
        self.cliente = Cliente.objects.create(nombres='Juan', apellidos='Perez', dni='12345678')
        self.categoria = Categoria.objects.create(nombre='Filtros', margen_minimo_esperado=Decimal('20.00'))
        self.marca = MarcaRepuesto.objects.create(nombre='Bosch')
        self.impuesto_igv = Impuesto.objects.create(nombre='IGV', tasa=Decimal('18.00'), estado=True)

        self.repuesto = Repuesto.objects.create(
            codigo='REP-001',
            nombre='Filtro de Aceite',
            categoria=self.categoria,
            marca=self.marca,
            precio_compra=Decimal('50.00'),
            precio_lista=Decimal('118.00'),
            precio_cash=Decimal('118.00'),
            precio_por_mayor=Decimal('118.00'),
            tipo_igv=self.impuesto_igv
        )

    def test_caso_1_repuesto_precio_con_igv(self):
        """Caso 1: Repuesto con precio de venta que incluye IGV (118.00 -> Base 100.00, IGV 18.00, Total 118.00)"""
        data = {
            'cliente': self.cliente.id,
            'sucursal': self.sucursal.id,
            'forma_pago': 'CONTADO',
            'incluye_igv': True,
            'moneda': 'PEN',
            'tipo_cambio': 1.0,
            'detalles': [{
                'tipo': 'REPUESTO',
                'repuesto': self.repuesto.id,
                'cantidad': 1,
                'precio_unitario': 118.00,
                'descuento': 0,
                'tipo_afectacion': 'GRAVADO_18',
                'precio_incluye_igv': True,
            }]
        }
        serializer = ProformaSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        proforma = serializer.save(creado_por=self.user)

        self.assertEqual(proforma.total_gravado, Decimal('100.00'))
        self.assertEqual(proforma.total_exonerado, Decimal('0.00'))
        self.assertEqual(proforma.subtotal, Decimal('100.00'))
        self.assertEqual(proforma.igv, Decimal('18.00'))
        self.assertEqual(proforma.total, Decimal('118.00'))

    def test_caso_2_repuesto_precio_sin_igv(self):
        """Caso 2: Repuesto con precio sin IGV gravado (200.00 -> Base 200.00, IGV 36.00, Total 236.00)"""
        data = {
            'cliente': self.cliente.id,
            'sucursal': self.sucursal.id,
            'forma_pago': 'CONTADO',
            'incluye_igv': False,
            'moneda': 'PEN',
            'tipo_cambio': 1.0,
            'detalles': [{
                'tipo': 'REPUESTO',
                'repuesto': self.repuesto.id,
                'cantidad': 1,
                'precio_unitario': 200.00,
                'descuento': 0,
                'tipo_afectacion': 'GRAVADO_18',
                'precio_incluye_igv': False,
            }]
        }
        serializer = ProformaSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        proforma = serializer.save(creado_por=self.user)

        self.assertEqual(proforma.total_gravado, Decimal('200.00'))
        self.assertEqual(proforma.subtotal, Decimal('200.00'))
        self.assertEqual(proforma.igv, Decimal('36.00'))
        self.assertEqual(proforma.total, Decimal('236.00'))

    def test_caso_3_repuesto_exonerado(self):
        """Caso 3: Repuesto/Insumo exonerado (150.00 -> Base 150.00, IGV 0.00, Total 150.00)"""
        data = {
            'cliente': self.cliente.id,
            'sucursal': self.sucursal.id,
            'forma_pago': 'CONTADO',
            'incluye_igv': True,
            'moneda': 'PEN',
            'tipo_cambio': 1.0,
            'detalles': [{
                'tipo': 'REPUESTO',
                'repuesto': self.repuesto.id,
                'cantidad': 1,
                'precio_unitario': 150.00,
                'descuento': 0,
                'tipo_afectacion': 'EXONERADO',
                'precio_incluye_igv': True,
            }]
        }
        serializer = ProformaSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        proforma = serializer.save(creado_por=self.user)

        self.assertEqual(proforma.total_gravado, Decimal('0.00'))
        self.assertEqual(proforma.total_exonerado, Decimal('150.00'))
        self.assertEqual(proforma.subtotal, Decimal('150.00'))
        self.assertEqual(proforma.igv, Decimal('0.00'))
        self.assertEqual(proforma.total, Decimal('150.00'))

    def test_caso_4_servicio_con_igv(self):
        """Caso 4: Servicio con precio que incluye IGV (150.00 -> Base 127.12, IGV 22.88, Total 150.00)"""
        data = {
            'cliente': self.cliente.id,
            'sucursal': self.sucursal.id,
            'forma_pago': 'CONTADO',
            'incluye_igv': True,
            'moneda': 'PEN',
            'tipo_cambio': 1.0,
            'detalles': [{
                'tipo': 'SERVICIO',
                'descripcion': 'Mantenimiento Preventivo 10K',
                'cantidad': 1,
                'precio_unitario': 150.00,
                'descuento': 0,
                'tipo_afectacion': 'GRAVADO_18',
                'precio_incluye_igv': True,
            }]
        }
        serializer = ProformaSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        proforma = serializer.save(creado_por=self.user)

        self.assertEqual(proforma.total_gravado, Decimal('127.12'))
        self.assertEqual(proforma.subtotal, Decimal('127.12'))
        self.assertEqual(proforma.igv, Decimal('22.88'))
        self.assertEqual(proforma.total, Decimal('150.00'))

    def test_caso_5_servicio_sin_igv(self):
        """Caso 5: Servicio con precio neto sin IGV (500.00 -> Base 500.00, IGV 90.00, Total 590.00)"""
        data = {
            'cliente': self.cliente.id,
            'sucursal': self.sucursal.id,
            'forma_pago': 'CONTADO',
            'incluye_igv': False,
            'moneda': 'PEN',
            'tipo_cambio': 1.0,
            'detalles': [{
                'tipo': 'SERVICIO',
                'descripcion': 'Mano de obra bajada de motor',
                'cantidad': 1,
                'precio_unitario': 500.00,
                'descuento': 0,
                'tipo_afectacion': 'GRAVADO_18',
                'precio_incluye_igv': False,
            }]
        }
        serializer = ProformaSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        proforma = serializer.save(creado_por=self.user)

        self.assertEqual(proforma.total_gravado, Decimal('500.00'))
        self.assertEqual(proforma.subtotal, Decimal('500.00'))
        self.assertEqual(proforma.igv, Decimal('90.00'))
        self.assertEqual(proforma.total, Decimal('590.00'))

    def test_caso_6_proforma_mixta(self):
        """
        Caso 6: Proforma mixta:
        - Repuesto gravado con IGV: 118.00 (Base 100.00, IGV 18.00)
        - Servicio gravado sin IGV: 100.00 (Base 100.00, IGV 18.00)
        - Insumo exonerado: 50.00 (Base 50.00, IGV 0.00)
        Esperado:
        Gravadas: 200.00, Exoneradas: 50.00, IGV: 36.00, Total: 286.00
        """
        data = {
            'cliente': self.cliente.id,
            'sucursal': self.sucursal.id,
            'forma_pago': 'CONTADO',
            'incluye_igv': True,
            'moneda': 'PEN',
            'tipo_cambio': 1.0,
            'detalles': [
                {
                    'tipo': 'REPUESTO',
                    'repuesto': self.repuesto.id,
                    'cantidad': 1,
                    'precio_unitario': 118.00,
                    'descuento': 0,
                    'tipo_afectacion': 'GRAVADO_18',
                    'precio_incluye_igv': True,
                },
                {
                    'tipo': 'SERVICIO',
                    'descripcion': 'Servicio especializado',
                    'cantidad': 1,
                    'precio_unitario': 100.00,
                    'descuento': 0,
                    'tipo_afectacion': 'GRAVADO_18',
                    'precio_incluye_igv': False,
                },
                {
                    'tipo': 'SERVICIO',
                    'descripcion': 'Insumo exonerado',
                    'cantidad': 1,
                    'precio_unitario': 50.00,
                    'descuento': 0,
                    'tipo_afectacion': 'EXONERADO',
                    'precio_incluye_igv': True,
                }
            ]
        }
        serializer = ProformaSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        proforma = serializer.save(creado_por=self.user)

        self.assertEqual(proforma.total_gravado, Decimal('200.00'))
        self.assertEqual(proforma.total_exonerado, Decimal('50.00'))
        self.assertEqual(proforma.subtotal, Decimal('250.00'))
        self.assertEqual(proforma.igv, Decimal('36.00'))
        self.assertEqual(proforma.total, Decimal('286.00'))
