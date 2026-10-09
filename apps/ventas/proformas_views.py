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

from .models import Venta, DetalleVenta, Proforma, ProformaDetalle
from .serializers import VentaSerializer, ProformaSerializer
from apps.seguridad.permissions import TienePermiso
from apps.seguridad.models import CuentaBancaria
from apps.seguridad.pdf_utils import contexto_empresa_pdf

from .paginacion import VentaPagination

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
        if self.action == 'convertir_a_pos':
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
            'cliente', 'sucursal', 'creado_por', 'venta_generada'
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
                Q(cliente__ruc__icontains=search)
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
        proforma = Proforma.objects.select_for_update().select_related('cliente', 'sucursal').prefetch_related('detalles').get(pk=pk)
        if proforma.estado == Proforma.Estado.CONVERTIDA and proforma.venta_generada:
            return Response(VentaSerializer(proforma.venta_generada).data)
        if proforma.estado == Proforma.Estado.ANULADA:
            return Response({"error": "No se puede convertir una proforma anulada."}, status=status.HTTP_400_BAD_REQUEST)
        if not proforma.detalles.exists():
            return Response({"error": "La proforma no tiene items para convertir."}, status=status.HTTP_400_BAD_REQUEST)

        venta = Venta.objects.create(
            cliente=proforma.cliente,
            sucursal=proforma.sucursal,
            estado=Venta.Estado.PRE_VENTA,
            ticket_kiosko=f"PROF-{str(uuid.uuid4())[:6].upper()}",
            moneda=proforma.moneda,
            tipo_cambio=proforma.tipo_cambio,
            subtotal=proforma.subtotal,
            igv=proforma.igv,
            total=proforma.total,
        )

        for detalle in proforma.detalles.all():
            DetalleVenta.objects.create(
                venta=venta,
                repuesto=detalle.repuesto if detalle.tipo == ProformaDetalle.Tipo.REPUESTO else None,
                descripcion_servicio=detalle.descripcion if detalle.tipo == ProformaDetalle.Tipo.SERVICIO else None,
                cantidad=detalle.cantidad,
                precio_unitario=detalle.precio_unitario,
                descuento=detalle.descuento,
                costo_unitario=detalle.repuesto.precio_compra if detalle.repuesto else None,
                subtotal_linea=detalle.subtotal_linea,
            )

        proforma.estado = Proforma.Estado.CONVERTIDA
        proforma.venta_generada = venta
        proforma.convertido_en = timezone.now()
        proforma.save(update_fields=['estado', 'venta_generada', 'convertido_en', 'actualizado_en'])

        return Response(VentaSerializer(venta).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='pdf')
    def generar_pdf(self, request, pk=None):
        proforma = self.get_object()
        cuentas = CuentaBancaria.objects.filter(estado=True).select_related('tipo_cuenta')
        context = {
            **contexto_empresa_pdf(),
            'proforma': proforma,
            'cuentas_bancarias': cuentas,
            'numero_documento': proforma.numero,
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
