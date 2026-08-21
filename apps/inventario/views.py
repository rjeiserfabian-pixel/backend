import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.db.models import Q, Prefetch
from .models import (
    Categoria, MarcaRepuesto, Repuesto, AplicacionRepuesto,
    Sucursal, Almacen, UbicacionFisica, InventarioStock, MovimientoInventario,
)
from .serializers import (
    CategoriaSerializer, MarcaRepuestoSerializer, RepuestoSerializer, RepuestoDetalleSerializer,
    SucursalSerializer, AlmacenSerializer, UbicacionFisicaSerializer,
    InventarioStockSerializer, MovimientoInventarioSerializer,
)
from rest_framework import filters, pagination
from django_filters.rest_framework import DjangoFilterBackend
from django.http import HttpResponse
from django.utils import timezone
import openpyxl
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# VIEWSETS EXISTENTES (sin cambios en lógica)
# ──────────────────────────────────────────────

class CategoriaViewSet(viewsets.ModelViewSet):
    queryset = Categoria.objects.filter(estado=True).order_by('-id')
    serializer_class = CategoriaSerializer
    permission_classes = [IsAuthenticated]

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save()


class MarcaRepuestoViewSet(viewsets.ModelViewSet):
    queryset = MarcaRepuesto.objects.filter(estado=True).order_by('-id')
    serializer_class = MarcaRepuestoSerializer
    permission_classes = [IsAuthenticated]

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save()


class RepuestoPagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

class RepuestoViewSet(viewsets.ModelViewSet):
    # select_related y prefetch_related para evitar N+1
    queryset = (
        Repuesto.objects
        .filter(estado=True)
        .select_related('categoria', 'marca')
        .prefetch_related(
            'aplicaciones',
            # Prefetch del inventario con sus relaciones anidadas para el detalle
            Prefetch(
                'inventario_stock',
                queryset=InventarioStock.objects.select_related(
                    'ubicacion__almacen__sucursal'
                )
            )
        )
        .order_by('-id')
    )
    serializer_class = RepuestoSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = RepuestoPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['categoria', 'marca']
    search_fields = ['codigo', 'nombre']
    ordering_fields = ['codigo', 'nombre', 'precio_lista']

    def get_queryset(self):
        qs = super().get_queryset()
        ubicacion = self.request.query_params.get('ubicacion')
        if ubicacion:
            # Filter by pasillo, estante, casillero or codigo
            qs = qs.filter(
                Q(inventario_stock__ubicacion__pasillo__icontains=ubicacion) |
                Q(inventario_stock__ubicacion__estante__icontains=ubicacion) |
                Q(inventario_stock__ubicacion__casillero__icontains=ubicacion) |
                Q(inventario_stock__ubicacion__codigo__icontains=ubicacion)
            ).distinct()
        return qs

    def get_serializer_class(self):
        """
        Usa el serializer detallado (con inventario anidado) para el retrieve (GET /id/).
        Usa el serializer estándar para list/create/update para mantener compatibilidad.
        """
        if self.action == 'retrieve':
            return RepuestoDetalleSerializer
        return RepuestoSerializer

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save()

    @transaction.atomic
    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save()

    @action(detail=False, methods=['get'])
    def compatibles(self, request):
        """
        Endpoint dinámico para obtener repuestos compatibles con un vehículo.
        Query Params esperados: marca, modelo, motor
        """
        marca = request.query_params.get('marca', None)
        modelo = request.query_params.get('modelo', None)
        motor = request.query_params.get('motor', None)

        if not marca:
            return Response({'error': 'La marca del vehiculo es requerida'}, status=400)

        query = Q(aplicaciones__marca_vehiculo__iexact=marca)

        if modelo:
            query &= (Q(aplicaciones__modelo_vehiculo__isnull=True) | Q(aplicaciones__modelo_vehiculo__iexact=modelo))

        if motor:
            query &= (Q(aplicaciones__motor__isnull=True) | Q(aplicaciones__motor__iexact=motor))

        repuestos = self.get_queryset().filter(query).distinct()

        page = self.paginate_queryset(repuestos)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(repuestos, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def exportar_excel(self, request):
        repuestos = self.filter_queryset(self.get_queryset())
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Repuestos"
        
        headers = ["Código", "Nombre", "Categoría", "Marca", "Stock Global", "P. Lista", "P. Compra"]
        ws.append(headers)
        
        for r in repuestos:
            ws.append([
                r.codigo,
                r.nombre,
                r.categoria.nombre if r.categoria else '',
                r.marca.nombre if r.marca else '',
                r.stock_total_disponible,
                float(r.precio_lista),
                float(r.precio_compra),
            ])
            
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename=repuestos.xlsx'
        wb.save(response)
        return response

    @action(detail=False, methods=['get'])
    def exportar_pdf(self, request):
        repuestos = self.filter_queryset(self.get_queryset())
        
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="reporte_repuestos.pdf"'
        
        doc = SimpleDocTemplate(response, pagesize=landscape(letter))
        elements = []
        styles = getSampleStyleSheet()
        
        elements.append(Paragraph("Reporte de Inventario de Repuestos", styles['Title']))
        elements.append(Paragraph(f"Generado el: {timezone.now().strftime('%Y-%m-%d %H:%M')}", styles['Normal']))
        elements.append(Spacer(1, 12))
        
        data = [["Código", "Nombre", "Categoría", "Marca", "Stock Global", "P. Lista"]]
        for r in repuestos:
            data.append([
                r.codigo,
                r.nombre[:30] + ('...' if len(r.nombre)>30 else ''),
                r.categoria.nombre if r.categoria else '',
                r.marca.nombre if r.marca else '',
                str(r.stock_total_disponible),
                f"S/ {r.precio_lista}"
            ])
            
        table = Table(data, colWidths=[80, 200, 100, 100, 80, 80])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1976d2")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        elements.append(table)
        doc.build(elements)
        return response


# ──────────────────────────────────────────────
# NUEVOS VIEWSETS: ESTRUCTURA FÍSICA
# ──────────────────────────────────────────────

class SucursalViewSet(viewsets.ModelViewSet):
    """CRUD completo de Sucursales."""
    queryset = Sucursal.objects.filter(estado=True).order_by('nombre')
    serializer_class = SucursalSerializer
    permission_classes = [IsAuthenticated]

    def perform_destroy(self, instance):
        # Soft delete: no eliminar físicamente
        instance.estado = False
        instance.save()
        logger.info(f"Sucursal desactivada: {instance.nombre} | Usuario: {self.request.user}")


class AlmacenViewSet(viewsets.ModelViewSet):
    """CRUD completo de Almacenes. Filtra por sucursal si se pasa ?sucursal=<id>."""
    # select_related para evitar N+1 al mostrar sucursal_nombre
    queryset = Almacen.objects.filter(estado=True).select_related('sucursal').order_by('sucursal__nombre', 'nombre')
    serializer_class = AlmacenSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        sucursal_id = self.request.query_params.get('sucursal')
        if sucursal_id:
            qs = qs.filter(sucursal_id=sucursal_id)
        return qs

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save()
        logger.info(f"Almacén desactivado: {instance} | Usuario: {self.request.user}")


class UbicacionFisicaViewSet(viewsets.ModelViewSet):
    """CRUD completo de Ubicaciones Físicas. Filtra por almacen si se pasa ?almacen=<id>."""
    # select_related para evitar N+1
    queryset = (
        UbicacionFisica.objects
        .select_related('almacen__sucursal')
        .order_by('almacen__sucursal__nombre', 'almacen__nombre', 'codigo')
    )
    serializer_class = UbicacionFisicaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        almacen_id = self.request.query_params.get('almacen')
        if almacen_id:
            qs = qs.filter(almacen_id=almacen_id)
        sucursal_id = self.request.query_params.get('sucursal')
        if sucursal_id:
            qs = qs.filter(almacen__sucursal_id=sucursal_id)
        return qs


# ──────────────────────────────────────────────
# NUEVOS VIEWSETS: STOCK Y KARDEX
# ──────────────────────────────────────────────

class InventarioStockViewSet(viewsets.ModelViewSet):
    """
    Gestión del stock por ubicación.
    - GET /inventario/stock/?repuesto=<id>  → Ver stock de un repuesto en todas las ubicaciones
    - GET /inventario/stock/?ubicacion=<id> → Ver todos los repuestos en una ubicación
    - PATCH /inventario/stock/<id>/         → Ajustar stock (crea automáticamente el movimiento de Kardex)
    """
    queryset = (
        InventarioStock.objects
        .select_related('repuesto', 'ubicacion__almacen__sucursal')
        .order_by('repuesto__codigo')
    )
    serializer_class = InventarioStockSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        repuesto_id = self.request.query_params.get('repuesto')
        if repuesto_id:
            qs = qs.filter(repuesto_id=repuesto_id)
        ubicacion_id = self.request.query_params.get('ubicacion')
        if ubicacion_id:
            qs = qs.filter(ubicacion_id=ubicacion_id)
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        """
        Al asignar por primera vez un repuesto a una ubicación,
        registramos el movimiento de 'INVENTARIO_INICIAL' en el Kardex.
        """
        instance = serializer.save()
        if instance.stock_disponible > 0:
            MovimientoInventario.objects.create(
                repuesto=instance.repuesto,
                ubicacion=instance.ubicacion,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.INVENTARIO_INICIAL,
                cantidad=instance.stock_disponible,
                stock_resultante=instance.stock_disponible,
                motivo=self.request.data.get('motivo', 'Asignación inicial a ubicación'),
                usuario=self.request.user,
            )
            logger.info(f"Kardex Inicial creado: {instance.repuesto} en {instance.ubicacion} con {instance.stock_disponible}")

    @transaction.atomic
    def perform_update(self, serializer):
        """
        Al actualizar el stock, registra automáticamente el movimiento de Kardex.
        Usa transaction.atomic para que el ajuste y el movimiento sean indivisibles.
        """
        instance_antes = self.get_object()
        stock_antes = instance_antes.stock_disponible

        instance = serializer.save()
        stock_despues = instance.stock_disponible
        diferencia = stock_despues - stock_antes

        if diferencia != 0:
            tipo = (
                MovimientoInventario.TipoMovimiento.AJUSTE_POSITIVO
                if diferencia > 0
                else MovimientoInventario.TipoMovimiento.AJUSTE_NEGATIVO
            )
            MovimientoInventario.objects.create(
                repuesto=instance.repuesto,
                ubicacion=instance.ubicacion,
                tipo_movimiento=tipo,
                cantidad=diferencia,
                stock_resultante=stock_despues,
                motivo=self.request.data.get('motivo', 'Ajuste manual desde el sistema'),
                usuario=self.request.user,
            )
            logger.info(
                f"Ajuste de stock: {instance.repuesto.codigo} | {diferencia:+d} unidades "
                f"→ {stock_despues} | Ubicación: {instance.ubicacion.codigo} | Usuario: {self.request.user}"
            )


class MovimientoInventarioViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Kardex de inventario (solo lectura). El Kardex es inmutable.
    Filtra por ?repuesto=<id> o ?ubicacion=<id>.
    Paginado por defecto (25 registros). Los movimientos más recientes van primero.
    """
    queryset = (
        MovimientoInventario.objects
        .select_related('repuesto', 'ubicacion__almacen__sucursal', 'usuario')
        .order_by('-fecha')
    )
    serializer_class = MovimientoInventarioSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        repuesto_id = self.request.query_params.get('repuesto')
        if repuesto_id:
            qs = qs.filter(repuesto_id=repuesto_id)
        ubicacion_id = self.request.query_params.get('ubicacion')
        if ubicacion_id:
            qs = qs.filter(ubicacion_id=ubicacion_id)
        tipo = self.request.query_params.get('tipo')
        if tipo:
            qs = qs.filter(tipo_movimiento=tipo)
        return qs
