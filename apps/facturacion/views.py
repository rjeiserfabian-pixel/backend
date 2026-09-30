import logging

from django.db.models import Q
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
        if self.action in ('emitir', 'reintentar', 'aplicar_impacto_interno'):
            return [TienePermiso("FACTURACION.COMPROBANTES.EMITIR")]
        if self.action in ('solicitar_baja', 'consultar_baja'):
            return [TienePermiso("FACTURACION.COMPROBANTES.ANULAR")]
        if self.action in ('preparar_venta', 'sincronizar_ventas', 'preparar_guia', 'generar_nota', 'generar_nota_credito'):
            return [TienePermiso("FACTURACION.COMPROBANTES.CREAR")]
        return [TienePermiso("FACTURACION.COMPROBANTES.VER")]

    def get_queryset(self):
        qs = super().get_queryset()
        tipo_documento = self.request.query_params.get('tipo_documento')
        if tipo_documento:
            qs = qs.filter(tipo_documento=tipo_documento)
        estado = self.request.query_params.get('estado')
        if estado:
            qs = qs.filter(estado=estado)
        sucursal = self.request.query_params.get('sucursal')
        if sucursal:
            qs = qs.filter(sucursal_id=sucursal)
        if (
            self.request.query_params.get('excluir_notas_credito')
            and not tipo_documento
        ):
            qs = qs.exclude(tipo_documento=ComprobanteElectronico.TipoDocumento.NOTA_CREDITO)
        cliente_documento = self.request.query_params.get('cliente_documento')
        if cliente_documento:
            qs = qs.filter(cliente_documento__icontains=cliente_documento)
        search = (self.request.query_params.get('search') or '').strip()
        if search:
            filtros = (
                Q(serie__icontains=search) |
                Q(numero__icontains=search) |
                Q(cliente_nombre__icontains=search) |
                Q(cliente_documento__icontains=search) |
                Q(comprobante_relacionado__serie__icontains=search) |
                Q(comprobante_relacionado__numero__icontains=search)
            )
            if search.isdigit():
                filtros |= Q(venta_id=int(search))
            qs = qs.filter(filtros)
        fecha_desde = self.request.query_params.get('fecha_desde')
        if fecha_desde:
            qs = qs.filter(creado_en__date__gte=fecha_desde)
        fecha_hasta = self.request.query_params.get('fecha_hasta')
        if fecha_hasta:
            qs = qs.filter(creado_en__date__lte=fecha_hasta)
        return qs

    @action(detail=False, methods=['get'], url_path='buscar-originales-nota-credito')
    def buscar_originales_nota_credito(self, request):
        qs = (
            ComprobanteElectronico.objects
            .select_related('sucursal', 'venta', 'venta__cliente')
            .filter(
                estado=ComprobanteElectronico.Estado.ACEPTADO,
                tipo_documento__in=[
                    ComprobanteElectronico.TipoDocumento.FACTURA,
                    ComprobanteElectronico.TipoDocumento.BOLETA,
                ],
            )
            .order_by('-creado_en')
        )

        venta_id = request.query_params.get('venta_id')
        if venta_id:
            qs = qs.filter(venta_id=venta_id)

        serie_numero = (request.query_params.get('serie_numero') or '').strip()
        if serie_numero:
            partes = serie_numero.replace(' ', '').split('-', 1)
            if len(partes) == 2:
                qs = qs.filter(serie__icontains=partes[0], numero__icontains=partes[1])
            else:
                qs = qs.filter(Q(serie__icontains=serie_numero) | Q(numero__icontains=serie_numero))

        cliente = (request.query_params.get('cliente') or '').strip()
        if cliente:
            qs = qs.filter(Q(cliente_nombre__icontains=cliente) | Q(cliente_documento__icontains=cliente))

        fecha_desde = request.query_params.get('fecha_desde')
        if fecha_desde:
            qs = qs.filter(creado_en__date__gte=fecha_desde)
        fecha_hasta = request.query_params.get('fecha_hasta')
        if fecha_hasta:
            qs = qs.filter(creado_en__date__lte=fecha_hasta)

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(ComprobanteElectronicoSerializer(page, many=True).data)
        return Response(ComprobanteElectronicoSerializer(qs[:50], many=True).data)

    @action(detail=True, methods=['get'], url_path='items-nota-credito')
    def items_nota_credito(self, request, pk=None):
        original = self.get_object()
        try:
            items = FacturacionService.items_sugeridos_nota_credito(original)
        except FacturacionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        cuenta = getattr(original.venta, 'cuenta_por_cobrar', None) if original.venta_id else None
        return Response({
            'comprobante': ComprobanteElectronicoSerializer(original).data,
            'venta': {
                'id': original.venta_id,
                'estado': original.venta.estado if original.venta_id else None,
                'es_credito': original.venta.estado == Venta.Estado.AL_CREDITO if original.venta_id else False,
                'cuenta_por_cobrar_id': cuenta.id if cuenta else None,
                'saldo_pendiente': cuenta.saldo_pendiente if cuenta else None,
            },
            'items': items,
        })

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

    @action(detail=False, methods=['post'], url_path='sincronizar-ventas')
    def sincronizar_ventas(self, request):
        resultado = FacturacionService.sincronizar_ventas_pendientes(request.user)
        return Response(resultado)

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

    @action(detail=True, methods=['post'], url_path='aplicar-impacto-interno')
    def aplicar_impacto_interno(self, request, pk=None):
        comprobante = self.get_object()
        try:
            comprobante = FacturacionService.aplicar_impacto_nota_credito(
                comprobante, request.user, validar_pendiente=True,
            )
        except FacturacionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ComprobanteElectronicoSerializer(comprobante).data)

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
                impacto_interno=request.data.get('impacto_interno') or {},
            )
        except FacturacionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ComprobanteElectronicoSerializer(nota).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='generar-nota-credito')
    def generar_nota_credito(self, request, pk=None):
        original = self.get_object()
        try:
            nota = FacturacionService.generar_nota(
                original,
                ComprobanteElectronico.TipoDocumento.NOTA_CREDITO,
                request.data.get('motivo_codigo'),
                request.data.get('items') or [],
                request.data.get('tipo_comprobante_id'),
                request.user,
                impacto_interno=request.data.get('impacto_interno') or {},
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
