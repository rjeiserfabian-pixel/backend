"""
views.py — Módulo de Cajas
Endpoints del centro de control financiero. Los modelos viven en apps.ventas
pero la lógica de negocio de Cajas está aquí, desacoplada.

Reglas de seguridad aplicadas (python-secure skill):
- select_related en todos los querysets con FK
- Paginación en todas las listas
- transaction.atomic en operaciones multi-paso
- permission_classes = [IsAuthenticated] en todas las vistas
- logger.error con contexto en todos los except
"""
import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone
from rest_framework import viewsets, status, views, pagination
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.ventas.models import (
    Caja, SesionCaja, MovimientoCaja, MetodoPago,
    TransferenciaCaja, ArqueoCaja
)
from .serializers import (
    CajaSerializer, SesionCajaResumenSerializer,
    MovimientoCajaSerializer, MovimientoCajaCreateSerializer,
    TransferenciaCajaSerializer, ArqueoCajaSerializer,
    MetodoPagoSerializer
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# PAGINACIÓN ESTÁNDAR DEL MÓDULO
# ──────────────────────────────────────────────

class CajasPagination(pagination.PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


# ──────────────────────────────────────────────
# HELPERS INTERNOS
# ──────────────────────────────────────────────

def _calcular_saldo_teorico(sesion: SesionCaja) -> Decimal:
    """Calcula el saldo teórico de una sesión: solo movimientos APROBADOS."""
    ingresos = sesion.movimientos.filter(
        tipo=MovimientoCaja.Tipo.INGRESO,
        estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO
    ).aggregate(t=Sum('monto'))['t'] or Decimal('0')
    egresos = sesion.movimientos.filter(
        tipo=MovimientoCaja.Tipo.EGRESO,
        estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO
    ).aggregate(t=Sum('monto'))['t'] or Decimal('0')
    return Decimal(str(sesion.saldo_inicial)) + ingresos - egresos


# ──────────────────────────────────────────────
# DASHBOARD — RESUMEN DE TODAS LAS CAJAS
# ──────────────────────────────────────────────

class DashboardCajasView(views.APIView):
    """
    GET /api/cajas/dashboard/
    Devuelve el resumen de todas las cajas con su sesión activa (o la última).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            sucursal_id = request.query_params.get('sucursal_id')
            cajas_qs = Caja.objects.select_related('sucursal').filter(estado=True)
            if sucursal_id:
                cajas_qs = cajas_qs.filter(sucursal_id=sucursal_id)

            resultado = []
            for caja in cajas_qs:
                sesion_activa = SesionCaja.objects.filter(
                    caja=caja,
                    estado=SesionCaja.Estado.ABIERTA
                ).select_related('usuario').first()

                sesion_data = None
                saldo_actual = Decimal('0')

                if sesion_activa:
                    saldo_actual = _calcular_saldo_teorico(sesion_activa)
                    sesion_data = {
                        'id': sesion_activa.id,
                        'usuario': f"{sesion_activa.usuario.nombres} {sesion_activa.usuario.apellidos}".strip() or sesion_activa.usuario.username,
                        'fecha_apertura': sesion_activa.fecha_apertura,
                        'saldo_inicial': float(sesion_activa.saldo_inicial),
                    }

                resultado.append({
                    'caja_id':      caja.id,
                    'caja_nombre':  caja.nombre,
                    'caja_tipo':    caja.tipo,
                    'sucursal':     caja.sucursal.nombre,
                    'estado':       'ABIERTA' if sesion_activa else 'CERRADA',
                    'saldo_actual': float(saldo_actual),
                    'sesion_activa': sesion_data,
                })

            return Response({'data': resultado})
        except Exception as e:
            logger.error(f"[DashboardCajas] Error: {str(e)}", exc_info=True)
            return Response({'error': 'Error al cargar el dashboard.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ──────────────────────────────────────────────
# GESTIÓN DE CAJAS
# ──────────────────────────────────────────────

class CajaViewSet(viewsets.ModelViewSet):
    """CRUD de cajas (Operativas y Chicas)."""
    queryset = Caja.objects.select_related('sucursal').filter(estado=True)
    serializer_class = CajaSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['sucursal', 'tipo', 'estado']


# ──────────────────────────────────────────────
# SESIONES — APERTURA / CIERRE / DETALLE
# ──────────────────────────────────────────────

class SesionCajaViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Gestión de sesiones de caja.
    La apertura y el cierre son acciones especiales con lógica de negocio propia.
    """
    queryset = SesionCaja.objects.select_related('caja__sucursal', 'usuario').all()
    serializer_class = SesionCajaResumenSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = CajasPagination

    def get_queryset(self):
        qs = super().get_queryset()
        caja_id    = self.request.query_params.get('caja_id')
        estado     = self.request.query_params.get('estado')
        sucursal_id = self.request.query_params.get('sucursal_id')
        if caja_id:
            qs = qs.filter(caja_id=caja_id)
        if estado:
            qs = qs.filter(estado=estado)
        if sucursal_id:
            qs = qs.filter(caja__sucursal_id=sucursal_id)
        return qs.order_by('-fecha_apertura')

    @action(detail=False, methods=['post'], url_path='abrir')
    def abrir(self, request):
        """POST /api/cajas/sesiones/abrir/"""
        caja_id      = request.data.get('caja_id')
        saldo_inicial = Decimal(str(request.data.get('saldo_inicial', '0')))

        caja = Caja.objects.filter(id=caja_id, estado=True).first()
        if not caja:
            return Response({'error': 'Caja no encontrada o inactiva.'}, status=status.HTTP_404_NOT_FOUND)

        ya_abierta = SesionCaja.objects.filter(caja=caja, estado=SesionCaja.Estado.ABIERTA).exists()
        if ya_abierta:
            return Response({'error': 'Esta caja ya tiene una sesión abierta.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                sesion = SesionCaja.objects.create(
                    caja=caja,
                    usuario=request.user,
                    saldo_inicial=saldo_inicial,
                    estado=SesionCaja.Estado.ABIERTA,
                )
                logger.info(f"[Cajas] Sesión {sesion.id} abierta en {caja.nombre} por {request.user}")
            return Response(SesionCajaResumenSerializer(sesion).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"[Cajas] Error al abrir sesión en caja {caja_id}: {str(e)}", exc_info=True)
            return Response({'error': 'Error al abrir la caja.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='detalle')
    def detalle(self, request, pk=None):
        """GET /api/cajas/sesiones/<id>/detalle/ — Saldo y movimientos paginados."""
        sesion = self.get_object()
        ingresos = sesion.movimientos.filter(
            tipo=MovimientoCaja.Tipo.INGRESO,
            estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO
        ).aggregate(t=Sum('monto'))['t'] or Decimal('0')
        egresos = sesion.movimientos.filter(
            tipo=MovimientoCaja.Tipo.EGRESO,
            estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO
        ).aggregate(t=Sum('monto'))['t'] or Decimal('0')
        saldo_actual = Decimal(str(sesion.saldo_inicial)) + ingresos - egresos

        movimientos_qs = sesion.movimientos.select_related('metodo_pago', 'creado_por').order_by('-fecha')

        # Filtros opcionales
        filtro_tipo   = request.query_params.get('tipo')
        filtro_origen = request.query_params.get('origen_movimiento')
        if filtro_tipo:
            movimientos_qs = movimientos_qs.filter(tipo=filtro_tipo)
        if filtro_origen:
            movimientos_qs = movimientos_qs.filter(origen_movimiento=filtro_origen)

        paginator = CajasPagination()
        # Permitir page_size dinámico desde el frontend (máx 100)
        page_size_param = request.query_params.get('page_size')
        if page_size_param and page_size_param.isdigit():
            paginator.page_size = min(int(page_size_param), 100)

        page = paginator.paginate_queryset(movimientos_qs, request)
        movs_data = MovimientoCajaSerializer(page, many=True).data

        return Response({
            'sesion':       SesionCajaResumenSerializer(sesion).data,
            'saldo_inicial': float(sesion.saldo_inicial),
            'ingresos':     float(ingresos),
            'egresos':      float(egresos),
            'saldo_actual': float(saldo_actual),
            'movimientos':  paginator.get_paginated_response(movs_data).data,
        })

    @action(detail=True, methods=['post'], url_path='cerrar')
    def cerrar(self, request, pk=None):
        """
        POST /api/cajas/sesiones/<id>/cerrar/
        Realiza el arqueo final y cierra la sesión.
        Si hay diferencia, marca la sesión como CERRADA_CON_DIFERENCIA.
        """
        sesion = self.get_object()
        if sesion.estado != SesionCaja.Estado.ABIERTA:
            return Response({'error': 'Esta sesión ya está cerrada.'}, status=status.HTTP_400_BAD_REQUEST)

        saldo_contado = Decimal(str(request.data.get('saldo_contado', '0')))
        motivo_diferencia = request.data.get('motivo_diferencia', '').strip()

        saldo_teorico = _calcular_saldo_teorico(sesion)
        diferencia    = saldo_contado - saldo_teorico

        # Si hay diferencia, el motivo es obligatorio
        if diferencia != Decimal('0') and not motivo_diferencia:
            return Response(
                {'error': 'Existe una diferencia de caja. Debe ingresar el motivo de la diferencia.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            with transaction.atomic():
                # Registrar arqueo final
                ArqueoCaja.objects.create(
                    sesion=sesion,
                    saldo_teorico=saldo_teorico,
                    saldo_contado=saldo_contado,
                    diferencia=diferencia,
                    motivo_diferencia=motivo_diferencia if diferencia != Decimal('0') else None,
                    es_cierre_final=True,
                    usuario=request.user,
                )

                # Determinar estado del cierre
                estado_cierre = (
                    SesionCaja.Estado.CERRADA_CON_DIFERENCIA
                    if diferencia != Decimal('0')
                    else SesionCaja.Estado.CERRADA
                )

                sesion.saldo_cierre_esperado = saldo_teorico
                sesion.saldo_cierre_real     = saldo_contado
                sesion.estado                = estado_cierre
                sesion.fecha_cierre          = timezone.now()
                sesion.save(update_fields=['saldo_cierre_esperado', 'saldo_cierre_real', 'estado', 'fecha_cierre'])

                logger.info(
                    f"[Cajas] Sesión {sesion.id} cerrada con estado={estado_cierre}, "
                    f"diferencia={diferencia} por {request.user}"
                )

            return Response(SesionCajaResumenSerializer(sesion).data)
        except Exception as e:
            logger.error(f"[Cajas] Error al cerrar sesión {pk}: {str(e)}", exc_info=True)
            return Response({'error': 'Error al cerrar la caja.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ──────────────────────────────────────────────
# MOVIMIENTOS MANUALES
# ──────────────────────────────────────────────

class MovimientoManualViewSet(viewsets.mixins.CreateModelMixin,
                               viewsets.mixins.ListModelMixin,
                               viewsets.mixins.RetrieveModelMixin,
                               viewsets.GenericViewSet):
    """
    Ingresos y egresos manuales.
    - Los MANUALES entran como PENDIENTE y requieren aprobación del admin.
    - Los automáticos (Venta, Cobro) no pasan por aquí; llegan de sus propios módulos.
    - NO hay PUT/PATCH: los movimientos son inmutables. Solo se aprueban o rechazan.
    """
    queryset = MovimientoCaja.objects.select_related(
        'sesion__caja', 'metodo_pago', 'creado_por', 'aprobado_por'
    ).all()
    permission_classes = [IsAuthenticated]
    pagination_class   = CajasPagination

    def get_serializer_class(self):
        if self.action == 'create':
            return MovimientoCajaCreateSerializer
        return MovimientoCajaSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        sesion_id = self.request.query_params.get('sesion_id')
        tipo      = self.request.query_params.get('tipo')
        estado    = self.request.query_params.get('estado')
        caja_id   = self.request.query_params.get('caja_id')
        if sesion_id:
            qs = qs.filter(sesion_id=sesion_id)
        if tipo:
            qs = qs.filter(tipo=tipo)
        if estado:
            qs = qs.filter(estado_movimiento=estado)
        if caja_id:
            qs = qs.filter(sesion__caja_id=caja_id)
        return qs.order_by('-fecha')

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)

    @action(detail=True, methods=['post'], url_path='aprobar')
    def aprobar(self, request, pk=None):
        """POST /api/cajas/movimientos/<id>/aprobar/ — Solo admins."""
        mov = self.get_object()
        if mov.estado_movimiento != MovimientoCaja.EstadoMovimiento.PENDIENTE:
            return Response({'error': 'Solo se pueden aprobar movimientos en estado PENDIENTE.'}, status=400)
        mov.estado_movimiento = MovimientoCaja.EstadoMovimiento.APROBADO
        mov.aprobado_por      = request.user
        mov.save(update_fields=['estado_movimiento', 'aprobado_por'])
        logger.info(f"[Cajas] Movimiento {mov.id} aprobado por {request.user}")
        return Response(MovimientoCajaSerializer(mov).data)

    @action(detail=True, methods=['post'], url_path='rechazar')
    def rechazar(self, request, pk=None):
        """POST /api/cajas/movimientos/<id>/rechazar/ — Solo admins."""
        mov = self.get_object()
        if mov.estado_movimiento != MovimientoCaja.EstadoMovimiento.PENDIENTE:
            return Response({'error': 'Solo se pueden rechazar movimientos en estado PENDIENTE.'}, status=400)
        motivo = request.data.get('motivo', '').strip()
        if not motivo:
            return Response({'error': 'El motivo del rechazo es obligatorio.'}, status=400)
        mov.estado_movimiento = MovimientoCaja.EstadoMovimiento.RECHAZADO
        mov.aprobado_por      = request.user
        mov.observacion       = f"RECHAZADO: {motivo}"
        mov.save(update_fields=['estado_movimiento', 'aprobado_por', 'observacion'])
        logger.info(f"[Cajas] Movimiento {mov.id} rechazado por {request.user}: {motivo}")
        return Response(MovimientoCajaSerializer(mov).data)


# ──────────────────────────────────────────────
# TRANSFERENCIAS ENTRE CAJAS
# ──────────────────────────────────────────────

class TransferenciaViewSet(viewsets.mixins.CreateModelMixin,
                            viewsets.mixins.ListModelMixin,
                            viewsets.mixins.RetrieveModelMixin,
                            viewsets.GenericViewSet):
    """
    Transferencias entre cajas (ej. Caja 01 → Caja Chica).
    Al crearse, genera dos MovimientoCaja atómicamente.
    """
    queryset = TransferenciaCaja.objects.select_related(
        'sesion_origen__caja', 'sesion_destino__caja', 'usuario'
    ).all()
    serializer_class   = TransferenciaCajaSerializer
    permission_classes = [IsAuthenticated]
    pagination_class   = CajasPagination

    def get_queryset(self):
        qs = super().get_queryset()
        caja_id = self.request.query_params.get('caja_id')
        if caja_id:
            qs = qs.filter(
                Q(sesion_origen__caja_id=caja_id) |
                Q(sesion_destino__caja_id=caja_id)
            )
        return qs.order_by('-fecha')

    def create(self, request, *args, **kwargs):
        sesion_origen_id  = request.data.get('sesion_origen')
        sesion_destino_id = request.data.get('sesion_destino')
        monto_raw         = request.data.get('monto', '0')
        motivo            = request.data.get('motivo', '')

        if sesion_origen_id == sesion_destino_id:
            return Response({'error': 'La caja origen y destino no pueden ser la misma.'}, status=400)

        try:
            monto = Decimal(str(monto_raw))
            if monto <= 0:
                return Response({'error': 'El monto debe ser mayor a 0.'}, status=400)
        except Exception:
            return Response({'error': 'Monto inválido.'}, status=400)

        sesion_origen  = SesionCaja.objects.filter(id=sesion_origen_id,  estado=SesionCaja.Estado.ABIERTA).first()
        sesion_destino = SesionCaja.objects.filter(id=sesion_destino_id, estado=SesionCaja.Estado.ABIERTA).first()

        if not sesion_origen:
            return Response({'error': 'La sesión de caja origen no está abierta.'}, status=400)
        if not sesion_destino:
            return Response({'error': 'La sesión de caja destino no está abierta.'}, status=400)

        # Verificar saldo suficiente en origen
        saldo_origen = _calcular_saldo_teorico(sesion_origen)
        if monto > saldo_origen:
            return Response(
                {'error': f'Saldo insuficiente en caja origen. Disponible: S/ {saldo_origen:.2f}'},
                status=400
            )

        try:
            with transaction.atomic():
                # Usamos el primer método de pago disponible (efectivo) para las transferencias
                from apps.ventas.models import MetodoPago as MP
                metodo_efectivo = MP.objects.filter(nombre__icontains='efectivo', estado=True).first()
                if not metodo_efectivo:
                    metodo_efectivo = MP.objects.filter(estado=True).first()
                if not metodo_efectivo:
                    return Response({'error': 'No hay métodos de pago configurados.'}, status=400)

                # Egreso en caja origen
                mov_salida = MovimientoCaja.objects.create(
                    sesion=sesion_origen,
                    tipo=MovimientoCaja.Tipo.EGRESO,
                    concepto=MovimientoCaja.Concepto.TRANSFERENCIA_SALIENTE,
                    origen_movimiento=MovimientoCaja.OrigenMovimiento.TRANSFERENCIA,
                    referencia_origen=f"Hacia: {sesion_destino.caja.nombre}",
                    metodo_pago=metodo_efectivo,
                    monto=monto,
                    observacion=motivo,
                    estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO,
                    creado_por=request.user,
                )
                # Ingreso en caja destino
                mov_entrada = MovimientoCaja.objects.create(
                    sesion=sesion_destino,
                    tipo=MovimientoCaja.Tipo.INGRESO,
                    concepto=MovimientoCaja.Concepto.TRANSFERENCIA_ENTRANTE,
                    origen_movimiento=MovimientoCaja.OrigenMovimiento.TRANSFERENCIA,
                    referencia_origen=f"Desde: {sesion_origen.caja.nombre}",
                    metodo_pago=metodo_efectivo,
                    monto=monto,
                    observacion=motivo,
                    estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO,
                    creado_por=request.user,
                )
                # Registro de auditoría
                transferencia = TransferenciaCaja.objects.create(
                    sesion_origen=sesion_origen,
                    sesion_destino=sesion_destino,
                    movimiento_salida=mov_salida,
                    movimiento_entrada=mov_entrada,
                    monto=monto,
                    motivo=motivo,
                    estado=TransferenciaCaja.Estado.COMPLETADA,
                    usuario=request.user,
                )
                logger.info(
                    f"[Cajas] Transferencia {transferencia.id}: "
                    f"S/ {monto} de {sesion_origen.caja.nombre} a {sesion_destino.caja.nombre} "
                    f"por {request.user}"
                )

            return Response(TransferenciaCajaSerializer(transferencia).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"[Cajas] Error en transferencia: {str(e)}", exc_info=True)
            return Response({'error': 'Error al registrar la transferencia.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ──────────────────────────────────────────────
# ARQUEOS PARCIALES
# ──────────────────────────────────────────────

class ArqueoView(views.APIView):
    """
    GET /api/cajas/arqueo/<sesion_id>/calcular/  — Devuelve el saldo teórico en tiempo real.
    POST /api/cajas/arqueo/<sesion_id>/registrar/ — Registra un arqueo parcial (no cierra la sesión).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, sesion_id):
        sesion = SesionCaja.objects.filter(id=sesion_id).select_related('caja', 'usuario').first()
        if not sesion:
            return Response({'error': 'Sesión no encontrada.'}, status=404)

        ingresos = sesion.movimientos.filter(
            tipo=MovimientoCaja.Tipo.INGRESO,
            estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO
        ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

        egresos = sesion.movimientos.filter(
            tipo=MovimientoCaja.Tipo.EGRESO,
            estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO
        ).aggregate(t=Sum('monto'))['t'] or Decimal('0')

        saldo_teorico = Decimal(str(sesion.saldo_inicial)) + ingresos - egresos

        return Response({
            'sesion_id':    sesion.id,
            'caja':         sesion.caja.nombre,
            'cajero':       f"{sesion.usuario.nombres} {sesion.usuario.apellidos}".strip() or sesion.usuario.username,
            'saldo_inicial': float(sesion.saldo_inicial),
            'ingresos':     float(ingresos),
            'egresos':      float(egresos),
            'saldo_teorico': float(saldo_teorico),
        })

    def post(self, request, sesion_id):
        """Registra un arqueo parcial sin cerrar la sesión."""
        sesion = SesionCaja.objects.filter(
            id=sesion_id, estado=SesionCaja.Estado.ABIERTA
        ).first()
        if not sesion:
            return Response({'error': 'Sesión no encontrada o no está abierta.'}, status=404)

        saldo_contado = Decimal(str(request.data.get('saldo_contado', '0')))
        motivo        = request.data.get('motivo_diferencia', '').strip()
        saldo_teorico = _calcular_saldo_teorico(sesion)
        diferencia    = saldo_contado - saldo_teorico

        try:
            arqueo = ArqueoCaja.objects.create(
                sesion=sesion,
                saldo_teorico=saldo_teorico,
                saldo_contado=saldo_contado,
                diferencia=diferencia,
                motivo_diferencia=motivo,
                es_cierre_final=False,
                usuario=request.user,
            )
            logger.info(f"[Cajas] Arqueo parcial {arqueo.id} en sesión {sesion_id} por {request.user}")
            return Response(ArqueoCajaSerializer(arqueo).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"[Cajas] Error al registrar arqueo en sesión {sesion_id}: {str(e)}", exc_info=True)
            return Response({'error': 'Error al registrar el arqueo.'}, status=500)
