"""
Reposición de stock: repuestos cuyo stock disponible llegó o cayó por debajo
del mínimo configurado (el mínimo vive en cada registro de InventarioStock y se
edita desde Repuestos > Ajustar stock).

El cálculo se hace por repuesto y almacén: se suma el stock disponible y el
mínimo de todas las ubicaciones de ese almacén. Un repuesto con mínimo 0 se
considera "sin mínimo configurado" y no genera alerta.
"""
import math
from decimal import Decimal

from django.db.models import F, Q, Sum
from rest_framework import pagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.compras.models import DetalleCompra
from apps.seguridad.permissions import TienePermiso

from .models import InventarioStock


class ReposicionPagination(pagination.PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


def _es_admin(user):
    return bool(
        getattr(user, 'is_superuser', False)
        or user.usuario_roles.filter(id_rol__codigo='ADMINISTRADOR').exists()
    )


def _sugerido(disponible: Decimal, minimo: Decimal, permite_decimales: bool) -> Decimal:
    """Cantidad a comprar para volver a 2 veces el mínimo (nunca negativa)."""
    cantidad = max(minimo * 2 - disponible, Decimal('0'))
    if not permite_decimales:
        cantidad = Decimal(math.ceil(cantidad))
    return cantidad.quantize(Decimal('0.01'))


class ReposicionStockView(APIView):
    """
    GET /api/inventario/reposicion/
    Filtros: sucursal, almacen, categoria, search (código/nombre), solo_agotados=1.
    Orden: primero los más urgentes (mayor déficit contra su mínimo).
    """
    pagination_class = ReposicionPagination

    def get_permissions(self):
        return [TienePermiso("INVENTARIO.REPOSICION.VER")]

    def _base_queryset(self, request):
        qs = InventarioStock.objects.filter(repuesto__estado=True)

        # Un usuario que no es administrador solo ve sus sucursales asignadas.
        if not _es_admin(request.user):
            ids = request.user.sucursales_asignadas.values_list('sucursal_id', flat=True)
            qs = qs.filter(ubicacion__almacen__sucursal_id__in=ids)

        params = request.query_params
        if params.get('sucursal'):
            qs = qs.filter(ubicacion__almacen__sucursal_id=params['sucursal'])
        if params.get('almacen'):
            qs = qs.filter(ubicacion__almacen_id=params['almacen'])
        if params.get('categoria'):
            qs = qs.filter(repuesto__categoria_id=params['categoria'])
        busqueda = (params.get('search') or '').strip()
        if busqueda:
            qs = qs.filter(Q(repuesto__codigo__icontains=busqueda) | Q(repuesto__nombre__icontains=busqueda))
        return qs

    def get(self, request):
        agrupado = (
            self._base_queryset(request)
            .values(
                'repuesto_id', 'repuesto__codigo', 'repuesto__nombre', 'repuesto__precio_compra',
                'repuesto__categoria__nombre', 'repuesto__marca__nombre',
                'repuesto__unidad_medida__abreviatura', 'repuesto__unidad_medida__permite_decimales',
                'ubicacion__almacen_id', 'ubicacion__almacen__nombre', 'ubicacion__almacen__sucursal__nombre',
            )
            .annotate(
                disponible=Sum('stock_disponible'),
                reservado=Sum('stock_reservado'),
                minimo=Sum('stock_minimo'),
            )
            .filter(minimo__gt=0, disponible__lte=F('minimo'))
        )
        if request.query_params.get('solo_agotados') in ('1', 'true', 'True'):
            agrupado = agrupado.filter(disponible__lte=0)
        agrupado = agrupado.order_by(F('disponible') - F('minimo'), 'repuesto__nombre')

        # Totales sobre TODO el conjunto filtrado (no solo la página actual).
        filas_total = list(agrupado.values_list('disponible', 'minimo', 'repuesto__precio_compra', 'repuesto__unidad_medida__permite_decimales'))
        agotados = sum(1 for d, *_ in filas_total if d <= 0)
        costo_total = sum(
            _sugerido(d, m, bool(pd)) * (p or Decimal('0')) for d, m, p, pd in filas_total
        )

        paginador = self.pagination_class()
        pagina = paginador.paginate_queryset(agrupado, request, view=self)

        # Último proveedor al que se le compró cada repuesto de esta página (1 consulta).
        ids_repuestos = {f['repuesto_id'] for f in pagina}
        ultimo_proveedor = {}
        compras = (
            DetalleCompra.objects
            .filter(repuesto_id__in=ids_repuestos)
            .exclude(compra__estado='Anulada')
            .order_by('repuesto_id', '-compra__fecha_emision', '-compra_id')
            .values('repuesto_id', 'compra__proveedor__nombre_o_razon_social')
        )
        for c in compras:
            ultimo_proveedor.setdefault(c['repuesto_id'], c['compra__proveedor__nombre_o_razon_social'])

        resultados = []
        for f in pagina:
            disponible, minimo = f['disponible'], f['minimo']
            precio = f['repuesto__precio_compra'] or Decimal('0')
            sugerido = _sugerido(disponible, minimo, bool(f['repuesto__unidad_medida__permite_decimales']))
            resultados.append({
                'repuesto_id': f['repuesto_id'],
                'codigo': f['repuesto__codigo'],
                'nombre': f['repuesto__nombre'],
                'categoria': f['repuesto__categoria__nombre'],
                'marca': f['repuesto__marca__nombre'],
                'unidad': f['repuesto__unidad_medida__abreviatura'] or '',
                'almacen_id': f['ubicacion__almacen_id'],
                'almacen': f['ubicacion__almacen__nombre'],
                'sucursal': f['ubicacion__almacen__sucursal__nombre'],
                'disponible': disponible,
                'reservado': f['reservado'],
                'minimo': minimo,
                'sugerido': sugerido,
                'precio_compra': precio,
                'costo_estimado': (sugerido * precio).quantize(Decimal('0.01')),
                'ultimo_proveedor': ultimo_proveedor.get(f['repuesto_id']),
                'nivel': 'AGOTADO' if disponible <= 0 else 'BAJO',
            })

        respuesta = paginador.get_paginated_response(resultados)
        respuesta.data['resumen'] = {
            'total_alertas': len(filas_total),
            'agotados': agotados,
            'bajos': len(filas_total) - agotados,
            'costo_reposicion_estimado': costo_total.quantize(Decimal('0.01')),
        }
        return respuesta
