import logging
from xml.sax.saxutils import escape

from django.db.models import Count, Sum
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.seguridad.permissions import TienePermiso

from . import reportes_herramientas as rep
from . import services_mantenimientos as svc
from .models import (
    AsignacionHerramienta,
    Herramienta,
    IncidenciaHerramienta,
    PlanMantenimientoHerramienta,
)
from .permisos import tiene_permiso

logger = logging.getLogger(__name__)

PERM_COSTOS = 'HERRAMIENTAS.COSTOS.VER'
PERM_EXPORTAR = 'HERRAMIENTAS.REPORTES.EXPORTAR'
FILAS_VISTA_PREVIA = 200
PREFIJOS_FORMULA = ('=', '+', '-', '@', '\t', '\r')
ESTADOS_TERMINALES = (Herramienta.EstadoOperativo.PERDIDA, Herramienta.EstadoOperativo.DADA_DE_BAJA)


class FiltrosReporteSerializer(serializers.Serializer):
    formato = serializers.ChoiceField(choices=['json', 'excel', 'pdf'], default='json')
    sucursal = serializers.IntegerField(required=False, min_value=1)
    categoria = serializers.IntegerField(required=False, min_value=1)
    estado_operativo = serializers.ChoiceField(choices=Herramienta.EstadoOperativo.choices, required=False)
    desde = serializers.DateField(required=False)
    hasta = serializers.DateField(required=False)

    def validate(self, attrs):
        if attrs.get('desde') and attrs.get('hasta') and attrs['desde'] > attrs['hasta']:
            raise serializers.ValidationError({'desde': 'La fecha inicial no puede ser posterior a la final.'})
        return attrs


def _seguro_excel(valor):
    """Evita la inyección de fórmulas: un texto que empieza con = + - @ se guarda como texto."""
    if isinstance(valor, str) and valor.startswith(PREFIJOS_FORMULA):
        return "'" + valor
    return valor


def _seguro_pdf(valor):
    """reportlab interpreta marcado tipo XML en los párrafos; se escapa el texto del usuario."""
    return escape(valor) if isinstance(valor, str) else valor


class ReporteHerramientasView(APIView):
    """GET /api/herramientas/reportes/<tipo>/?formato=json|excel|pdf&sucursal=&categoria=&desde=&hasta="""

    def get_permissions(self):
        return [TienePermiso('HERRAMIENTAS.REPORTES.VER')]

    def get(self, request, tipo):
        info = rep.REPORTES.get(tipo)
        if info is None:
            raise Http404('Reporte no encontrado.')
        serializer = FiltrosReporteSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        filtros = serializer.validated_data
        formato = filtros['formato']

        if formato != 'json' and not tiene_permiso(request, PERM_EXPORTAR):
            raise PermissionDenied('No tiene permiso para exportar reportes.')
        ver_costos = tiene_permiso(request, PERM_COSTOS)
        if info['costos'] and not ver_costos:
            raise PermissionDenied('No tiene permiso para ver costos.')

        titulo, headers, rows = rep.CONSTRUCTORES[tipo](filtros, ver_costos)
        if len(rows) > rep.MAX_FILAS_EXPORTACION:
            raise ValidationError('El reporte tiene demasiadas filas; aplique filtros para reducirlo.')
        logger.info('Reporte %s (%s) generado por usuario %s: %s filas',
                    tipo, formato, request.user.pk, len(rows))

        if formato == 'json':
            total = len(rows)
            if total > FILAS_VISTA_PREVIA:
                # La fila TOTAL (si existe) se conserva al recortar la vista previa.
                cola = [rows[-1]] if rows[-1][0] == 'TOTAL' else []
                rows = rows[:FILAS_VISTA_PREVIA - len(cola)] + cola
            return Response({
                'tipo': tipo, 'titulo': titulo, 'headers': headers, 'rows': rows,
                'total_filas': total, 'truncado': total > FILAS_VISTA_PREVIA,
            })

        # Importación local: se reutilizan los exportadores del módulo de reportes existente.
        from apps.reportes.views import _exportar_excel, _exportar_pdf

        fecha = timezone.localdate().strftime('%Y%m%d')
        if formato == 'excel':
            filas = [[_seguro_excel(c) for c in fila] for fila in rows]
            return _exportar_excel([_seguro_excel(h) for h in headers], filas, f'{titulo} {fecha}')
        filas = [[_seguro_pdf(c) for c in fila] for fila in rows]
        return _exportar_pdf(f'{titulo} {fecha}', headers, filas)


class ResumenHerramientasView(APIView):
    """GET /api/herramientas/resumen/ — contadores para el panel del inventario."""

    def get_permissions(self):
        return [TienePermiso('HERRAMIENTAS.INVENTARIO.VER')]

    def get(self, request):
        hoy = timezone.localdate()
        sucursal = request.query_params.get('sucursal')
        if sucursal is not None and not sucursal.isdigit():
            raise ValidationError({'sucursal': 'Valor no válido.'})

        herramientas = Herramienta.objects.filter(estado=True)
        if sucursal:
            herramientas = herramientas.filter(sucursal_id=sucursal)
        por_estado = {e.value: 0 for e in Herramienta.EstadoOperativo}
        for fila in herramientas.values('estado_operativo').annotate(n=Count('id')):
            por_estado[fila['estado_operativo']] = fila['n']

        data = {
            'total': sum(por_estado.values()),
            'por_estado': por_estado,
            'valor_total': None,
            'mantenimientos_vencidos': None,
            'mantenimientos_por_vencer': None,
            'prestamos_vencidos': None,
            'incidencias_abiertas': None,
        }
        if tiene_permiso(request, PERM_COSTOS):
            suma = herramientas.exclude(estado_operativo__in=ESTADOS_TERMINALES).aggregate(
                s=Sum('costo_adquisicion'))['s']
            data['valor_total'] = float(suma or 0)

        if tiene_permiso(request, 'HERRAMIENTAS.MANTENIMIENTOS.VER'):
            planes = PlanMantenimientoHerramienta.objects.filter(
                activo=True, herramienta__estado=True
            ).exclude(herramienta__estado_operativo__in=ESTADOS_TERMINALES)
            if sucursal:
                planes = planes.filter(herramienta__sucursal_id=sucursal)
            data['mantenimientos_vencidos'] = planes.filter(svc.planes_vencidos_q(hoy)).count()
            data['mantenimientos_por_vencer'] = (
                planes.filter(svc.planes_por_vencer_q(hoy)).exclude(svc.planes_vencidos_q(hoy)).count()
            )
        if tiene_permiso(request, 'HERRAMIENTAS.ASIGNACIONES.VER'):
            prestamos = AsignacionHerramienta.objects.filter(
                estado=AsignacionHerramienta.Estado.ACTIVA, fecha_devolucion_esperada__lt=hoy
            )
            if sucursal:
                prestamos = prestamos.filter(herramienta__sucursal_id=sucursal)
            data['prestamos_vencidos'] = prestamos.count()
        if tiene_permiso(request, 'HERRAMIENTAS.INCIDENCIAS.VER'):
            incidencias = IncidenciaHerramienta.objects.filter(estado=IncidenciaHerramienta.Estado.ABIERTA)
            if sucursal:
                incidencias = incidencias.filter(herramienta__sucursal_id=sucursal)
            data['incidencias_abiertas'] = incidencias.count()
        return Response(data)
