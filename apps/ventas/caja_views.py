import logging
from django.db import transaction
from django.db.models import Sum
from rest_framework import viewsets, status, pagination
from rest_framework.response import Response
from rest_framework.decorators import action
from decimal import Decimal

from .models import Caja, SesionCaja, MovimientoCaja, ArqueoCaja
from .serializers import CajaSerializer, SesionCajaSerializer, MovimientoCajaSerializer
from apps.seguridad.permissions import TienePermiso, PermisoPorMetodoMixin
from apps.seguridad.auditoria import registrar

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# MANTENIMIENTO (CONFIGURACIONES)
# ──────────────────────────────────────────────


class CajaViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    # No existe un código CAJAS.CREAR/EDITAR/ELIMINAR dedicado en el catálogo;
    # se reutiliza CAJAS.VER también para escritura (limitación documentada).
    permiso_ver = "CAJAS.VER"
    permiso_editar = "CAJAS.VER"
    queryset = Caja.objects.all()
    serializer_class = CajaSerializer


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
