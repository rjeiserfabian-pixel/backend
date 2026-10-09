import logging

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import pagination, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.seguridad.permissions import TienePermiso

from .models import AvisoCliente
from .serializers import AvisoSerializer
from .servicios import generar_avisos

logger = logging.getLogger(__name__)


class AvisoPagination(pagination.PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class AvisoViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Avisos a clientes. Ver/actualizar exige AVISOS.VER; marcar como enviado o descartar, AVISOS.GESTIONAR.
    Filtros: estado (por defecto PENDIENTE), tipo, search (cliente/teléfono).
    """
    serializer_class = AvisoSerializer
    pagination_class = AvisoPagination

    def get_permissions(self):
        if self.action in ('marcar_enviado', 'descartar', 'reabrir'):
            return [TienePermiso('AVISOS.GESTIONAR')]
        return [TienePermiso('AVISOS.VER')]

    def get_queryset(self):
        qs = AvisoCliente.objects.select_related('atendido_por')
        params = self.request.query_params
        if self.action == 'list':
            qs = qs.filter(estado=params.get('estado') or AvisoCliente.Estado.PENDIENTE)
        if params.get('tipo'):
            qs = qs.filter(tipo=params['tipo'])
        busqueda = (params.get('search') or '').strip()
        if busqueda:
            qs = qs.filter(Q(cliente_nombre__icontains=busqueda) | Q(telefono__icontains=busqueda))
        return qs

    @action(detail=False, methods=['post'])
    def generar(self, request):
        """Calcula los avisos de hoy (seguro de repetir: no duplica)."""
        resultado = generar_avisos()
        logger.info('Avisos generados por %s: %s', request.user, resultado)
        return Response(resultado)

    @action(detail=False, methods=['get'])
    def resumen(self, request):
        """Pendientes por tipo (para insignias y tableros)."""
        filas = (
            AvisoCliente.objects.filter(estado=AvisoCliente.Estado.PENDIENTE)
            .values('tipo').annotate(total=Count('id'))
        )
        por_tipo = {fila['tipo']: fila['total'] for fila in filas}
        return Response({'total': sum(por_tipo.values()), 'por_tipo': por_tipo})

    def _cambiar_estado(self, request, estado):
        aviso = self.get_object()
        aviso.estado = estado
        aviso.descartado_automatico = False
        aviso.atendido_por = request.user if estado != AvisoCliente.Estado.PENDIENTE else None
        aviso.atendido_en = timezone.now() if estado != AvisoCliente.Estado.PENDIENTE else None
        aviso.save(update_fields=['estado', 'descartado_automatico', 'atendido_por', 'atendido_en'])
        return Response(self.get_serializer(aviso).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='marcar-enviado')
    def marcar_enviado(self, request, pk=None):
        return self._cambiar_estado(request, AvisoCliente.Estado.ENVIADO)

    @action(detail=True, methods=['post'])
    def descartar(self, request, pk=None):
        return self._cambiar_estado(request, AvisoCliente.Estado.DESCARTADO)

    @action(detail=True, methods=['post'])
    def reabrir(self, request, pk=None):
        return self._cambiar_estado(request, AvisoCliente.Estado.PENDIENTE)
