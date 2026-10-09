import logging
import uuid
from io import BytesIO
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import viewsets, status, views, pagination
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.parsers import FormParser, MultiPartParser
from decimal import Decimal

from .models import (
    Caja, SesionCaja, MovimientoCaja, TipoComprobante, SerieComprobante, MetodoPago,
    Impuesto, Venta, DetalleVenta, CuentaPorCobrar, CuotaCredito, PagoVenta, SerieDocumentoInterno,
    KioskoTerminal, ArqueoCaja, Proforma, ProformaDetalle
)
from .serializers import (
    CajaSerializer, SesionCajaSerializer, MovimientoCajaSerializer,
    TipoComprobanteSerializer, SerieComprobanteSerializer, MetodoPagoSerializer, ImpuestoSerializer,
    VentaSerializer, TicketKioskoCreateSerializer, ProcesarVentaSerializer,
    CuentaPorCobrarSerializer, SerieDocumentoInternoSerializer, KioskoTerminalSerializer,
    ProformaSerializer, QRMetodoPagoSerializer
)
from .services import VentasService, CreditoService
from apps.inventario.models import Sucursal, Almacen, Repuesto
from apps.clientes.models import Cliente
from apps.vehiculos.models import Vehiculo
from apps.seguridad.permissions import TienePermiso, PermisoPorMetodoMixin
from apps.seguridad.auditoria import registrar
from apps.seguridad.models import CuentaBancaria
from apps.seguridad.pdf_utils import contexto_empresa_pdf

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# MANTENIMIENTO (CONFIGURACIONES)
# ──────────────────────────────────────────────

class MetodoPagoViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = "VENTAS.CONFIGURACION.VER"
    permiso_crear = "VENTAS.CONFIGURACION.CREAR"
    permiso_editar = "VENTAS.CONFIGURACION.EDITAR"
    permiso_eliminar = "VENTAS.CONFIGURACION.ELIMINAR"
    queryset = MetodoPago.objects.all()
    serializer_class = MetodoPagoSerializer

    def get_permissions(self):
        # Subir/quitar el QR es editar el método de pago (no "crear" ni "eliminar" el método).
        if self.action == 'qr':
            return [TienePermiso(self.permiso_editar)]
        return super().get_permissions()

    @action(detail=True, methods=['post', 'delete'], parser_classes=[MultiPartParser, FormParser])
    def qr(self, request, pk=None):
        """POST (multipart: imagen, descripcion) sube/reemplaza el QR de cobro; DELETE lo quita."""
        metodo = self.get_object()
        if request.method == 'DELETE':
            if metodo.qr_imagen:
                metodo.qr_imagen.delete(save=False)
            metodo.qr_descripcion = ''
            metodo.qr_imagen = None
            metodo.save(update_fields=['qr_imagen', 'qr_descripcion'])
            return Response(self.get_serializer(metodo).data)

        datos = QRMetodoPagoSerializer(data=request.data)
        datos.is_valid(raise_exception=True)
        imagen = datos.validated_data.get('imagen')
        if imagen is None and not metodo.qr_imagen:
            return Response({'error': 'Suba la imagen del QR.'}, status=status.HTTP_400_BAD_REQUEST)
        if imagen is not None:
            if metodo.qr_imagen:
                metodo.qr_imagen.delete(save=False)  # no dejar QR viejos huérfanos
            metodo.qr_imagen = imagen
        metodo.qr_descripcion = datos.validated_data.get('descripcion', metodo.qr_descripcion)
        metodo.save(update_fields=['qr_imagen', 'qr_descripcion'])
        return Response(self.get_serializer(metodo).data)


class ImpuestoViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = "INVENTARIO.IMPUESTOS.VER"
    permiso_crear = "INVENTARIO.IMPUESTOS.CREAR"
    permiso_editar = "INVENTARIO.IMPUESTOS.EDITAR"
    permiso_eliminar = "INVENTARIO.IMPUESTOS.ELIMINAR"
    queryset = Impuesto.objects.all()
    serializer_class = ImpuestoSerializer


class TipoComprobanteViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = "VENTAS.CONFIGURACION.VER"
    permiso_crear = "VENTAS.CONFIGURACION.CREAR"
    permiso_editar = "VENTAS.CONFIGURACION.EDITAR"
    permiso_eliminar = "VENTAS.CONFIGURACION.ELIMINAR"
    queryset = TipoComprobante.objects.all()
    serializer_class = TipoComprobanteSerializer


class SerieComprobanteViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = "VENTAS.CONFIGURACION.VER"
    permiso_crear = "VENTAS.CONFIGURACION.CREAR"
    permiso_editar = "VENTAS.CONFIGURACION.EDITAR"
    permiso_eliminar = "VENTAS.CONFIGURACION.ELIMINAR"
    queryset = SerieComprobante.objects.select_related('sucursal', 'tipo_comprobante').all()
    serializer_class = SerieComprobanteSerializer
    filterset_fields = ['sucursal', 'tipo_comprobante', 'estado']

class SerieDocumentoInternoViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = "SERIES_INTERNAS.VER"
    permiso_crear = "SERIES_INTERNAS.CREAR"
    permiso_editar = "SERIES_INTERNAS.EDITAR"
    permiso_eliminar = "SERIES_INTERNAS.ELIMINAR"
    queryset = SerieDocumentoInterno.objects.select_related('sucursal').all()
    serializer_class = SerieDocumentoInternoSerializer
    filterset_fields = ['sucursal', 'tipo_documento', 'estado']


class CajaViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    # No existe un código CAJAS.CREAR/EDITAR/ELIMINAR dedicado en el catálogo;
    # se reutiliza CAJAS.VER también para escritura (limitación documentada).
    permiso_ver = "CAJAS.VER"
    permiso_editar = "CAJAS.VER"
    queryset = Caja.objects.all()
    serializer_class = CajaSerializer


# ──────────────────────────────────────────────
# CAJA Y SESIONES
# ──────────────────────────────────────────────

class SesionCajaViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SesionCaja.objects.all()
    serializer_class = SesionCajaSerializer

    def get_permissions(self):
        if self.action == 'aperturar':
            return [TienePermiso("CAJAS.SESION.ABRIR")]
        if self.action == 'cerrar':
            return [TienePermiso("CAJAS.SESION.CERRAR")]
        if self.request.method == 'GET':
            return [TienePermiso("CAJAS.HISTORIAL.VER")]
        return [TienePermiso("CAJAS.VER")]

    @action(detail=False, methods=['post'])
    def aperturar(self, request):
        caja_id = request.data.get('caja_id')
        saldo_inicial = request.data.get('saldo_inicial', 0.00)
        
        caja = Caja.objects.filter(id=caja_id).first()
        if not caja:
            return Response({"error": "Caja no encontrada."}, status=status.HTTP_404_NOT_FOUND)
            
        sesion_abierta = SesionCaja.objects.filter(caja=caja, estado=SesionCaja.Estado.ABIERTA).exists()
        if sesion_abierta:
            return Response({"error": "Ya existe una sesión abierta para esta caja."}, status=status.HTTP_400_BAD_REQUEST)
            
        sesion = SesionCaja.objects.create(
            caja=caja,
            usuario=request.user,
            saldo_inicial=saldo_inicial
        )
        return Response(SesionCajaSerializer(sesion).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def cerrar(self, request, pk=None):
        sesion = self.get_object()
        if sesion.estado != SesionCaja.Estado.ABIERTA:
            return Response({"error": "Esta sesión ya está cerrada."}, status=status.HTTP_400_BAD_REQUEST)

        saldo_fisico_declarado = Decimal(str(request.data.get('saldo_cierre_real', 0.00)))
        motivo_diferencia = (request.data.get('motivo_diferencia') or '').strip()

        # Calcular saldo esperado sumando solo movimientos APROBADOS (un movimiento
        # manual PENDIENTE de aprobación, registrado desde el módulo Cajas sobre esta
        # misma sesión, no debe contarse en el cierre — bug real ya corregido aquí).
        movimientos_aprobados = sesion.movimientos.filter(estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO)
        ingresos = movimientos_aprobados.filter(tipo=MovimientoCaja.Tipo.INGRESO).aggregate(t=Sum('monto'))['t'] or 0
        egresos = movimientos_aprobados.filter(tipo=MovimientoCaja.Tipo.EGRESO).aggregate(t=Sum('monto'))['t'] or 0
        saldo_esperado = Decimal(str(sesion.saldo_inicial)) + Decimal(str(ingresos)) - Decimal(str(egresos))
        diferencia = saldo_fisico_declarado - saldo_esperado

        # Misma exigencia que el cierre del módulo Cajas: si hay diferencia, el
        # motivo es obligatorio y queda un ArqueoCaja de auditoría (antes este
        # cierre "legacy" no dejaba ese respaldo).
        if diferencia != Decimal('0') and not motivo_diferencia:
            return Response(
                {'error': 'Existe una diferencia de caja. Debe ingresar el motivo de la diferencia.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        ArqueoCaja.objects.create(
            sesion=sesion,
            saldo_teorico=saldo_esperado,
            saldo_contado=saldo_fisico_declarado,
            diferencia=diferencia,
            motivo_diferencia=motivo_diferencia if diferencia != Decimal('0') else None,
            es_cierre_final=True,
            usuario=request.user,
        )

        sesion.saldo_cierre_esperado = saldo_esperado
        sesion.saldo_cierre_real = saldo_fisico_declarado
        sesion.estado = (
            SesionCaja.Estado.CERRADA_CON_DIFERENCIA
            if diferencia != Decimal('0')
            else SesionCaja.Estado.CERRADA
        )
        from django.utils import timezone
        sesion.fecha_cierre = timezone.now()
        sesion.save()

        registrar(request, 'CAJAS', 'CIERRE_CAJA', 'sesion_caja', sesion.id,
                  {'saldo_esperado': saldo_esperado},
                  {'saldo_contado': saldo_fisico_declarado, 'diferencia': diferencia,
                   'motivo_diferencia': motivo_diferencia or None})
        return Response(SesionCajaSerializer(sesion).data)

    @action(detail=True, methods=['get'], url_path='reporte-cierre')
    def reporte_cierre(self, request, pk=None):
        sesion = self.get_object()

        # Agrupar ingresos por método de pago (solo movimientos APROBADOS)
        movimientos = sesion.movimientos.filter(estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO)
        por_metodo = {}
        por_concepto = {}
        
        for mov in movimientos:
            # Agrupar por método
            if mov.metodo_pago.nombre not in por_metodo:
                por_metodo[mov.metodo_pago.nombre] = 0
            # Agrupar por concepto
            if mov.concepto not in por_concepto:
                por_concepto[mov.concepto] = 0
                
            if mov.tipo == MovimientoCaja.Tipo.INGRESO:
                por_metodo[mov.metodo_pago.nombre] += float(mov.monto)
                por_concepto[mov.concepto] += float(mov.monto)
            else:
                por_metodo[mov.metodo_pago.nombre] -= float(mov.monto)
                por_concepto[mov.concepto] -= float(mov.monto)
                
        return Response({
            "sesion_id": sesion.id,
            "caja": sesion.caja.nombre,
            "cajero": sesion.usuario.get_full_name(),
            "saldo_inicial": sesion.saldo_inicial,
            "desglose_por_metodo": por_metodo,
            "desglose_por_concepto": por_concepto,
            "saldo_final_esperado": sesion.saldo_cierre_esperado,
            "estado": sesion.estado
        })

    @action(detail=True, methods=['get'], url_path='detalle-activa')
    def detalle_activa(self, request, pk=None):
        sesion = self.get_object()

        # Solo movimientos APROBADOS entran al saldo (ver nota en `cerrar`); la lista
        # de movimientos que se muestra abajo sí incluye pendientes, para que el
        # cajero vea que existen aunque todavía no afecten su saldo.
        movimientos_aprobados = sesion.movimientos.filter(estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO)
        ingresos = movimientos_aprobados.filter(tipo=MovimientoCaja.Tipo.INGRESO).aggregate(t=Sum('monto'))['t'] or 0
        egresos = movimientos_aprobados.filter(tipo=MovimientoCaja.Tipo.EGRESO).aggregate(t=Sum('monto'))['t'] or 0
        saldo_actual = float(sesion.saldo_inicial) + float(ingresos) - float(egresos)

        movimientos = sesion.movimientos.all().order_by('-fecha')
        
        paginator = pagination.PageNumberPagination()
        paginator.page_size = request.query_params.get('page_size', 10)
        page_obj = paginator.paginate_queryset(movimientos, request)
        movimientos_data = paginator.get_paginated_response(MovimientoCajaSerializer(page_obj, many=True).data).data
        
        return Response({
            "sesion_id": sesion.id,
            "estado": sesion.estado,
            "saldo_inicial": float(sesion.saldo_inicial),
            "ingresos": float(ingresos),
            "egresos": float(egresos),
            "saldo_actual": float(saldo_actual),
            "movimientos": movimientos_data
        })

# ──────────────────────────────────────────────
# KIOSKOS (terminales físicos de autoservicio)
# ──────────────────────────────────────────────

class KioskoTerminalViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    """
    CRUD de kioskos (panel de administración, requiere sesión) + dos acciones
    públicas para el propio dispositivo físico: `activar` (una vez, con el
    código que entrega el administrador) y `whoami` (confirmar identidad en
    cada carga, sin volver a pedir nada).
    """
    permiso_ver = "CONFIGURACION.KIOSKOS.VER"
    permiso_crear = "CONFIGURACION.KIOSKOS.CREAR"
    permiso_editar = "CONFIGURACION.KIOSKOS.EDITAR"
    permiso_eliminar = "CONFIGURACION.KIOSKOS.ELIMINAR"
    queryset = KioskoTerminal.objects.select_related('sucursal').all()
    serializer_class = KioskoTerminalSerializer

    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def activar(self, request):
        codigo = (request.data.get('codigo_activacion') or '').strip().upper()
        if not codigo:
            return Response({"error": "Debes indicar el código de activación."}, status=status.HTTP_400_BAD_REQUEST)

        kiosko = KioskoTerminal.objects.select_related('sucursal').filter(codigo_activacion=codigo).first()
        if not kiosko or not kiosko.activo:
            return Response({"error": "Código inválido o kiosko desactivado."}, status=status.HTTP_404_NOT_FOUND)

        from django.utils import timezone as _timezone
        if not kiosko.activado_en:
            kiosko.activado_en = _timezone.now()
        kiosko.ultima_actividad = _timezone.now()
        kiosko.save(update_fields=['activado_en', 'ultima_actividad'])

        return Response({
            'token': kiosko.token,
            'kiosko_id': kiosko.id,
            'kiosko_nombre': kiosko.nombre,
            'sucursal_id': kiosko.sucursal_id,
            'sucursal_nombre': kiosko.sucursal.nombre,
        })

    @action(detail=False, methods=['get'], permission_classes=[AllowAny])
    def whoami(self, request):
        token = request.query_params.get('token')
        kiosko = KioskoTerminal.objects.select_related('sucursal').filter(token=token, activo=True).first() if token else None
        if not kiosko:
            return Response({"error": "Kiosko no reconocido o desactivado."}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'kiosko_id': kiosko.id,
            'kiosko_nombre': kiosko.nombre,
            'sucursal_id': kiosko.sucursal_id,
            'sucursal_nombre': kiosko.sucursal.nombre,
        })


# ──────────────────────────────────────────────
# VENTAS Y KIOSKO
# ──────────────────────────────────────────────

class VentaPagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


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


class VentaViewSet(viewsets.ModelViewSet):
    queryset = Venta.objects.all().order_by('-creado_en')
    serializer_class = VentaSerializer
    pagination_class = VentaPagination
    # Las ventas no se editan ni se borran por la API genérica: borrar una venta
    # dejaría huecos de correlativo y descuadres de caja/stock. Se cancelan
    # con la acción `cancelar` (solo pre-ventas) y se anulan por el flujo propio.
    http_method_names = ['get', 'post', 'head', 'options']

    def get_permissions(self):
        # Si la acción tiene permission_classes propios (ej. @action(permission_classes=[AllowAny])),
        # se respetan por encima del comportamiento genérico — permite endpoints públicos del kiosko.
        action_handler = getattr(self, self.action, None)
        if action_handler and hasattr(action_handler, 'kwargs'):
            action_perms = action_handler.kwargs.get('permission_classes')
            if action_perms is not None:
                return [permission() for permission in action_perms]

        if self.action == 'cancelar':
            return [TienePermiso("VENTAS.POS.ANULAR")]
        if self.action == 'anular':
            return [TienePermiso("VENTAS.POS.ANULAR_VENTA")]
        if self.request.method == 'GET':
            return [TienePermiso("VENTAS.POS.VER")]
        return [TienePermiso("VENTAS.POS.CREAR")]

    def get_queryset(self):
        qs = super().get_queryset().select_related('cliente', 'vehiculo').prefetch_related('pagos__movimiento_caja')
        if self.action in ('list', 'retrieve'):
            # Todo lo que VentaSerializer muestra por venta se carga de una vez; antes se hacía
            # una consulta extra por cada venta (con 100 ventas por página, más de 1000 consultas).
            qs = qs.select_related(
                'tipo_comprobante', 'sesion_caja__usuario', 'sesion_caja__caja', 'kiosko', 'guia_remision_origen',
            ).prefetch_related('detalles__repuesto__unidad_medida', 'pagos__movimiento_caja__metodo_pago')
        estado = self.request.query_params.get('estado')
        if estado == 'PENDIENTE':
            qs = qs.filter(estado=Venta.Estado.PRE_VENTA)
        elif estado == 'COMPLETADO':
            qs = qs.filter(estado__in=[Venta.Estado.PAGADA, Venta.Estado.AL_CREDITO])
        elif estado:
            qs = qs.filter(estado=estado)

        # Un cajero solo debe ver los pedidos (Kiosko/OT) de la sucursal que
        # tiene activa — antes esta lista mostraba tickets de TODAS las
        # sucursales a cualquier cajero, sin importar dónde estuviera parado.
        sucursal = self.request.query_params.get('sucursal')
        if sucursal:
            qs = qs.filter(sucursal_id=sucursal)

        cliente = (self.request.query_params.get('cliente') or '').strip()
        if cliente:
            qs = qs.filter(
                Q(cliente__nombres__icontains=cliente) |
                Q(cliente__apellidos__icontains=cliente) |
                Q(cliente__dni__icontains=cliente)
            )

        referencia = (self.request.query_params.get('referencia') or '').strip()
        if referencia:
            qs = qs.filter(
                Q(ticket_kiosko__icontains=referencia) |
                Q(serie_correlativo__icontains=referencia) |
                Q(pagos__movimiento_caja__referencia__icontains=referencia)
            )

        fecha_desde = self.request.query_params.get('fecha_desde')
        if fecha_desde:
            qs = qs.filter(creado_en__date__gte=fecha_desde)

        fecha_hasta = self.request.query_params.get('fecha_hasta')
        if fecha_hasta:
            qs = qs.filter(creado_en__date__lte=fecha_hasta)

        return qs.distinct()

    @action(
        detail=False, methods=['post'],
        url_path='kiosko/generar-ticket',
        permission_classes=[AllowAny]  # Público: el kiosko opera sin sesión de usuario
    )
    def kiosko_generar_ticket(self, request):
        serializer = TicketKioskoCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # La sucursal SIEMPRE se resuelve desde el kiosko registrado (nunca del
        # sucursal_id que mande el navegador) — es lo que evita que un ticket
        # generado en una sucursal termine facturado/descontando stock en otra.
        kiosko_token = data.get('kiosko_token')
        kiosko = KioskoTerminal.objects.filter(token=kiosko_token, activo=True).select_related('sucursal').first() if kiosko_token else None
        if not kiosko:
            return Response(
                {"error": "Este kiosko no está activado o fue desactivado. Contacta al administrador del taller."},
                status=status.HTTP_400_BAD_REQUEST
            )
        from django.utils import timezone as _timezone
        kiosko.ultima_actividad = _timezone.now()
        kiosko.save(update_fields=['ultima_actividad'])

        cliente = Cliente.objects.get(id=data['cliente_id'])
        vehiculo_id = data.get('vehiculo_id')
        vehiculo = Vehiculo.objects.get(id=vehiculo_id) if vehiculo_id else None
        kilometraje = data.get('kilometraje', None)

        try:
            venta = VentasService.generar_ticket_kiosko(
                cliente=cliente,
                vehiculo=vehiculo,
                sucursal=kiosko.sucursal,
                detalles_data=data['detalles'],
                kilometraje=kilometraje,
                kiosko=kiosko,
            )
            return Response(VentaSerializer(venta).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Error generando ticket: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def procesar(self, request, pk=None):
        # CAMINO ANTIGUO: ninguna pantalla lo usa. El POS y el Registro Manual cobran con
        # `directa` (VentasService.cobrar_venta_directa). Se conserva por compatibilidad hasta
        # decidir su retiro; no agregar reglas de cobro nuevas aquí.
        venta = self.get_object()
        serializer = ProcesarVentaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        
        sesion_caja_id = request.data.get('sesion_caja_id')
        sesion = SesionCaja.objects.filter(id=sesion_caja_id, estado=SesionCaja.Estado.ABIERTA).first()
        if not sesion:
            return Response({"error": "Debe proporcionar una sesión de caja abierta."}, status=status.HTTP_400_BAD_REQUEST)
            
        almacen_origen = Almacen.objects.filter(id=data.get('almacen_origen_id')).first()
        if not almacen_origen:
            almacen_origen = venta.sucursal.almacenes.first()
            
        try:
            venta = VentasService.procesar_pago_venta(
                venta=venta,
                sesion_caja=sesion,
                tipo_comprobante=data['tipo_comprobante'],
                pagos_data=data['pagos'],
                almacen_origen=almacen_origen,
                usuario=request.user
            )
            
            # Si es crédito, generamos las cuotas
            if 'credito' in data:
                CreditoService.generar_credito(
                    venta=venta,
                    frecuencia=data['credito']['frecuencia'],
                    num_cuotas=data['credito']['cuotas'],
                    dia_pago=data['credito'].get('dia_pago')
                )

            try:
                from apps.facturacion.services import FacturacionService
                FacturacionService.preparar_para_venta(venta, request.user)
            except Exception as exc:
                logger.warning("No se pudo preparar comprobante electronico para venta %s: %s", venta.id, exc)
                
            return Response(VentaSerializer(venta).data)
        except Exception as e:
            logger.error(f"Error procesando venta {pk}: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def cancelar(self, request, pk=None):
        """
        Cancela un pedido pendiente (Kiosko, Taller, Proforma, POS sin cobrar).
        Solo aplica a PRE_VENTA: aún no tiene pago, caja, stock ni correlativo,
        por lo que basta marcarla ANULADA. Las ventas ya cobradas requieren un
        flujo de anulación con reversión y no se pueden cancelar desde aquí.
        """
        motivo = (request.data.get('motivo') or '').strip()
        if not motivo:
            return Response({"error": "Debe indicar el motivo de la cancelación."}, status=status.HTTP_400_BAD_REQUEST)

        # Se bloquea la fila para no cancelar un ticket que otro cajero está cobrando.
        self.get_object()  # 404 si no existe
        venta = Venta.objects.select_for_update().get(pk=pk)

        if venta.estado != Venta.Estado.PRE_VENTA:
            return Response(
                {"error": "Solo se pueden cancelar pedidos pendientes. Esta venta ya fue procesada o anulada."},
                status=status.HTTP_400_BAD_REQUEST
            )

        venta.estado = Venta.Estado.ANULADA
        venta.anulado_en = timezone.now()
        venta.anulado_por = request.user
        venta.motivo_anulacion = motivo
        venta.save(update_fields=['estado', 'anulado_en', 'anulado_por', 'motivo_anulacion'])
        logger.info(
            "Pedido %s (venta %s) cancelado por %s. Motivo: %s",
            venta.ticket_kiosko, venta.id, request.user, motivo
        )
        registrar(request, 'VENTAS', 'PEDIDO_CANCELADO', 'venta', venta.id,
                  {'estado': 'PRE_VENTA', 'referencia': venta.ticket_kiosko},
                  {'estado': 'ANULADA', 'motivo': motivo, 'total': venta.total})
        return Response(VentaSerializer(venta).data)

    @action(detail=True, methods=['post'])
    def anular(self, request, pk=None):
        """
        Anula una venta ya cobrada (PAGADA / AL_CREDITO) revirtiendo stock, caja
        y crédito. Ver VentasService.anular_venta_cobrada para las reglas.
        """
        venta = self.get_object()
        try:
            resumen = VentasService.anular_venta_cobrada(venta.id, request.user, request.data.get('motivo'))
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        venta.refresh_from_db()
        registrar(request, 'VENTAS', 'VENTA_ANULADA', 'venta', venta.id,
                  {'estado': 'PAGADA/AL_CREDITO', 'comprobante': venta.serie_correlativo, 'total': venta.total},
                  {'estado': 'ANULADA', 'motivo': venta.motivo_anulacion, **resumen})
        return Response({**VentaSerializer(venta).data, 'resumen_anulacion': resumen})

    @action(detail=False, methods=['post'], url_path='directa')
    @transaction.atomic
    def procesar_venta_directa(self, request):
        # Cobro del POS y del Registro Manual. La lógica vive en VentasService.cobrar_venta_directa;
        # ante cualquier error se revierte TODO y se devuelve el motivo.
        try:
            venta = VentasService.cobrar_venta_directa(request.data, request.user)
        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Error procesando venta directa: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(VentaSerializer(venta).data, status=status.HTTP_201_CREATED)


import requests
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

class TipoCambioView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.core.cache import cache
        from django.utils import timezone

        # SUNAT solo publica un tipo de cambio por día: se cachea por fecha para
        # que N usuarios cambiando de moneda en el POS no disparen N peticiones
        # al proveedor externo (apis.net.pe), que además tiene límite de tasa.
        cache_key = f"tipo_cambio_sunat_{timezone.localdate().isoformat()}"
        data = cache.get(cache_key)
        if data is not None:
            return Response(data)

        try:
            url = "https://api.apis.net.pe/v1/tipo-cambio-sunat"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()
            # APIsPeru devuelve: {"compra": 3.75, "venta": 3.76, "origen": "SUNAT", "moneda": "USD", "fecha": "2023-10-10"}
            cache.set(cache_key, data, timeout=60 * 60 * 24)
            return Response(data)
        except Exception as e:
            logger.error(f"Error consultando tipo de cambio: {str(e)}")
            return Response({"error": "No se pudo obtener el tipo de cambio."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CuentaPorCobrarViewSet(viewsets.ModelViewSet):
    queryset = CuentaPorCobrar.objects.select_related('venta__cliente').prefetch_related('cuotas').all()
    serializer_class = CuentaPorCobrarSerializer
    pagination_class = VentaPagination

    def get_permissions(self):
        if self.action == 'pagar_cuota':
            return [TienePermiso("CUENTAS.POR_COBRAR.REGISTRAR_PAGO")]
        if self.request.method == 'GET':
            return [TienePermiso("CUENTAS.POR_COBRAR.VER")]
        # No hay código CREAR/EDITAR/ELIMINAR dedicado para cuentas por cobrar
        # fuera del registro de pagos; se reutiliza REGISTRAR_PAGO.
        return [TienePermiso("CUENTAS.POR_COBRAR.REGISTRAR_PAGO")]

    def get_queryset(self):
        qs = super().get_queryset()
        estado = self.request.query_params.get('estado')
        cliente_id = self.request.query_params.get('cliente_id')
        if estado:
            qs = qs.filter(estado=estado)
        if cliente_id:
            qs = qs.filter(venta__cliente_id=cliente_id)
        return qs.order_by('-creado_en')

    @action(detail=False, methods=['get'], url_path='resumen-clientes')
    def resumen_clientes(self, request):
        from django.db.models import Sum, Count, Q
        from django.utils import timezone

        hoy = timezone.localdate()

        # El estado ATRASADO de CuentaPorCobrar nunca se actualiza en ningún
        # lado del sistema (queda fijo en PENDIENTE aunque venza), así que
        # contar por ese campo siempre daría 0. Se calcula en su lugar
        # dinámicamente: una cuenta "tiene atraso" si alguna de sus cuotas
        # está vencida y con saldo pendiente — igual que ya hace Cuentas por
        # Pagar en resumen_proveedores.
        qs = CuentaPorCobrar.objects.values(
            'venta__cliente__id',
            'venta__cliente__dni',
            'venta__cliente__nombres',
            'venta__cliente__apellidos'
        ).annotate(
            total_deuda=Sum('monto_financiado'),
            saldo_pendiente_total=Sum('saldo_pendiente'),
            tiene_atrasos=Count(
                'id',
                filter=Q(cuotas__fecha_vencimiento__lt=hoy, cuotas__saldo_pendiente__gt=0),
                distinct=True
            )
        ).order_by('-saldo_pendiente_total')

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(page)

        return Response(qs)

    @action(detail=False, methods=['get'], url_path='cuotas-vencidas')
    def cuotas_vencidas(self, request):
        """
        Para la campanita de alertas del header: cuotas de crédito vencidas
        (fecha_vencimiento pasada y con saldo pendiente) ordenadas por las más
        urgentes primero (más antiguas vencidas). Se limita a 30 resultados
        para no sobrecargar el desplegable; `total` lleva la cuenta real.
        Filtra por sucursal si se manda `sucursal_id` (vía Venta.sucursal).
        """
        from django.utils import timezone

        hoy = timezone.localdate()
        sucursal_id = request.query_params.get('sucursal_id')
        LIMITE = 30

        qs = CuotaCredito.objects.select_related(
            'cuenta_cobrar__venta__cliente'
        ).filter(
            fecha_vencimiento__lt=hoy,
            saldo_pendiente__gt=0
        )
        if sucursal_id:
            qs = qs.filter(cuenta_cobrar__venta__sucursal_id=sucursal_id)
        qs = qs.order_by('fecha_vencimiento')

        total = qs.count()
        data = [{
            'id': c.id,
            'cuenta_cobrar_id': c.cuenta_cobrar_id,
            'cliente_nombre': f"{c.cuenta_cobrar.venta.cliente.nombres} {c.cuenta_cobrar.venta.cliente.apellidos}".strip(),
            'codigo_credito': c.cuenta_cobrar.codigo_credito,
            'numero_cuota': c.numero_cuota,
            'saldo_pendiente': c.saldo_pendiente,
            'fecha_vencimiento': c.fecha_vencimiento,
            'dias_vencido': (hoy - c.fecha_vencimiento).days,
        } for c in qs[:LIMITE]]

        return Response({'total': total, 'results': data})

    @action(detail=False, methods=['post'], url_path='pagar-cuota/(?P<cuota_id>[^/.]+)')
    def pagar_cuota(self, request, cuota_id=None):
        from django.utils import timezone
        from decimal import Decimal
        from apps.ventas.models import PagoCuota, MovimientoCaja
        try:
            with transaction.atomic():
                cuota = CuotaCredito.objects.select_for_update().get(id=cuota_id)
                if cuota.estado == CuotaCredito.Estado.PAGADA:
                    return Response({"error": "Esta cuota ya está pagada."}, status=400)
                
                sesion = SesionCaja.objects.filter(usuario=request.user, fecha_cierre__isnull=True).first()
                if not sesion:
                    return Response({"error": "Debes abrir una caja antes de registrar un cobro."}, status=400)
                
                pagos = request.data.get('pagos', [])
                if not pagos:
                    return Response({"error": "Debe enviar al menos un método de pago y monto."}, status=400)
                
                total_pagado = sum(Decimal(str(p.get('monto', 0))) for p in pagos)
                if total_pagado > cuota.saldo_pendiente:
                    return Response({"error": "El monto pagado supera el saldo pendiente de la cuota."}, status=400)

                import uuid
                operacion_uuid = uuid.uuid4()
                nuevos_pagos = []
                for pago_data in pagos:
                    monto_decimal = Decimal(str(pago_data.get('monto', 0)))
                    metodo_pago_id = pago_data.get('metodo_pago_id')
                    metodo_pago = MetodoPago.objects.get(id=metodo_pago_id)
                    
                    movimiento = MovimientoCaja.objects.create(
                        sesion=sesion,
                        tipo=MovimientoCaja.Tipo.INGRESO,
                        concepto=MovimientoCaja.Concepto.COBRO_CUOTA,
                        metodo_pago=metodo_pago,
                        monto=monto_decimal,
                        referencia=pago_data.get('referencia', ''),
                        origen_movimiento=MovimientoCaja.OrigenMovimiento.COBRO,
                        creado_por=request.user
                    )
                    pago_obj = PagoCuota.objects.create(
                        cuota=cuota,
                        movimiento_caja=movimiento,
                        operacion_id=operacion_uuid,
                        monto=monto_decimal
                    )
                    nuevos_pagos.append(pago_obj)
                
                cuota.saldo_pendiente -= total_pagado
                if cuota.saldo_pendiente <= 0:
                    cuota.saldo_pendiente = 0
                    cuota.estado = CuotaCredito.Estado.PAGADA
                    cuota.fecha_pago = timezone.now().date()
                else:
                    cuota.estado = CuotaCredito.Estado.PARCIAL
                cuota.save()

                cuenta = cuota.cuenta_cobrar
                cuenta.saldo_pendiente -= total_pagado
                if cuenta.saldo_pendiente <= 0:
                    cuenta.saldo_pendiente = 0
                    cuenta.estado = CuentaPorCobrar.Estado.PAGADO
                cuenta.save()
                
                from apps.ventas.serializers import PagoCuotaSerializer
                pagos_data = PagoCuotaSerializer(nuevos_pagos, many=True).data

                return Response({
                    "message": "Pago registrado exitosamente.",
                    "pagos": pagos_data
                })
        except Exception as e:
            logger.error(f"Error al pagar cuota: {str(e)}")
            return Response({"error": str(e)}, status=500)
