import logging
import mimetypes

from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import pagination, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.seguridad.permissions import permisos_efectivos

from .models import Documento
from .serializers import DocumentoSerializer

logger = logging.getLogger(__name__)

# Los documentos reutilizan los permisos de su propietario: quien puede ver/editar
# el vehículo (o el cliente) puede ver/editar sus documentos.
PERMISOS = {
    'VEHICULO': {'ver': 'VEHICULOS.VER', 'editar': 'VEHICULOS.EDITAR'},
    'CLIENTE': {'ver': 'CONTACTOS.CLIENTES.VER', 'editar': 'CONTACTOS.CLIENTES.EDITAR'},
}


class DocumentoPagination(pagination.PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


def _entidad(documento_o_datos):
    if isinstance(documento_o_datos, Documento):
        return 'VEHICULO' if documento_o_datos.vehiculo_id else 'CLIENTE'
    return 'VEHICULO' if documento_o_datos.get('vehiculo') else 'CLIENTE'


class DocumentoViewSet(viewsets.ModelViewSet):
    """
    /api/documentos/?vehiculo=<id> | ?cliente=<id>  (+ tipo, vigencia=vencido|por_vencer|alerta|vigente)
    Subida con multipart (campo `archivo`: PDF/JPG/PNG, máx. 5 MB).
    El archivo se descarga con GET /api/documentos/<id>/archivo/ (requiere sesión y permisos).
    """
    serializer_class = DocumentoSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = DocumentoPagination
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    # --- permisos por propietario -------------------------------------------------
    def _permisos(self):
        if not hasattr(self.request, '_permisos_documentos'):
            self.request._permisos_documentos = permisos_efectivos(self.request.user)
        return self.request._permisos_documentos

    def _puede(self, entidad, accion):
        # Mismo criterio que TienePermiso: el superusuario siempre tiene acceso.
        if self.request.user.is_superuser:
            return True
        return PERMISOS[entidad][accion] in self._permisos()

    def _exigir(self, entidad, accion):
        if not self._puede(entidad, accion):
            raise PermissionDenied('No tienes permiso para esta acción sobre estos documentos.')

    # --- consulta -----------------------------------------------------------------
    def get_queryset(self):
        qs = Documento.objects.filter(activo=True).select_related('vehiculo', 'cliente')

        visibles = Q(pk__in=[])
        if self._puede('VEHICULO', 'ver'):
            visibles |= Q(vehiculo__isnull=False)
        if self._puede('CLIENTE', 'ver'):
            visibles |= Q(cliente__isnull=False)
        qs = qs.filter(visibles)

        params = self.request.query_params
        if params.get('vehiculo'):
            qs = qs.filter(vehiculo_id=params['vehiculo'])
        if params.get('cliente'):
            qs = qs.filter(cliente_id=params['cliente'])
        if params.get('tipo'):
            qs = qs.filter(tipo=params['tipo'])

        hoy = timezone.localdate()
        limite = Documento.limite_por_vencer()
        vigencia = params.get('vigencia')
        if vigencia == 'vencido':
            qs = qs.filter(fecha_vencimiento__lt=hoy)
        elif vigencia == 'por_vencer':
            qs = qs.filter(fecha_vencimiento__gte=hoy, fecha_vencimiento__lte=limite)
        elif vigencia == 'alerta':
            qs = qs.filter(fecha_vencimiento__lte=limite)
        elif vigencia == 'vigente':
            qs = qs.filter(Q(fecha_vencimiento__isnull=True) | Q(fecha_vencimiento__gt=limite))
        return qs

    # --- escritura ----------------------------------------------------------------
    def perform_create(self, serializer):
        self._exigir(_entidad(serializer.validated_data), 'editar')
        archivo = serializer.validated_data.get('archivo')
        serializer.save(creado_por=self.request.user, nombre_archivo=archivo.name[:200] if archivo else '')

    def perform_update(self, serializer):
        documento = self.get_object()
        self._exigir(_entidad(documento), 'editar')
        archivo = serializer.validated_data.get('archivo')
        if archivo:
            if documento.archivo:
                documento.archivo.delete(save=False)  # no dejar archivos huérfanos
            serializer.save(nombre_archivo=archivo.name[:200])
        else:
            serializer.save()

    def perform_destroy(self, instance):
        self._exigir(_entidad(instance), 'editar')
        instance.activo = False  # baja lógica: se conserva el rastro
        instance.save(update_fields=['activo', 'actualizado_en'])
        logger.info('Documento %s dado de baja por %s', instance.pk, self.request.user)

    # --- acciones -----------------------------------------------------------------
    @action(detail=True, methods=['get'])
    def archivo(self, request, pk=None):
        documento = self.get_object()
        self._exigir(_entidad(documento), 'ver')
        if not documento.archivo:
            raise NotFound('Este documento no tiene archivo adjunto.')
        tipo, _ = mimetypes.guess_type(documento.archivo.name)
        respuesta = FileResponse(
            documento.archivo.open('rb'), content_type=tipo or 'application/octet-stream',
            filename=documento.nombre_archivo or None,
        )
        respuesta['X-Content-Type-Options'] = 'nosniff'
        return respuesta

    @action(detail=False, methods=['get'])
    def resumen(self, request):
        """Conteo de documentos vencidos y por vencer (para tableros y avisos)."""
        qs = self.get_queryset()
        hoy = timezone.localdate()
        limite = Documento.limite_por_vencer()
        return Response({
            'vencidos': qs.filter(fecha_vencimiento__lt=hoy).count(),
            'por_vencer': qs.filter(fecha_vencimiento__gte=hoy, fecha_vencimiento__lte=limite).count(),
        })
