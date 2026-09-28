import logging

from rest_framework import pagination, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.inventario.models import GuiaRemision
from apps.seguridad.permissions import TienePermiso
from apps.ventas.models import Venta

from .exceptions import FacturacionError
from .models import ComprobanteElectronico
from .serializers import ComprobanteElectronicoSerializer
from .services import FacturacionService

logger = logging.getLogger(__name__)


class ComprobantePagination(pagination.PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class ComprobanteElectronicoViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Bandeja de comprobantes electrónicos: solo lectura salvo por las
    acciones explícitas de abajo. El usuario nunca crea/edita un
    ComprobanteElectronico por PUT/PATCH directo — siempre pasa por una
    acción de negocio (preparar/emitir/generar nota/solicitar baja) que
    valida el estado correspondiente en FacturacionService.
    """
    queryset = (
        ComprobanteElectronico.objects
        .select_related('sucursal', 'venta', 'guia_remision', 'comprobante_relacionado')
        .prefetch_related('logs', 'detalles')
        .all()
    )
    serializer_class = ComprobanteElectronicoSerializer
    pagination_class = ComprobantePagination
    filterset_fields = ['tipo_documento', 'estado', 'sucursal']

    def get_permissions(self):
        if self.action in ('emitir', 'reintentar'):
            return [TienePermiso("FACTURACION.COMPROBANTES.EMITIR")]
        if self.action in ('solicitar_baja', 'consultar_baja'):
            return [TienePermiso("FACTURACION.COMPROBANTES.ANULAR")]
        if self.action in ('preparar_venta', 'preparar_guia', 'generar_nota'):
            return [TienePermiso("FACTURACION.COMPROBANTES.CREAR")]
        return [TienePermiso("FACTURACION.COMPROBANTES.VER")]

    def get_queryset(self):
        qs = super().get_queryset()
        cliente_documento = self.request.query_params.get('cliente_documento')
        if cliente_documento:
            qs = qs.filter(cliente_documento__icontains=cliente_documento)
        return qs

    @action(detail=False, methods=['post'], url_path='preparar-venta')
    def preparar_venta(self, request):
        venta = Venta.objects.filter(id=request.data.get('venta_id')).select_related('cliente').first()
        if not venta:
            return Response({'detail': 'Venta no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            comprobante = FacturacionService.preparar_para_venta(venta, request.user)
        except FacturacionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ComprobanteElectronicoSerializer(comprobante).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='preparar-guia')
    def preparar_guia(self, request):
        guia = GuiaRemision.objects.filter(id=request.data.get('guia_remision_id')).select_related('cliente').first()
        if not guia:
            return Response({'detail': 'Guía de remisión no encontrada.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            comprobante = FacturacionService.preparar_para_guia(guia, request.user, request.data.get('tipo_comprobante_id'))
        except FacturacionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ComprobanteElectronicoSerializer(comprobante).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def emitir(self, request, pk=None):
        comprobante = self.get_object()
        datos_adicionales = request.data.get('datos_adicionales')
        if datos_adicionales:
            comprobante.datos_adicionales = {**(comprobante.datos_adicionales or {}), **datos_adicionales}
            comprobante.save(update_fields=['datos_adicionales'])
        try:
            comprobante = FacturacionService.emitir(comprobante, request.user, correcciones=request.data.get('correcciones'))
        except FacturacionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ComprobanteElectronicoSerializer(comprobante).data)

    @action(detail=True, methods=['post'])
    def reintentar(self, request, pk=None):
        # Mismo flujo que emitir: FacturacionService.emitir ya valida que el
        # comprobante no esté ACEPTADO/BAJA_ACEPTADA antes de reenviarlo.
        return self.emitir(request, pk=pk)

    @action(detail=True, methods=['post'], url_path='generar-nota')
    def generar_nota(self, request, pk=None):
        original = self.get_object()
        try:
            nota = FacturacionService.generar_nota(
                original,
                request.data.get('tipo_documento'),
                request.data.get('motivo_codigo'),
                request.data.get('items') or [],
                request.data.get('tipo_comprobante_id'),
                request.user,
            )
        except FacturacionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ComprobanteElectronicoSerializer(nota).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='solicitar-baja')
    def solicitar_baja(self, request, pk=None):
        comprobante = self.get_object()
        try:
            comprobante = FacturacionService.solicitar_baja(comprobante, request.data.get('motivo', ''), request.user)
        except FacturacionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ComprobanteElectronicoSerializer(comprobante).data)

    @action(detail=True, methods=['post'], url_path='consultar-baja')
    def consultar_baja(self, request, pk=None):
        comprobante = self.get_object()
        try:
            comprobante = FacturacionService.consultar_ticket_baja(comprobante, request.user)
        except FacturacionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ComprobanteElectronicoSerializer(comprobante).data)
