import logging
import uuid
from io import BytesIO
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action

from .models import Venta, DetalleVenta, Proforma, ProformaDetalle, Impuesto
from .serializers import VentaSerializer, ProformaSerializer
from apps.seguridad.permissions import TienePermiso
from apps.seguridad.models import CuentaBancaria
from apps.seguridad.pdf_utils import contexto_empresa_pdf

from .paginacion import VentaPagination

from apps.taller.models import OrdenTrabajo, OrdenServicio, OrdenRepuesto, OrdenHistorialEstado
from apps.taller.serializers import OrdenTrabajoDetailSerializer
from apps.inventario.models import InventarioStock, MovimientoInventario

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# MANTENIMIENTO (CONFIGURACIONES)
# ──────────────────────────────────────────────


class ProformaViewSet(viewsets.ModelViewSet):
    queryset = Proforma.objects.all().order_by('-creado_en')
    serializer_class = ProformaSerializer
    pagination_class = VentaPagination

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def get_permissions(self):
        if self.action in ['convertir_a_pos', 'convertir_a_ot']:
            return [TienePermiso("VENTAS.PROFORMAS.CONVERTIR")]
        if self.request.method == 'GET':
            return [TienePermiso("VENTAS.PROFORMAS.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("VENTAS.PROFORMAS.CREAR")]
        if self.request.method in ['PUT', 'PATCH']:
            return [TienePermiso("VENTAS.PROFORMAS.EDITAR")]
        return [TienePermiso("VENTAS.PROFORMAS.ELIMINAR")]

    def get_queryset(self):
        qs = super().get_queryset().select_related(
            'cliente', 'vehiculo', 'sucursal', 'creado_por', 'venta_generada', 'orden_trabajo_generada'
        ).prefetch_related('detalles__repuesto__unidad_medida')

        sucursal = self.request.query_params.get('sucursal')
        if sucursal:
            qs = qs.filter(sucursal_id=sucursal)

        estado = self.request.query_params.get('estado')
        if estado:
            qs = qs.filter(estado=estado)

        search = (self.request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(numero__icontains=search) |
                Q(cliente__nombres__icontains=search) |
                Q(cliente__apellidos__icontains=search) |
                Q(cliente__dni__icontains=search) |
                Q(cliente__ruc__icontains=search) |
                Q(vehiculo__placa__icontains=search)
            )

        fecha_desde = self.request.query_params.get('fecha_desde')
        if fecha_desde:
            qs = qs.filter(creado_en__date__gte=fecha_desde)
        fecha_hasta = self.request.query_params.get('fecha_hasta')
        if fecha_hasta:
            qs = qs.filter(creado_en__date__lte=fecha_hasta)

        return qs.distinct()

    @action(detail=True, methods=['post'], url_path='convertir-a-pos')
    @transaction.atomic
    def convertir_a_pos(self, request, pk=None):
        proforma = Proforma.objects.select_for_update(of=('self',)).select_related('cliente', 'vehiculo', 'sucursal', 'venta_generada').prefetch_related('detalles').get(pk=pk)
        if proforma.estado == Proforma.Estado.CONVERTIDA and proforma.venta_generada:
            return Response(VentaSerializer(proforma.venta_generada).data)
        if proforma.estado == Proforma.Estado.CONVERTIDA_OT:
            return Response({"error": "Esta proforma ya fue convertida a Orden de Trabajo y no puede convertirse a POS."}, status=status.HTTP_400_BAD_REQUEST)
        if proforma.estado == Proforma.Estado.ANULADA:
            return Response({"error": "No se puede convertir una proforma anulada."}, status=status.HTTP_400_BAD_REQUEST)
        if not proforma.detalles.exists():
            return Response({"error": "La proforma no tiene items para convertir."}, status=status.HTTP_400_BAD_REQUEST)

        venta = Venta.objects.create(
            cliente=proforma.cliente,
            vehiculo=proforma.vehiculo,
            kilometraje=proforma.kilometraje,
            sucursal=proforma.sucursal,
            estado=Venta.Estado.PRE_VENTA,
            ticket_kiosko=f"PROF-{str(uuid.uuid4())[:6].upper()}",
            moneda=proforma.moneda,
            tipo_cambio=proforma.tipo_cambio,
            subtotal=proforma.subtotal,
            igv=proforma.igv,
            total=proforma.total,
        )

        impuesto_igv = Impuesto.objects.filter(estado=True, nombre__iexact='IGV').first()
        if not impuesto_igv:
            impuesto_igv = Impuesto.objects.filter(estado=True, tasa=18.00).first()

        for detalle in proforma.detalles.all():
            es_gravado = detalle.tipo_afectacion == ProformaDetalle.TipoAfectacion.GRAVADO_18
            DetalleVenta.objects.create(
                venta=venta,
                repuesto=detalle.repuesto if detalle.tipo == ProformaDetalle.Tipo.REPUESTO else None,
                descripcion_servicio=detalle.descripcion if detalle.tipo == ProformaDetalle.Tipo.SERVICIO else None,
                cantidad=detalle.cantidad,
                precio_unitario=detalle.precio_unitario,
                descuento=detalle.descuento,
                costo_unitario=detalle.repuesto.precio_compra if detalle.repuesto else None,
                impuesto_aplicado=impuesto_igv if es_gravado else None,
                monto_impuesto=detalle.igv_linea,
                subtotal_linea=detalle.subtotal_linea,
            )

        proforma.estado = Proforma.Estado.CONVERTIDA
        proforma.venta_generada = venta
        proforma.convertido_en = timezone.now()
        proforma.save(update_fields=['estado', 'venta_generada', 'convertido_en', 'actualizado_en'])

        return Response(VentaSerializer(venta).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='convertir-a-ot')
    @transaction.atomic
    def convertir_a_ot(self, request, pk=None):
        proforma = Proforma.objects.select_for_update(of=('self',)).select_related(
            'cliente', 'vehiculo', 'sucursal', 'orden_trabajo_generada'
        ).prefetch_related('detalles__repuesto').get(pk=pk)

        if proforma.estado == Proforma.Estado.CONVERTIDA_OT and proforma.orden_trabajo_generada:
            return Response(OrdenTrabajoDetailSerializer(proforma.orden_trabajo_generada).data)

        if proforma.estado == Proforma.Estado.CONVERTIDA:
            return Response({"error": "Esta proforma ya fue convertida a Venta/POS y no puede convertirse a Orden de Trabajo."}, status=status.HTTP_400_BAD_REQUEST)

        if proforma.estado == Proforma.Estado.ANULADA:
            return Response({"error": "No se puede convertir una proforma anulada."}, status=status.HTTP_400_BAD_REQUEST)

        if not proforma.vehiculo:
            return Response({
                "error": "Esta proforma no tiene un vehículo asignado. Para crear una Orden de Trabajo en taller, primero edita la proforma y asigna el vehículo del cliente."
            }, status=status.HTTP_400_BAD_REQUEST)

        if not proforma.detalles.exists():
            return Response({"error": "La proforma no tiene items para convertir."}, status=status.HTTP_400_BAD_REQUEST)

        # Generar número correlativo único de 6 dígitos
        last_ot = OrdenTrabajo.objects.order_by('-id').first()
        next_num = 1 if not last_ot else last_ot.id + 1
        numero_ot = f"{next_num:06d}"

        motivo_texto = f"Orden creada a partir de la Proforma {proforma.numero}"
        if proforma.observaciones:
            motivo_texto += f"\nObservaciones: {proforma.observaciones}"

        orden = OrdenTrabajo.objects.create(
            numero=numero_ot,
            cliente=proforma.cliente,
            vehiculo=proforma.vehiculo,
            recepcionista=request.user,
            estado=OrdenTrabajo.Estado.RECEPCIONADO,
            kilometraje_ingreso=proforma.kilometraje,
            motivo_ingreso=motivo_texto,
        )

        if orden.cliente:
            orden.vehiculo.clientes.add(orden.cliente)

        if orden.kilometraje_ingreso is not None:
            vehiculo = orden.vehiculo
            if not vehiculo.kilometraje_actual or orden.kilometraje_ingreso > vehiculo.kilometraje_actual:
                vehiculo.kilometraje_actual = orden.kilometraje_ingreso
                vehiculo.save(update_fields=['kilometraje_actual'])

        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=OrdenTrabajo.Estado.RECEPCIONADO,
            usuario=request.user,
            observaciones=f"Orden creada a partir de la Proforma {proforma.numero}"
        )

        for detalle in proforma.detalles.all():
            if detalle.tipo == ProformaDetalle.Tipo.SERVICIO:
                OrdenServicio.objects.create(
                    orden=orden,
                    descripcion=detalle.descripcion,
                    precio_estimado=detalle.precio_unitario,
                    aprobado_cliente=True,
                )
            elif detalle.tipo == ProformaDetalle.Tipo.REPUESTO and detalle.repuesto:
                OrdenRepuesto.objects.create(
                    orden=orden,
                    repuesto=detalle.repuesto,
                    cantidad=detalle.cantidad,
                    precio_unitario=detalle.precio_unitario,
                    aprobado_cliente=True,
                )
                stock_record = InventarioStock.objects.select_for_update().filter(
                    repuesto=detalle.repuesto, stock_disponible__gte=detalle.cantidad
                ).first()
                if stock_record:
                    stock_record.stock_disponible -= detalle.cantidad
                    stock_record.stock_reservado += detalle.cantidad
                    stock_record.save()
                    MovimientoInventario.objects.create(
                        repuesto=detalle.repuesto,
                        ubicacion=stock_record.ubicacion,
                        tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                        cantidad=-detalle.cantidad,
                        stock_resultante=stock_record.stock_disponible,
                        motivo=f"Reserva por conversión de Proforma {proforma.numero} a OT-{orden.numero}",
                        usuario=request.user,
                        referencia_id=orden.id,
                        referencia_tipo="OT",
                    )

        proforma.estado = Proforma.Estado.CONVERTIDA_OT
        proforma.orden_trabajo_generada = orden
        proforma.convertido_en = timezone.now()
        proforma.save(update_fields=['estado', 'orden_trabajo_generada', 'convertido_en', 'actualizado_en'])

        return Response(OrdenTrabajoDetailSerializer(orden).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='pdf')
    def generar_pdf(self, request, pk=None):
        proforma = self.get_object()
        cuentas = CuentaBancaria.objects.filter(estado=True).select_related('tipo_cuenta')
        moneda_simbolo = 'S/' if proforma.moneda == 'PEN' else ('$' if proforma.moneda == 'USD' else '€')
        context = {
            **contexto_empresa_pdf(),
            'proforma': proforma,
            'cuentas_bancarias': cuentas,
            'numero_documento': proforma.numero,
            'moneda_simbolo': moneda_simbolo,
        }
        html_string = render_to_string('ventas/proforma_pdf.html', context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="proforma_{proforma.numero}.pdf"'

        from xhtml2pdf import pisa
        pdf_buffer = BytesIO()
        pisa_status = pisa.CreatePDF(html_string, dest=pdf_buffer)
        if pisa_status.err:
            return Response({"error": "No se pudo generar el PDF."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        response.write(pdf_buffer.getvalue())
        return response
