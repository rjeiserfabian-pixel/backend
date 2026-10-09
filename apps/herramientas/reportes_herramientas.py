"""Constructores de reportes del módulo Herramientas.

Cada función recibe los filtros ya validados y devuelve (titulo, headers, rows).
Los reportes con costos solo se arman si el usuario tiene HERRAMIENTAS.COSTOS.VER
(lo valida la vista antes de llamar aquí).
"""
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from .models import (
    AsignacionHerramienta,
    Herramienta,
    IncidenciaHerramienta,
    RegistroMantenimiento,
)

# Tipos de reporte y si necesitan el permiso de costos.
REPORTES = {
    'inventario': {'nombre': 'Inventario de herramientas', 'costos': False},
    'por-responsable': {'nombre': 'Herramientas por responsable', 'costos': False},
    'costos-mantenimiento': {'nombre': 'Costo de mantenimiento por herramienta', 'costos': True},
    'mas-fallas': {'nombre': 'Herramientas con mas fallas', 'costos': False},
}

MAX_FILAS_EXPORTACION = 5000


def _dec(valor):
    return Decimal(valor or 0)


def _fmt_dec(valor):
    return float(_dec(valor))


def inventario(filtros, ver_costos):
    qs = Herramienta.objects.filter(estado=True).select_related('categoria', 'sucursal', 'almacen')
    if filtros.get('sucursal'):
        qs = qs.filter(sucursal_id=filtros['sucursal'])
    if filtros.get('categoria'):
        qs = qs.filter(categoria_id=filtros['categoria'])
    if filtros.get('estado_operativo'):
        qs = qs.filter(estado_operativo=filtros['estado_operativo'])

    headers = [
        'Codigo', 'Nombre', 'Categoria', 'Marca', 'Modelo', 'N. de serie', 'Sucursal',
        'Almacen', 'Estado', 'Estado fisico', 'Horas de uso',
    ]
    if ver_costos:
        headers += ['Fecha de compra', 'Costo de adquisicion']
    rows, total = [], Decimal('0')
    for h in qs.order_by('codigo')[:MAX_FILAS_EXPORTACION + 1]:
        fila = [
            h.codigo, h.nombre, h.categoria.nombre, h.marca, h.modelo, h.numero_serie,
            h.sucursal.nombre, h.almacen.nombre if h.almacen else '',
            h.get_estado_operativo_display(), h.get_estado_fisico_display(),
            _fmt_dec(h.horas_uso_acumuladas),
        ]
        if ver_costos:
            fila += [
                h.fecha_compra.strftime('%d/%m/%Y') if h.fecha_compra else '',
                _fmt_dec(h.costo_adquisicion) if h.costo_adquisicion is not None else '',
            ]
            total += _dec(h.costo_adquisicion)
        rows.append(fila)
    if ver_costos and rows:
        rows.append(['TOTAL'] + [''] * (len(headers) - 2) + [_fmt_dec(total)])
    return REPORTES['inventario']['nombre'], headers, rows


def por_responsable(filtros, ver_costos):
    hoy = timezone.localdate()
    qs = AsignacionHerramienta.objects.filter(
        estado=AsignacionHerramienta.Estado.ACTIVA
    ).select_related('herramienta', 'herramienta__sucursal', 'tecnico')
    if filtros.get('sucursal'):
        qs = qs.filter(herramienta__sucursal_id=filtros['sucursal'])
    headers = [
        'Responsable', 'Codigo', 'Herramienta', 'Sucursal', 'Fecha de entrega',
        'Devolucion esperada', 'Dias de atraso',
    ]
    rows = []
    qs = qs.order_by('tecnico__apellidos', 'tecnico__nombres', 'herramienta__codigo')
    for a in qs[:MAX_FILAS_EXPORTACION + 1]:
        atraso = (hoy - a.fecha_devolucion_esperada).days if (
            a.fecha_devolucion_esperada and a.fecha_devolucion_esperada < hoy
        ) else 0
        rows.append([
            f'{a.tecnico.nombres} {a.tecnico.apellidos}'.strip(), a.herramienta.codigo,
            a.herramienta.nombre, a.herramienta.sucursal.nombre,
            timezone.localtime(a.fecha_entrega).strftime('%d/%m/%Y %H:%M'),
            a.fecha_devolucion_esperada.strftime('%d/%m/%Y') if a.fecha_devolucion_esperada else '',
            atraso,
        ])
    return REPORTES['por-responsable']['nombre'], headers, rows


def costos_mantenimiento(filtros, ver_costos):
    qs = RegistroMantenimiento.objects.filter(estado=RegistroMantenimiento.Estado.FINALIZADO)
    if filtros.get('desde'):
        qs = qs.filter(fecha_fin__date__gte=filtros['desde'])
    if filtros.get('hasta'):
        qs = qs.filter(fecha_fin__date__lte=filtros['hasta'])
    if filtros.get('sucursal'):
        qs = qs.filter(herramienta__sucursal_id=filtros['sucursal'])
    if filtros.get('categoria'):
        qs = qs.filter(herramienta__categoria_id=filtros['categoria'])
    agrupado = (
        qs.values('herramienta__codigo', 'herramienta__nombre', 'herramienta__categoria__nombre')
        .annotate(
            trabajos=Count('id'),
            preventivos=Count('id', filter=Q(tipo=RegistroMantenimiento.Tipo.PREVENTIVO)),
            correctivos=Count('id', filter=Q(tipo=RegistroMantenimiento.Tipo.CORRECTIVO)),
            costo=Sum('costo'),
        )
        .order_by('-costo', 'herramienta__codigo')
    )
    headers = ['Codigo', 'Herramienta', 'Categoria', 'Trabajos', 'Preventivos', 'Correctivos', 'Costo total']
    rows, total = [], Decimal('0')
    for g in agrupado[:MAX_FILAS_EXPORTACION + 1]:
        rows.append([
            g['herramienta__codigo'], g['herramienta__nombre'], g['herramienta__categoria__nombre'],
            g['trabajos'], g['preventivos'], g['correctivos'], _fmt_dec(g['costo']),
        ])
        total += _dec(g['costo'])
    if rows:
        rows.append(['TOTAL', '', '', '', '', '', _fmt_dec(total)])
    return REPORTES['costos-mantenimiento']['nombre'], headers, rows


def mas_fallas(filtros, ver_costos):
    """Herramientas con más reparaciones (mantenimientos correctivos) e incidencias de daño."""
    filtro_rep = Q(mantenimientos__tipo=RegistroMantenimiento.Tipo.CORRECTIVO) & ~Q(
        mantenimientos__estado=RegistroMantenimiento.Estado.CANCELADO
    )
    filtro_inc = Q(incidencias__tipo=IncidenciaHerramienta.Tipo.DANO)
    if filtros.get('desde'):
        filtro_rep &= Q(mantenimientos__fecha_inicio__date__gte=filtros['desde'])
        filtro_inc &= Q(incidencias__fecha__date__gte=filtros['desde'])
    if filtros.get('hasta'):
        filtro_rep &= Q(mantenimientos__fecha_inicio__date__lte=filtros['hasta'])
        filtro_inc &= Q(incidencias__fecha__date__lte=filtros['hasta'])
    qs = Herramienta.objects.filter(estado=True).select_related('categoria', 'sucursal')
    if filtros.get('sucursal'):
        qs = qs.filter(sucursal_id=filtros['sucursal'])
    if filtros.get('categoria'):
        qs = qs.filter(categoria_id=filtros['categoria'])
    qs = qs.annotate(
        reparaciones=Count('mantenimientos', filter=filtro_rep, distinct=True),
        danos=Count('incidencias', filter=filtro_inc, distinct=True),
    ).annotate(fallas=F('reparaciones') + F('danos')).filter(fallas__gt=0).order_by('-fallas', 'codigo')
    headers = ['Codigo', 'Herramienta', 'Categoria', 'Sucursal', 'Reparaciones', 'Danos reportados', 'Total de fallas']
    rows = [
        [h.codigo, h.nombre, h.categoria.nombre, h.sucursal.nombre, h.reparaciones, h.danos, h.fallas]
        for h in qs[:50]
    ]
    return REPORTES['mas-fallas']['nombre'], headers, rows


CONSTRUCTORES = {
    'inventario': inventario,
    'por-responsable': por_responsable,
    'costos-mantenimiento': costos_mantenimiento,
    'mas-fallas': mas_fallas,
}
