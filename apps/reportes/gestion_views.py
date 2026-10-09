"""
Reportes de gestión (para el dueño / administrador del taller).

GET /api/reportes/gestion/?tipo=<tipo>&fecha_inicio=&fecha_fin=&dias=&formato=json|excel|pdf

Tipos:
  ticket_promedio, servicios_mas_vendidos, productividad_mecanicos,
  ordenes_perdidas, clientes_inactivos, sin_movimiento

Todos devuelven el mismo formato para que una sola pantalla los muestre:
  { titulo, columnas: [{key, label, formato}], filas: [...], resumen: [{label, valor, formato}] }
`formato` de columna/resumen: texto | entero | decimal | dinero | fecha | porcentaje
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import (
    Avg, Count, DecimalField, ExpressionWrapper, F, Max, OuterRef, Q, Subquery, Sum, Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.models import InventarioStock, MovimientoInventario, Repuesto
from apps.seguridad.permissions import TienePermiso
from apps.taller.models import OrdenHistorialEstado, OrdenRepuesto, OrdenServicio, OrdenTrabajo
from apps.ventas.models import DetalleVenta, Venta

from .views import _exportar_excel, _exportar_pdf, _parse_date_range

MAX_FILAS = 500
VENTAS_VALIDAS = [Venta.Estado.PAGADA, Venta.Estado.AL_CREDITO]
DINERO = DecimalField(max_digits=16, decimal_places=2)
CERO = Value(0, output_field=DINERO)


def _f(valor):
    """Decimal/None -> float redondeado (JSON y Excel)."""
    return round(float(valor or 0), 2)


def _dias(request, por_defecto):
    try:
        dias = int(request.query_params.get('dias', por_defecto))
    except ValueError:
        raise ValidationError('"dias" debe ser un número entero.')
    if not 1 <= dias <= 3650:
        raise ValidationError('"dias" debe estar entre 1 y 3650.')
    return dias


def _total_soles():
    # Cada venta se convierte a soles con su propio tipo de cambio antes de sumar.
    return ExpressionWrapper(F('total') * F('tipo_cambio'), output_field=DINERO)


def _ventas_en_rango(fi, ff):
    return Venta.objects.filter(
        estado__in=VENTAS_VALIDAS, fecha_emision__date__gte=fi, fecha_emision__date__lte=ff,
    )


def _nombre_usuario(nombres, apellidos, username):
    return f'{nombres or ""} {apellidos or ""}'.strip() or username or 'Sin asignar'


class ReporteGestionView(APIView):
    def get_permissions(self):
        if self.request.query_params.get('formato') in ('excel', 'pdf'):
            return [TienePermiso('REPORTES.GESTION.EXPORTAR')]
        return [TienePermiso('REPORTES.GESTION.VER')]

    def get(self, request):
        tipo = request.query_params.get('tipo', 'ticket_promedio')
        generador = {
            'ticket_promedio': self._ticket_promedio,
            'servicios_mas_vendidos': self._servicios_mas_vendidos,
            'productividad_mecanicos': self._productividad_mecanicos,
            'ordenes_perdidas': self._ordenes_perdidas,
            'clientes_inactivos': self._clientes_inactivos,
            'sin_movimiento': self._sin_movimiento,
        }.get(tipo)
        if generador is None:
            return Response({'error': 'Tipo de reporte no válido.'}, status=400)

        reporte = generador(request)
        formato = request.query_params.get('formato', 'json')
        if formato in ('excel', 'pdf'):
            encabezados = [c['label'] for c in reporte['columnas']]
            filas = [[fila.get(c['key']) for c in reporte['columnas']] for fila in reporte['filas']]
            if formato == 'excel':
                return _exportar_excel(encabezados, filas, tipo)
            return _exportar_pdf(reporte['titulo'], encabezados, filas)
        return Response(reporte)

    # ── 1. Ticket promedio ────────────────────────────────────────────────────
    def _ticket_promedio(self, request):
        fi, ff = _parse_date_range(request)
        qs = _ventas_en_rango(fi, ff)
        filas = []
        for r in (
            qs.values('sucursal__nombre')
            .annotate(ventas=Count('id'), total=Coalesce(Sum(_total_soles()), CERO))
            .order_by('-total')
        ):
            filas.append({
                'sucursal': r['sucursal__nombre'], 'ventas': r['ventas'], 'total': _f(r['total']),
                'ticket_promedio': _f(r['total'] / r['ventas']) if r['ventas'] else 0,
            })
        total = sum(f['total'] for f in filas)
        cantidad = sum(f['ventas'] for f in filas)
        return {
            'titulo': f'Ticket promedio ({fi:%d/%m/%Y} al {ff:%d/%m/%Y})',
            'columnas': [
                {'key': 'sucursal', 'label': 'Sucursal', 'formato': 'texto'},
                {'key': 'ventas', 'label': 'N.º de ventas', 'formato': 'entero'},
                {'key': 'total', 'label': 'Total vendido (S/)', 'formato': 'dinero'},
                {'key': 'ticket_promedio', 'label': 'Ticket promedio (S/)', 'formato': 'dinero'},
            ],
            'filas': filas,
            'resumen': [
                {'label': 'Ventas', 'valor': cantidad, 'formato': 'entero'},
                {'label': 'Total vendido', 'valor': round(total, 2), 'formato': 'dinero'},
                {'label': 'Ticket promedio', 'valor': round(total / cantidad, 2) if cantidad else 0, 'formato': 'dinero'},
            ],
        }

    # ── 2. Servicios (mano de obra) más vendidos ──────────────────────────────
    def _servicios_mas_vendidos(self, request):
        fi, ff = _parse_date_range(request)
        qs = (
            DetalleVenta.objects.filter(
                repuesto__isnull=True, venta__estado__in=VENTAS_VALIDAS,
                venta__fecha_emision__date__gte=fi, venta__fecha_emision__date__lte=ff,
            )
            .exclude(descripcion_servicio__isnull=True).exclude(descripcion_servicio='')
        )
        filas = [
            {
                'servicio': r['descripcion_servicio'], 'veces': _f(r['veces']), 'ventas': _f(r['ventas']),
                'precio_promedio': _f(r['ventas'] / r['veces']) if r['veces'] else 0,
            }
            for r in qs.values('descripcion_servicio')
            .annotate(veces=Coalesce(Sum('cantidad'), CERO), ventas=Coalesce(Sum('subtotal_linea'), CERO))
            .order_by('-ventas')[:MAX_FILAS]
        ]
        return {
            'titulo': f'Servicios más vendidos ({fi:%d/%m/%Y} al {ff:%d/%m/%Y})',
            'columnas': [
                {'key': 'servicio', 'label': 'Servicio', 'formato': 'texto'},
                {'key': 'veces', 'label': 'Veces vendido', 'formato': 'decimal'},
                {'key': 'ventas', 'label': 'Ventas (S/)', 'formato': 'dinero'},
                {'key': 'precio_promedio', 'label': 'Precio promedio (S/)', 'formato': 'dinero'},
            ],
            'filas': filas,
            'resumen': [
                {'label': 'Servicios distintos', 'valor': len(filas), 'formato': 'entero'},
                {'label': 'Total en servicios', 'valor': round(sum(f['ventas'] for f in filas), 2), 'formato': 'dinero'},
            ],
        }

    # ── 3. Productividad por mecánico ─────────────────────────────────────────
    def _productividad_mecanicos(self, request):
        fi, ff = _parse_date_range(request)
        ordenes = OrdenTrabajo.objects.filter(
            mecanico_asignado__isnull=False,
            estado__in=[OrdenTrabajo.Estado.FINALIZADO, OrdenTrabajo.Estado.FACTURADO],
            fecha_finalizacion__date__gte=fi, fecha_finalizacion__date__lte=ff,
        )
        base = {
            r['mecanico_asignado']: r
            for r in ordenes.values('mecanico_asignado', 'mecanico_asignado__nombres', 'mecanico_asignado__apellidos',
                                    'mecanico_asignado__username')
            .annotate(ordenes=Count('id'), duracion=Avg(F('fecha_finalizacion') - F('fecha_ingreso')))
        }
        mano_obra = {
            r['orden__mecanico_asignado']: r['total']
            for r in OrdenServicio.objects.filter(orden__in=ordenes, aprobado_cliente=True)
            .values('orden__mecanico_asignado').annotate(total=Coalesce(Sum('precio_estimado'), CERO))
        }
        filas = []
        for mecanico_id, r in base.items():
            horas = r['duracion'].total_seconds() / 3600 if r['duracion'] else 0
            filas.append({
                'mecanico': _nombre_usuario(r['mecanico_asignado__nombres'], r['mecanico_asignado__apellidos'],
                                            r['mecanico_asignado__username']),
                'ordenes': r['ordenes'],
                'horas_promedio': round(horas, 1),
                'mano_obra': _f(mano_obra.get(mecanico_id)),
            })
        filas.sort(key=lambda f: f['ordenes'], reverse=True)
        return {
            'titulo': f'Productividad por mecánico ({fi:%d/%m/%Y} al {ff:%d/%m/%Y})',
            'columnas': [
                {'key': 'mecanico', 'label': 'Mecánico', 'formato': 'texto'},
                {'key': 'ordenes', 'label': 'Órdenes terminadas', 'formato': 'entero'},
                {'key': 'horas_promedio', 'label': 'Horas promedio por orden', 'formato': 'decimal'},
                {'key': 'mano_obra', 'label': 'Mano de obra aprobada (S/)', 'formato': 'dinero'},
            ],
            'filas': filas,
            'resumen': [
                {'label': 'Órdenes terminadas', 'valor': sum(f['ordenes'] for f in filas), 'formato': 'entero'},
                {'label': 'Mano de obra aprobada', 'valor': round(sum(f['mano_obra'] for f in filas), 2), 'formato': 'dinero'},
            ],
        }

    # ── 4. Órdenes perdidas (canceladas) por motivo ───────────────────────────
    def _ordenes_perdidas(self, request):
        fi, ff = _parse_date_range(request)
        etiquetas = dict(OrdenHistorialEstado.MotivoCategoria.choices)
        etiquetas[None] = 'Sin motivo indicado'
        etiquetas[''] = 'Sin motivo indicado'

        categoria_por_orden = {}
        for orden_id, motivo in (
            OrdenHistorialEstado.objects.filter(
                estado=OrdenTrabajo.Estado.CANCELADO, fecha_registro__date__gte=fi, fecha_registro__date__lte=ff,
            ).order_by('fecha_registro').values_list('orden_id', 'motivo_categoria')
        ):
            categoria_por_orden[orden_id] = motivo  # si hubo varias, se queda la última

        ids = list(categoria_por_orden)
        valor = {}
        for r in OrdenServicio.objects.filter(orden_id__in=ids).values('orden_id').annotate(t=Coalesce(Sum('precio_estimado'), CERO)):
            valor[r['orden_id']] = valor.get(r['orden_id'], Decimal('0')) + r['t']
        repuestos_valor = ExpressionWrapper(F('cantidad') * F('precio_unitario'), output_field=DINERO)
        for r in OrdenRepuesto.objects.filter(orden_id__in=ids).values('orden_id').annotate(t=Coalesce(Sum(repuestos_valor), CERO)):
            valor[r['orden_id']] = valor.get(r['orden_id'], Decimal('0')) + r['t']

        agrupado = {}
        for orden_id, motivo in categoria_por_orden.items():
            grupo = agrupado.setdefault(motivo, {'ordenes': 0, 'valor': Decimal('0')})
            grupo['ordenes'] += 1
            grupo['valor'] += valor.get(orden_id, Decimal('0'))
        filas = sorted(
            ({'motivo': etiquetas.get(m, m), 'ordenes': g['ordenes'], 'valor': _f(g['valor'])} for m, g in agrupado.items()),
            key=lambda f: f['valor'], reverse=True,
        )
        return {
            'titulo': f'Órdenes perdidas por motivo ({fi:%d/%m/%Y} al {ff:%d/%m/%Y})',
            'columnas': [
                {'key': 'motivo', 'label': 'Motivo de cancelación', 'formato': 'texto'},
                {'key': 'ordenes', 'label': 'Órdenes', 'formato': 'entero'},
                {'key': 'valor', 'label': 'Valor estimado perdido (S/)', 'formato': 'dinero'},
            ],
            'filas': filas,
            'resumen': [
                {'label': 'Órdenes canceladas', 'valor': len(ids), 'formato': 'entero'},
                {'label': 'Valor estimado perdido', 'valor': round(sum(f['valor'] for f in filas), 2), 'formato': 'dinero'},
            ],
        }

    # ── 5. Clientes inactivos ────────────────────────────────────────────────
    def _clientes_inactivos(self, request):
        dias = _dias(request, 180)
        limite = timezone.now() - timedelta(days=dias)
        filas = [
            {
                'cliente': f"{r['cliente__nombres']} {r['cliente__apellidos']}".strip(),
                'telefono': r['cliente__telefono'] or '',
                'compras': r['compras'],
                'total': _f(r['total']),
                'ultima_compra': r['ultima'].date().isoformat() if r['ultima'] else None,
                'dias_sin_comprar': (timezone.now() - r['ultima']).days if r['ultima'] else None,
            }
            for r in Venta.objects.filter(estado__in=VENTAS_VALIDAS, fecha_emision__isnull=False)
            .values('cliente_id', 'cliente__nombres', 'cliente__apellidos', 'cliente__telefono')
            .annotate(compras=Count('id'), total=Coalesce(Sum(_total_soles()), CERO), ultima=Max('fecha_emision'))
            .filter(ultima__lt=limite)
            .order_by('-total')[:MAX_FILAS]
        ]
        return {
            'titulo': f'Clientes sin comprar hace más de {dias} días',
            'columnas': [
                {'key': 'cliente', 'label': 'Cliente', 'formato': 'texto'},
                {'key': 'telefono', 'label': 'Teléfono', 'formato': 'texto'},
                {'key': 'compras', 'label': 'Compras históricas', 'formato': 'entero'},
                {'key': 'total', 'label': 'Total histórico (S/)', 'formato': 'dinero'},
                {'key': 'ultima_compra', 'label': 'Última compra', 'formato': 'fecha'},
                {'key': 'dias_sin_comprar', 'label': 'Días sin comprar', 'formato': 'entero'},
            ],
            'filas': filas,
            'resumen': [
                {'label': 'Clientes inactivos', 'valor': len(filas), 'formato': 'entero'},
                {'label': 'Valor histórico en riesgo', 'valor': round(sum(f['total'] for f in filas), 2), 'formato': 'dinero'},
            ],
        }

    # ── 6. Repuestos sin movimiento (inventario inmovilizado) ─────────────────
    def _sin_movimiento(self, request):
        dias = _dias(request, 90)
        limite = timezone.now() - timedelta(days=dias)
        stock = (
            InventarioStock.objects.filter(repuesto=OuterRef('pk'))
            .values('repuesto').annotate(t=Sum('stock_disponible')).values('t')
        )
        ultima_salida = (
            MovimientoInventario.objects.filter(
                repuesto=OuterRef('pk'), tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA,
            ).values('repuesto').annotate(m=Max('fecha')).values('m')
        )
        repuestos = (
            Repuesto.objects.filter(estado=True)
            .annotate(stock_total=Coalesce(Subquery(stock, output_field=DINERO), CERO), ultima=Subquery(ultima_salida))
            .filter(stock_total__gt=0)
            .filter(Q(ultima__isnull=True) | Q(ultima__lt=limite))
            .annotate(valor=ExpressionWrapper(F('stock_total') * F('precio_compra'), output_field=DINERO))
            .select_related('categoria')
            .order_by('-valor')
        )
        total_valor = repuestos.aggregate(t=Coalesce(Sum('valor'), CERO))['t']
        total_items = repuestos.count()
        filas = [
            {
                'codigo': r.codigo, 'repuesto': r.nombre, 'categoria': r.categoria.nombre if r.categoria_id else '',
                'stock': _f(r.stock_total), 'valor': _f(r.valor),
                'ultima_salida': r.ultima.date().isoformat() if r.ultima else None,
            }
            for r in repuestos[:MAX_FILAS]
        ]
        return {
            'titulo': f'Repuestos sin salidas en los últimos {dias} días',
            'columnas': [
                {'key': 'codigo', 'label': 'Código', 'formato': 'texto'},
                {'key': 'repuesto', 'label': 'Repuesto', 'formato': 'texto'},
                {'key': 'categoria', 'label': 'Categoría', 'formato': 'texto'},
                {'key': 'stock', 'label': 'Stock', 'formato': 'decimal'},
                {'key': 'valor', 'label': 'Valor inmovilizado (S/)', 'formato': 'dinero'},
                {'key': 'ultima_salida', 'label': 'Última salida', 'formato': 'fecha'},
            ],
            'filas': filas,
            'resumen': [
                {'label': 'Repuestos sin movimiento', 'valor': total_items, 'formato': 'entero'},
                {'label': 'Valor inmovilizado (a costo)', 'valor': _f(total_valor), 'formato': 'dinero'},
            ],
        }

