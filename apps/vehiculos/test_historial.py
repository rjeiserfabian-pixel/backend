from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from django.template.defaultfilters import floatformat
from pypdf import PdfReader
from rest_framework.test import APIClient

from apps.inventario.models import Categoria, MarcaRepuesto, Repuesto
from apps.seguridad.models import Usuario
from apps.taller.models import OrdenRepuesto, OrdenServicio, OrdenTrabajo
from apps.vehiculos.models import Vehiculo, VehiculoQR


class HistorialTrabajosAprobadosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = Usuario.objects.create_superuser(
            username='admin_historial_test', email='historial@example.com',
            nombres='Admin', apellidos='Historial', password='x',
        )
        cls.vehiculo = Vehiculo.objects.create(placa='HIS-001', marca='KIA', modelo='Sportage')
        cls.qr = VehiculoQR.objects.create(vehiculo=cls.vehiculo, creado_por=cls.usuario)
        cls.orden = OrdenTrabajo.objects.create(
            numero='HIS-001', vehiculo=cls.vehiculo, recepcionista=cls.usuario,
            estado=OrdenTrabajo.Estado.FACTURADO,
        )
        for descripcion in ('Filtro de aire autorizado', 'Revision de motor autorizada'):
            OrdenServicio.objects.create(
                orden=cls.orden, descripcion=descripcion,
                precio_estimado=Decimal('20.00'), aprobado_cliente=True, completado=True,
            )
        cls.propuesta = OrdenServicio.objects.create(
            orden=cls.orden, descripcion='Filtro de aceite sin aprobar',
            precio_estimado=Decimal('10.00'), aprobado_cliente=False,
        )
        for numero in ('HIS-002', 'HIS-003'):
            OrdenTrabajo.objects.create(
                numero=numero, vehiculo=cls.vehiculo, recepcionista=cls.usuario,
                estado=OrdenTrabajo.Estado.FACTURADO,
            )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.usuario)
        self.url = f'/api/vehiculos/{self.vehiculo.pk}/historial/'

    def orden_del_historial(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_ordenes'], 3)
        return next(item for item in response.data['ordenes'] if item['numero'] == self.orden.numero)

    def test_historial_coincide_con_qr_sin_borrar_propuestas_ni_visitas(self):
        orden = self.orden_del_historial()
        self.assertEqual(len(orden['servicios']), 2)
        self.assertEqual(orden['total_servicios'], Decimal('40.00'))
        self.assertEqual(orden['total_general'], Decimal('40.00'))
        qr = self.client.get(f'/api/vehiculos/public/qr/{self.qr.token_publico}/')
        self.assertEqual(qr.status_code, 200)
        orden_qr = next(item for item in qr.data['historial_ingresos']['results'] if item['numero'] == self.orden.numero)
        self.assertEqual(orden['servicios'], [
            {**item, 'precio_estimado': Decimal('20.00')} for item in orden_qr['servicios']
        ])
        self.propuesta.refresh_from_db()
        self.assertFalse(self.propuesta.aprobado_cliente)
        self.assertEqual(self.orden.servicios.count(), 3)
        self.assertEqual(self.vehiculo.ordenes_trabajo.count(), 3)

    def test_repuestos_solo_aprobados_y_pendientes_autorizados_se_conservan(self):
        categoria = Categoria.objects.create(nombre='Historial')
        marca = MarcaRepuesto.objects.create(nombre='Historial')
        repuesto = Repuesto.objects.create(
            codigo='HIS-REP', nombre='Repuesto historial', categoria=categoria, marca=marca,
            precio_compra=5, precio_por_mayor=8, precio_cash=9, precio_lista=10,
        )
        OrdenRepuesto.objects.create(
            orden=self.orden, repuesto=repuesto, cantidad=2,
            precio_unitario=Decimal('10.00'), aprobado_cliente=True,
        )
        OrdenRepuesto.objects.create(
            orden=self.orden, repuesto=repuesto, cantidad=3,
            precio_unitario=Decimal('10.00'), aprobado_cliente=False,
        )
        OrdenServicio.objects.create(
            orden=self.orden, descripcion='Autorizado pendiente',
            precio_estimado=Decimal('5.00'), aprobado_cliente=True, completado=False,
        )
        orden = self.orden_del_historial()
        self.assertEqual(len(orden['repuestos']), 1)
        self.assertFalse(orden['repuestos'][0]['instalado'])
        self.assertEqual(orden['total_repuestos'], Decimal('20.00'))
        self.assertEqual(orden['total_general'], Decimal('65.00'))
        pendiente = next(item for item in orden['servicios'] if item['descripcion'] == 'Autorizado pendiente')
        self.assertFalse(pendiente['completado'])
        self.assertEqual(self.orden.repuestos.count(), 2)

    def test_visita_sin_aprobaciones_permanece_con_total_cero(self):
        self.orden.servicios.update(aprobado_cliente=False)
        orden = self.orden_del_historial()
        self.assertEqual(orden['servicios'], [])
        self.assertEqual(orden['repuestos'], [])
        self.assertEqual(orden['total_general'], Decimal('0'))

    def test_pdf_excluye_propuesta_y_muestra_total_autorizado(self):
        response = self.client.get(f'{self.url}pdf/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        contenido = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
        self.assertNotIn('Filtro de aceite sin aprobar', contenido)
        self.assertIn('Filtro de aire autorizado', contenido)
        self.assertIn('Revision de motor autorizada', contenido)
        self.assertIn(f'S/ {floatformat(Decimal("40.00"), 2)}', contenido)
        self.assertNotIn(f'S/ {floatformat(Decimal("50.00"), 2)}', contenido)
