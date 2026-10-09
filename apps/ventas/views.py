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

from .paginacion import VentaPagination  # noqa: F401  (se usa al definir VentaViewSet y se re-exporta)

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


# ──────────────────────────────────────────────
# CAJA Y SESIONES
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# KIOSKOS (terminales físicos de autoservicio)
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# VENTAS Y KIOSKO
# ──────────────────────────────────────────────


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


# Los bloques movidos se re-exportan para que `from .views import ...` siga funcionando.
from .caja_views import (  # noqa: E402,F401
    CajaViewSet,
    SesionCajaViewSet,
)
from .kioskos_views import (  # noqa: E402,F401
    KioskoTerminalViewSet,
)
from .proformas_views import (  # noqa: E402,F401
    ProformaViewSet,
)
from .cuentas_cobrar_views import (  # noqa: E402,F401
    CuentaPorCobrarViewSet,
)
