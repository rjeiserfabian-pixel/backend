import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.db.models import Q, Prefetch
from .models import (
    UnidadMedida, Categoria, MarcaRepuesto, Repuesto, AplicacionRepuesto,
    Sucursal, Almacen, UbicacionFisica, InventarioStock, MovimientoInventario,
    TrasladoInventario, TrasladoInventarioDetalle
)
from .serializers import (
    UnidadMedidaSerializer, CategoriaSerializer, MarcaRepuestoSerializer, RepuestoSerializer, RepuestoDetalleSerializer,
    SucursalSerializer, AlmacenSerializer, UbicacionFisicaSerializer,
    InventarioStockSerializer, MovimientoInventarioSerializer, TrasladoInventarioSerializer
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

class UnidadMedidaViewSet(viewsets.ModelViewSet):
    queryset = UnidadMedida.objects.filter(estado=True).order_by('nombre')
    serializer_class = UnidadMedidaSerializer
    permission_classes = [IsAuthenticated]

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save()


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
        Query Params esperados: marca (obligatorio), modelo (opcional), motor (opcional), anio (opcional)
        Lógica: NULL en anio_desde/anio_hasta = sin restricción de año (aplica a todos).
        """
        marca = request.query_params.get('marca', '').strip()
        modelo = request.query_params.get('modelo', '').strip()
        motor = request.query_params.get('motor', '').strip()
        anio = request.query_params.get('anio', None)

        if not marca:
            return Response({'error': 'La marca del vehiculo es requerida'}, status=400)

        query = Q(aplicaciones__marca_vehiculo__iexact=marca)

        if modelo:
            query &= (Q(aplicaciones__modelo_vehiculo__isnull=True) | Q(aplicaciones__modelo_vehiculo__iexact=modelo))

        if motor:
            query &= (Q(aplicaciones__motor__isnull=True) | Q(aplicaciones__motor__iexact=motor))

        # Filtro por año: si el año viene, se respetan los rangos.
        # NULL en anio_desde o anio_hasta significa "sin límite en ese extremo".
        if anio:
            try:
                anio_int = int(anio)
                query &= (Q(aplicaciones__anio_desde__isnull=True) | Q(aplicaciones__anio_desde__lte=anio_int))
                query &= (Q(aplicaciones__anio_hasta__isnull=True) | Q(aplicaciones__anio_hasta__gte=anio_int))
            except (ValueError, TypeError):
                logger.warning(f"Valor de año inválido recibido en /compatibles/: {anio}")

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

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, 'is_superuser', False) or user.usuario_roles.filter(id_rol__codigo='ADMINISTRADOR').exists():
            return qs
        sucursales_ids = user.sucursales_asignadas.values_list('sucursal_id', flat=True)
        return qs.filter(id__in=sucursales_ids)

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
        
        user = self.request.user
        if not (getattr(user, 'is_superuser', False) or user.usuario_roles.filter(id_rol__codigo='ADMINISTRADOR').exists()):
            sucursales_ids = user.sucursales_asignadas.values_list('sucursal_id', flat=True)
            qs = qs.filter(sucursal_id__in=sucursales_ids)
            
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
        almacen_id = self.request.query_params.get('almacen')
        if almacen_id:
            qs = qs.filter(ubicacion__almacen_id=almacen_id)
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
    pagination_class = RepuestoPagination

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


class TrasladoInventarioViewSet(viewsets.ModelViewSet):
    """
    Gestiona los traslados de mercadería entre ubicaciones físicas.
    La creación procesa el stock y genera Kardex atómicamente.
    """
    queryset = TrasladoInventario.objects.all().select_related('almacen_origen', 'almacen_destino', 'usuario').prefetch_related('detalles__repuesto', 'detalles__ubicacion_origen', 'detalles__ubicacion_destino').order_by('-fecha_traslado')
    serializer_class = TrasladoInventarioSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = RepuestoPagination

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        detalles_datos = serializer.validated_data.pop('detalles_datos')
        almacen_origen = serializer.validated_data['almacen_origen']
        almacen_destino = serializer.validated_data['almacen_destino']
        
        # Validar y procesar detalles
        if not detalles_datos:
            return Response({'error': 'Debe enviar al menos un producto.'}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Crear Cabecera del Traslado
        traslado = TrasladoInventario.objects.create(
            almacen_origen=almacen_origen,
            almacen_destino=almacen_destino,
            observaciones=serializer.validated_data.get('observaciones', ''),
            usuario=request.user
        )

        for detalle in detalles_datos:
            repuesto_id = detalle['repuesto']
            ubicacion_origen_id = detalle['ubicacion_origen']
            ubicacion_destino_id = detalle['ubicacion_destino']
            cantidad = detalle['cantidad']
            
            try:
                cantidad = float(cantidad)
            except ValueError:
                return Response({'error': 'Cantidad inválida'}, status=status.HTTP_400_BAD_REQUEST)
                
            if cantidad <= 0:
                return Response({'error': 'La cantidad debe ser mayor a 0'}, status=status.HTTP_400_BAD_REQUEST)
                
            # Validar y obtener registros origen
            try:
                repuesto = Repuesto.objects.get(id=repuesto_id)
                ubi_origen = UbicacionFisica.objects.get(id=ubicacion_origen_id, almacen=almacen_origen)
                ubi_destino = UbicacionFisica.objects.get(id=ubicacion_destino_id, almacen=almacen_destino)
                
                stock_origen = InventarioStock.objects.get(repuesto=repuesto, ubicacion=ubi_origen)
            except Repuesto.DoesNotExist:
                return Response({'error': f'Repuesto ID {repuesto_id} no existe.'}, status=status.HTTP_400_BAD_REQUEST)
            except UbicacionFisica.DoesNotExist:
                return Response({'error': 'Ubicación origen o destino inválida.'}, status=status.HTTP_400_BAD_REQUEST)
            except InventarioStock.DoesNotExist:
                return Response({'error': f'No hay stock registrado del repuesto {repuesto.codigo} en la ubicación de origen.'}, status=status.HTTP_400_BAD_REQUEST)

            if stock_origen.stock_disponible < cantidad:
                return Response({'error': f'Stock insuficiente para el repuesto {repuesto.codigo} en la ubicación origen.'}, status=status.HTTP_400_BAD_REQUEST)

            # 2. Descontar stock en origen
            stock_origen.stock_disponible -= cantidad
            stock_origen.save()

            # Registrar Salida de Traslado en Kardex
            MovimientoInventario.objects.create(
                repuesto=repuesto,
                ubicacion=ubi_origen,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.TRASLADO_SALIDA,
                cantidad=-cantidad,
                stock_resultante=stock_origen.stock_disponible,
                motivo=f"Traslado #{traslado.id} a {almacen_destino.nombre}",
                usuario=request.user,
                referencia_id=traslado.id,
                referencia_tipo='TRASLADO'
            )

            # 3. Sumar stock en destino
            stock_destino, created = InventarioStock.objects.get_or_create(
                repuesto=repuesto,
                ubicacion=ubi_destino,
                defaults={'stock_disponible': 0, 'stock_minimo': stock_origen.stock_minimo}
            )
            stock_destino.stock_disponible += cantidad
            stock_destino.save()

            # Registrar Entrada de Traslado en Kardex
            MovimientoInventario.objects.create(
                repuesto=repuesto,
                ubicacion=ubi_destino,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.TRASLADO_ENTRADA,
                cantidad=cantidad,
                stock_resultante=stock_destino.stock_disponible,
                motivo=f"Traslado #{traslado.id} desde {almacen_origen.nombre}",
                usuario=request.user,
                referencia_id=traslado.id,
                referencia_tipo='TRASLADO'
            )

            # 4. Crear Detalle de Traslado
            TrasladoInventarioDetalle.objects.create(
                traslado=traslado,
                repuesto=repuesto,
                ubicacion_origen=ubi_origen,
                ubicacion_destino=ubi_destino,
                cantidad=cantidad
            )

        headers = self.get_success_headers(serializer.data)
        response_serializer = TrasladoInventarioSerializer(traslado)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

