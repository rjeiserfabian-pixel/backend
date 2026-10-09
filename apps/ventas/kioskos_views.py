import logging
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from .models import KioskoTerminal
from .serializers import KioskoTerminalSerializer
from apps.seguridad.permissions import PermisoPorMetodoMixin

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# MANTENIMIENTO (CONFIGURACIONES)
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
