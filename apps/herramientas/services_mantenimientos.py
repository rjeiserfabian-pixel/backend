"""Reglas de negocio de mantenimiento e incidencias.

Igual que en services.py: todo cambio de estado ocurre en una transacción con la
herramienta bloqueada (`select_for_update`) para evitar carreras entre usuarios.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import (
    AsignacionHerramienta,
    Herramienta,
    HistorialHerramienta,
    IncidenciaHerramienta,
    PlanMantenimientoHerramienta,
    RegistroMantenimiento,
)
from .services import ConflictoAsignacion

logger = logging.getLogger(__name__)

Estado = Herramienta.EstadoOperativo
# Estados desde los que se puede abrir un mantenimiento.
ESTADOS_PARA_MANTENIMIENTO = {
    Estado.DISPONIBLE, Estado.EN_MANTENIMIENTO, Estado.EN_REPARACION, Estado.FUERA_DE_SERVICIO,
}
ESTADOS_TERMINALES = {Estado.PERDIDA, Estado.DADA_DE_BAJA}


# ───────────── Filtros de vencimiento (misma regla que PlanMantenimientoHerramienta) ─────────────

def planes_vencidos_q(hoy=None):
    hoy = hoy or timezone.localdate()
    return Q(proxima_fecha__lt=hoy) | Q(proximas_horas__lte=F('herramienta__horas_uso_acumuladas'))


def planes_por_vencer_q(hoy=None):
    hoy = hoy or timezone.localdate()
    aviso = PlanMantenimientoHerramienta.DIAS_AVISO
    pct = PlanMantenimientoHerramienta.PORCENTAJE_AVISO_HORAS
    return Q(proxima_fecha__lte=hoy + timedelta(days=aviso)) | Q(
        proximas_horas__lte=F('herramienta__horas_uso_acumuladas') + F('intervalo_horas') * pct
    )


def planes_vencidos_de(herramienta):
    """Planes activos ya vencidos de una herramienta (bloquean la entrega)."""
    planes = herramienta.planes_mantenimiento.filter(activo=True).select_related('herramienta')
    return [p for p in planes if p.estado_vencimiento() == 'VENCIDO']


def _historial(herramienta, accion, detalle, usuario):
    HistorialHerramienta.objects.create(
        herramienta=herramienta, accion=accion, detalle=detalle, usuario=usuario
    )


# ───────────── Mantenimientos ─────────────

def _abrir_mantenimiento(herramienta, *, tipo, descripcion, plan, usuario, realizado_por=''):
    """Abre un registro EN_CURSO sobre una herramienta YA bloqueada por el llamador."""
    if herramienta.estado_operativo not in ESTADOS_PARA_MANTENIMIENTO:
        raise ConflictoAsignacion(
            f'No se puede iniciar mantenimiento: la herramienta está '
            f'"{herramienta.get_estado_operativo_display()}".'
        )
    if plan is not None and (plan.herramienta_id != herramienta.pk or not plan.activo):
        raise ValidationError({'plan': 'El plan no pertenece a la herramienta o está inactivo.'})
    try:
        with transaction.atomic():
            registro = RegistroMantenimiento.objects.create(
                herramienta=herramienta, plan=plan, tipo=tipo, descripcion=descripcion,
                realizado_por=realizado_por, creado_por=usuario,
            )
    except IntegrityError:
        raise ConflictoAsignacion('La herramienta ya tiene un mantenimiento en curso.')

    herramienta.estado_operativo = (
        Estado.EN_REPARACION if tipo == RegistroMantenimiento.Tipo.CORRECTIVO else Estado.EN_MANTENIMIENTO
    )
    herramienta.save(update_fields=['estado_operativo', 'fecha_actualizacion'])
    _historial(
        herramienta, HistorialHerramienta.Accion.MANTENIMIENTO_INICIO,
        f'{registro.get_tipo_display()}: {descripcion}', usuario,
    )
    return registro


def iniciar_mantenimiento(*, herramienta_id, tipo, descripcion, usuario, plan_id=None, realizado_por=''):
    with transaction.atomic():
        try:
            herramienta = Herramienta.objects.select_for_update().get(pk=herramienta_id, estado=True)
        except Herramienta.DoesNotExist:
            raise ValidationError({'herramienta': 'La herramienta no existe.'})
        plan = None
        if plan_id:
            plan = PlanMantenimientoHerramienta.objects.filter(pk=plan_id).first()
            if plan is None:
                raise ValidationError({'plan': 'El plan no existe.'})
        registro = _abrir_mantenimiento(
            herramienta, tipo=tipo, descripcion=descripcion, plan=plan,
            usuario=usuario, realizado_por=realizado_por,
        )
    logger.info('Mantenimiento %s iniciado (herramienta %s) por usuario %s',
                registro.pk, herramienta.codigo, usuario.pk)
    return registro


def _bloquear_registro(registro_id):
    try:
        return RegistroMantenimiento.objects.select_for_update(of=('self',)).get(pk=registro_id)
    except RegistroMantenimiento.DoesNotExist:
        raise Http404('Registro de mantenimiento no encontrado.')


def finalizar_mantenimiento(*, registro_id, usuario, resultado, costo=None, repuestos_usados='',
                            realizado_por='', estado_fisico_final, dejar_fuera_de_servicio=False):
    """Cierra el trabajo, deja la herramienta lista (o fuera de servicio) y reinicia el plan asociado."""
    with transaction.atomic():
        registro = _bloquear_registro(registro_id)
        if registro.estado != RegistroMantenimiento.Estado.EN_CURSO:
            raise ConflictoAsignacion('Solo se puede finalizar un mantenimiento en curso.')
        herramienta = Herramienta.objects.select_for_update().get(pk=registro.herramienta_id)

        registro.estado = RegistroMantenimiento.Estado.FINALIZADO
        registro.fecha_fin = timezone.now()
        registro.resultado = resultado
        registro.costo = costo
        registro.repuestos_usados = repuestos_usados
        if realizado_por:
            registro.realizado_por = realizado_por
        registro.horas_herramienta_al_cierre = herramienta.horas_uso_acumuladas
        registro.cerrado_por = usuario
        registro.save()

        herramienta.estado_fisico = estado_fisico_final
        herramienta.estado_operativo = (
            Estado.FUERA_DE_SERVICIO if dejar_fuera_de_servicio else Estado.DISPONIBLE
        )
        herramienta.save(update_fields=['estado_fisico', 'estado_operativo', 'fecha_actualizacion'])

        if registro.plan_id and not dejar_fuera_de_servicio:
            plan = PlanMantenimientoHerramienta.objects.select_for_update().get(pk=registro.plan_id)
            plan.ultima_fecha = timezone.localdate()
            plan.ultimas_horas = herramienta.horas_uso_acumuladas
            plan.save()

        _historial(
            herramienta, HistorialHerramienta.Accion.MANTENIMIENTO_FIN,
            f'{registro.get_tipo_display()} finalizado. Resultado: {resultado}'
            + (' Queda fuera de servicio.' if dejar_fuera_de_servicio else ''),
            usuario,
        )
    logger.info('Mantenimiento %s finalizado por usuario %s', registro.pk, usuario.pk)
    return registro


def cancelar_mantenimiento(*, registro_id, usuario, motivo):
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError({'motivo': 'El motivo es obligatorio.'})
    with transaction.atomic():
        registro = _bloquear_registro(registro_id)
        if registro.estado != RegistroMantenimiento.Estado.EN_CURSO:
            raise ConflictoAsignacion('Solo se puede cancelar un mantenimiento en curso.')
        herramienta = Herramienta.objects.select_for_update().get(pk=registro.herramienta_id)

        registro.estado = RegistroMantenimiento.Estado.CANCELADO
        registro.motivo_cancelacion = motivo
        registro.fecha_fin = timezone.now()
        registro.cerrado_por = usuario
        registro.save()

        if herramienta.estado_operativo in (Estado.EN_MANTENIMIENTO, Estado.EN_REPARACION):
            herramienta.estado_operativo = (
                Estado.FUERA_DE_SERVICIO
                if herramienta.estado_fisico == Herramienta.EstadoFisico.MALO else Estado.DISPONIBLE
            )
            herramienta.save(update_fields=['estado_operativo', 'fecha_actualizacion'])
        _historial(
            herramienta, HistorialHerramienta.Accion.MANTENIMIENTO_FIN,
            f'Mantenimiento cancelado. Motivo: {motivo}', usuario,
        )
    return registro


def tiene_mantenimiento_en_curso(herramienta):
    return herramienta.mantenimientos.filter(estado=RegistroMantenimiento.Estado.EN_CURSO).exists()


# ───────────── Incidencias ─────────────

def crear_incidencia(*, herramienta_id, tipo, descripcion, usuario, solo_propias=False):
    """Registra una incidencia. Con `solo_propias` (técnico con alcance propio) la herramienta
    debe estar asignada al propio usuario."""
    try:
        herramienta = Herramienta.objects.get(pk=herramienta_id, estado=True)
    except Herramienta.DoesNotExist:
        raise ValidationError({'herramienta': 'La herramienta no existe.'})
    asignacion = (
        AsignacionHerramienta.objects.filter(
            herramienta=herramienta, estado=AsignacionHerramienta.Estado.ACTIVA
        ).select_related('tecnico').first()
    )
    if solo_propias and (asignacion is None or asignacion.tecnico_id != usuario.pk):
        raise ValidationError({'herramienta': 'Solo puede reportar incidencias de herramientas asignadas a usted.'})

    with transaction.atomic():
        incidencia = IncidenciaHerramienta.objects.create(
            herramienta=herramienta, tipo=tipo, descripcion=descripcion,
            responsable=asignacion.tecnico if asignacion else None, reportada_por=usuario,
        )
        _historial(
            herramienta, HistorialHerramienta.Accion.INCIDENCIA,
            f'{incidencia.get_tipo_display()}: {descripcion}', usuario,
        )
    logger.info('Incidencia %s creada (herramienta %s) por usuario %s',
                incidencia.pk, herramienta.codigo, usuario.pk)
    return incidencia


def resolver_incidencia(*, incidencia_id, usuario, decision, notas='', puede_dar_baja=False):
    D = IncidenciaHerramienta.Decision
    with transaction.atomic():
        try:
            incidencia = IncidenciaHerramienta.objects.select_for_update(of=('self',)).get(pk=incidencia_id)
        except IncidenciaHerramienta.DoesNotExist:
            raise Http404('Incidencia no encontrada.')
        if incidencia.estado != IncidenciaHerramienta.Estado.ABIERTA:
            raise ConflictoAsignacion('La incidencia ya fue resuelta.')
        herramienta = Herramienta.objects.select_for_update().get(pk=incidencia.herramienta_id)
        perdida = incidencia.tipo in (IncidenciaHerramienta.Tipo.ROBO, IncidenciaHerramienta.Tipo.PERDIDA)
        detalle = f'Incidencia resuelta ({incidencia.get_tipo_display()}): {decision}.'

        if decision == D.REPARAR:
            if perdida:
                raise ValidationError({'decision': 'No se puede reparar una herramienta robada o perdida.'})
            if herramienta.estado_operativo == Estado.ASIGNADA:
                raise ConflictoAsignacion('La herramienta está asignada; registre su devolución primero.')
            _abrir_mantenimiento(
                herramienta, tipo=RegistroMantenimiento.Tipo.CORRECTIVO,
                descripcion=incidencia.descripcion, plan=None, usuario=usuario,
            )
            detalle += ' Se abrió una reparación.'
        elif decision == D.DAR_DE_BAJA:
            if not puede_dar_baja:
                raise PermissionDenied('No tiene permiso para dar de baja una herramienta.')
            if herramienta.estado_operativo in ESTADOS_TERMINALES:
                raise ConflictoAsignacion('La herramienta ya está dada de baja o perdida.')
            if tiene_mantenimiento_en_curso(herramienta):
                raise ConflictoAsignacion('Finalice o cancele el mantenimiento en curso primero.')
            asignacion = AsignacionHerramienta.objects.select_for_update().filter(
                herramienta=herramienta, estado=AsignacionHerramienta.Estado.ACTIVA
            ).first()
            if asignacion:
                if not perdida:
                    raise ConflictoAsignacion('La herramienta está asignada; registre su devolución primero.')
                asignacion.estado = AsignacionHerramienta.Estado.NO_DEVUELTA
                asignacion.observaciones_devolucion = f'Cerrada por incidencia #{incidencia.pk}.'
                asignacion.save()
            herramienta.estado_operativo = Estado.PERDIDA if perdida else Estado.DADA_DE_BAJA
            herramienta.save(update_fields=['estado_operativo', 'fecha_actualizacion'])
            detalle += f' Estado: {herramienta.get_estado_operativo_display()}.'

        incidencia.estado = IncidenciaHerramienta.Estado.RESUELTA
        incidencia.decision = decision
        incidencia.notas_resolucion = notas
        incidencia.resuelta_por = usuario
        incidencia.fecha_resolucion = timezone.now()
        incidencia.save()
        _historial(herramienta, HistorialHerramienta.Accion.INCIDENCIA, detalle, usuario)
    logger.info('Incidencia %s resuelta (%s) por usuario %s', incidencia.pk, decision, usuario.pk)
    return incidencia
