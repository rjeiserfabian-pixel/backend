from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from apps.seguridad.models import Usuario, Rol, Modulo, Permiso, RolPermiso, UsuarioRol
from apps.inventario.models import Sucursal, Categoria, MarcaRepuesto, Repuesto
from apps.clientes.models import Cliente
from apps.vehiculos.models import Vehiculo
from apps.ventas.models import Impuesto, Proforma, ProformaDetalle
from apps.ventas.proformas_views import ProformaViewSet
from apps.taller.models import OrdenTrabajo


class ConversionProformaAOtTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = ProformaViewSet.as_view({'post': 'convertir_a_ot'})
        self.view_pos = ProformaViewSet.as_view({'post': 'convertir_a_pos'})

        self.user = Usuario.objects.create_superuser(
            username='admin_ot_test',
            email='admin_ot@test.com',
            password='secretpassword123',
            nombres='Admin',
            apellidos='OT Test'
        )
        # Asignar permiso VENTAS.PROFORMAS.CONVERTIR
        modulo = Modulo.objects.create(codigo='VENTAS', nombre='Ventas')
        permiso = Permiso.objects.create(id_modulo=modulo, nombre='Convertir Proforma', codigo='VENTAS.PROFORMAS.CONVERTIR', accion='CONVERTIR')
        rol = Rol.objects.create(nombre='Admin Rol', codigo='ADMIN_ROL', estado=True)
        RolPermiso.objects.create(id_rol=rol, id_permiso=permiso)
        UsuarioRol.objects.create(id_usuario=self.user, id_rol=rol, estado=True)

        self.sucursal = Sucursal.objects.create(nombre='Taller Central', direccion='Av. Taller 123')
        self.cliente = Cliente.objects.create(nombres='Carlos', apellidos='Mendoza', dni='87654321')
        self.vehiculo = Vehiculo.objects.create(placa='ABC-123', marca='Toyota', modelo='Corolla', anio_fabricacion=2020)
        self.categoria = Categoria.objects.create(nombre='Frenos')
        self.marca = MarcaRepuesto.objects.create(nombre='Ferodo')
        self.impuesto_igv = Impuesto.objects.create(nombre='IGV', tasa=Decimal('18.00'), estado=True)

        self.repuesto = Repuesto.objects.create(
            codigo='REP-FRENO',
            nombre='Pastillas de Freno',
            categoria=self.categoria,
            marca=self.marca,
            precio_compra=Decimal('60.00'),
            precio_lista=Decimal('120.00'),
            precio_cash=Decimal('120.00'),
            precio_por_mayor=Decimal('120.00'),
            tipo_igv=self.impuesto_igv
        )

    def _crear_proforma(self, con_vehiculo=True, estado=Proforma.Estado.EMITIDA):
        proforma = Proforma.objects.create(
            numero=f'PROF-TEST-{Proforma.objects.count() + 1}',
            cliente=self.cliente,
            vehiculo=self.vehiculo if con_vehiculo else None,
            kilometraje=15000 if con_vehiculo else None,
            sucursal=self.sucursal,
            creado_por=self.user,
            estado=estado,
            total=Decimal('220.00')
        )
        ProformaDetalle.objects.create(
            proforma=proforma,
            tipo=ProformaDetalle.Tipo.SERVICIO,
            descripcion='Alineación y balanceo',
            cantidad=1,
            precio_unitario=Decimal('100.00'),
            subtotal_linea=Decimal('100.00')
        )
        ProformaDetalle.objects.create(
            proforma=proforma,
            tipo=ProformaDetalle.Tipo.REPUESTO,
            repuesto=self.repuesto,
            cantidad=1,
            precio_unitario=Decimal('120.00'),
            subtotal_linea=Decimal('120.00')
        )
        return proforma

    def test_conversion_exitosa_a_orden_trabajo(self):
        """Conversión exitosa: genera OT en RECEPCIONADO, traspasa servicios/repuestos aprobados y actualiza estado."""
        proforma = self._crear_proforma(con_vehiculo=True)

        request = self.factory.post(f'/api/ventas/proformas/{proforma.id}/convertir-a-ot/')
        force_authenticate(request, user=self.user)
        response = self.view(request, pk=proforma.id)

        self.assertEqual(response.status_code, 201)
        proforma.refresh_from_db()
        self.assertEqual(proforma.estado, Proforma.Estado.CONVERTIDA_OT)
        self.assertIsNotNone(proforma.orden_trabajo_generada)

        ot = proforma.orden_trabajo_generada
        self.assertEqual(ot.estado, OrdenTrabajo.Estado.RECEPCIONADO)
        self.assertEqual(ot.cliente, self.cliente)
        self.assertEqual(ot.vehiculo, self.vehiculo)
        self.assertEqual(ot.kilometraje_ingreso, 15000)

        # Servicios y repuestos transferidos y aprobados
        self.assertEqual(ot.servicios.count(), 1)
        servicio = ot.servicios.first()
        self.assertEqual(servicio.descripcion, 'Alineación y balanceo')
        self.assertTrue(servicio.aprobado_cliente)

        self.assertEqual(ot.repuestos.count(), 1)
        repuesto_ot = ot.repuestos.first()
        self.assertEqual(repuesto_ot.repuesto, self.repuesto)
        self.assertEqual(repuesto_ot.cantidad, Decimal('1.00'))
        self.assertTrue(repuesto_ot.aprobado_cliente)

    def test_rechazo_conversion_sin_vehiculo(self):
        """Si la proforma no tiene vehículo asignado, debe rechazar con HTTP 400 y mensaje explicativo."""
        proforma = self._crear_proforma(con_vehiculo=False)

        request = self.factory.post(f'/api/ventas/proformas/{proforma.id}/convertir-a-ot/')
        force_authenticate(request, user=self.user)
        response = self.view(request, pk=proforma.id)

        self.assertEqual(response.status_code, 400)
        self.assertIn('no tiene un vehículo asignado', response.data['error'])

    def test_exclusion_mutua_pos_bloqueado_si_convertida_a_ot(self):
        """Si la proforma ya fue convertida a OT, no puede convertirse a POS."""
        proforma = self._crear_proforma(con_vehiculo=True)

        # 1. Convertir a OT
        req_ot = self.factory.post(f'/api/ventas/proformas/{proforma.id}/convertir-a-ot/')
        force_authenticate(req_ot, user=self.user)
        res_ot = self.view(req_ot, pk=proforma.id)
        self.assertEqual(res_ot.status_code, 201)

        # 2. Intentar convertir a POS
        req_pos = self.factory.post(f'/api/ventas/proformas/{proforma.id}/convertir-a-pos/')
        force_authenticate(req_pos, user=self.user)
        res_pos = self.view_pos(req_pos, pk=proforma.id)

        self.assertEqual(res_pos.status_code, 400)
        self.assertIn('ya fue convertida a Orden de Trabajo', res_pos.data['error'])

    def test_exclusion_mutua_ot_bloqueado_si_convertida_a_pos(self):
        """Si la proforma ya fue convertida a POS, no puede convertirse a OT."""
        proforma = self._crear_proforma(con_vehiculo=True)

        # 1. Convertir a POS
        req_pos = self.factory.post(f'/api/ventas/proformas/{proforma.id}/convertir-a-pos/')
        force_authenticate(req_pos, user=self.user)
        res_pos = self.view_pos(req_pos, pk=proforma.id)
        self.assertEqual(res_pos.status_code, 201)

        # 2. Intentar convertir a OT
        req_ot = self.factory.post(f'/api/ventas/proformas/{proforma.id}/convertir-a-ot/')
        force_authenticate(req_ot, user=self.user)
        res_ot = self.view(req_ot, pk=proforma.id)

        self.assertEqual(res_ot.status_code, 400)
        self.assertIn('ya fue convertida a Venta/POS', res_ot.data['error'])
