"""Reglas de negocio de asignaciones (entrega, devolución, anulación).

Todo lo que cambia estado pasa por aquí, dentro de una transacción y con la
herramienta bloqueada (`select_for_update`) para evitar entregas dobles.
"""
import logging
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from apps.seguridad.models import Usuario

from .models import AsignacionHerramienta, Herramienta, HistorialHerramienta

logger = logging.getLogger(__name__)

ROLES_ASIGNABLES_POR_DEFECTO = ('TÉCNICO_AUTOMOTRIZ',)


class ConflictoAsignacion(APIException):
    status_code = 409
    default_detail = 'La operación entra en conflicto con el estado actual.'
    default_code = 'conflicto'


def roles_asignables():
    """Códigos de rol a los que se pueden entregar herramientas (configurable en settings)."""
    return tuple(getattr(settings, 'HERRAMIENTAS_ROLES_ASIGNABLES', ROLES_ASIGNABLES_POR_DEFECTO))


def tecnicos_asignables():
    """Usuarios activos, no eliminados, con un rol asignable vigente."""
    ahora = timezone.now()
    return (
        Usuario.objects.filter(
            estado='activo',
            fecha_eliminacion__isnull=True,
            usuario_roles__id_rol__codigo__in=roles_asignables(),
            usuario_roles__id_rol__estado=True,
            usuario_roles__estado=True,
        )
        .filter(
            Q(usuario_roles__fecha_expiracion__isnull=True)
            | Q(usuario_roles__fecha_expiracion__gt=ahora)
        )
        .distinct()
        .order_by('apellidos', 'nombres')
    )


def _historial(herramienta, accion, detalle, usuario):
    HistorialHerramienta.objects.create(
        herramienta=herramienta, accion=accion, detalle=detalle, usuario=usuario
    )


def _nombre(usuario):
    return f'{usuario.nombres} {usuario.apellidos}'.strip()


def entregar_herramienta(*, herramienta_id, tecnico_id, entregado_por,
                         fecha_devolucion_esperada=None, observaciones='',
                         autorizar_excepcion=False, motivo_excepcion=''):
    """Entrega una herramienta disponible a un técnico. Devuelve (asignacion, advertencias)."""
    with transaction.atomic():
        try:
            herramienta = Herramienta.objects.select_for_update().get(pk=herramienta_id, estado=True)
        except Herramienta.DoesNotExist:
            raise ValidationError({'herramienta': 'La herramienta no existe.'})

        if herramienta.estado_operativo != Herramienta.EstadoOperativo.DISPONIBLE:
            raise ConflictoAsignacion(
                f'La herramienta {herramienta.codigo} no está disponible '
                f'(estado: {herramienta.get_estado_operativo_display()}).'
            )

        vencidos = [
            p for p in herramienta.planes_mantenimiento.filter(activo=True).select_related('herramienta')
            if p.estado_vencimiento() == 'VENCIDO'
        ]
        if vencidos and not autorizar_excepcion:
            nombres = ', '.join(p.nombre for p in vencidos)
            raise ConflictoAsignacion({
                'detail': f'La herramienta {herramienta.codigo} tiene mantenimiento vencido: {nombres}.',
                'codigo': 'MANTENIMIENTO_VENCIDO',
            })

        tecnico = tecnicos_asignables().filter(pk=tecnico_id).first()
        if tecnico is None:
            raise ValidationError(
                {'tecnico': 'El usuario no existe, está inactivo o no tiene un rol al que se puedan entregar herramientas.'}
            )

        advertencias = []
        sucursales = set(tecnico.sucursales_asignadas.values_list('sucursal_id', flat=True))
        if sucursales and herramienta.sucursal_id not in sucursales:
            advertencias.append(
                f'El técnico no tiene asignada la sucursal de la herramienta ({herramienta.sucursal.nombre}).'
            )

        try:
            asignacion = AsignacionHerramienta.objects.create(
                herramienta=herramienta,
                tecnico=tecnico,
                entregado_por=entregado_por,
                fecha_devolucion_esperada=fecha_devolucion_esperada,
                estado_fisico_entrega=herramienta.estado_fisico,
                observaciones_entrega=observaciones,
            )
        except IntegrityError:
            # La restricción única es la red de seguridad si dos entregas chocan.
            raise ConflictoAsignacion('La herramienta ya tiene una asignación activa.')

        herramienta.estado_operativo = Herramienta.EstadoOperativo.ASIGNADA
        herramienta.save(update_fields=['estado_operativo', 'fecha_actualizacion'])
        _historial(
            herramienta, HistorialHerramienta.Accion.ENTREGA,
            f'Entregada a {_nombre(tecnico)}.'
            + (f' Devolución esperada: {fecha_devolucion_esperada}.' if fecha_devolucion_esperada else '')
            + (f' EXCEPCIÓN por mantenimiento vencido. Motivo: {motivo_excepcion}.' if vencidos else ''),
            entregado_por,
        )
    logger.info(
        'Herramienta %s entregada a usuario %s por usuario %s',
        herramienta.codigo, tecnico.pk, entregado_por.pk,
    )
    return asignacion, advertencias


def devolver_herramienta(*, asignacion_id, recibido_por, estado_fisico_devolucion,
                         horas_uso_periodo=Decimal('0'), requiere_mantenimiento=False,
                         observaciones=''):
    """Registra la devolución: suma horas de uso y deja la herramienta disponible o en mantenimiento."""
    with transaction.atomic():
        asignacion = _bloquear_asignacion(asignacion_id)
        if asignacion.estado != AsignacionHerramienta.Estado.ACTIVA:
            raise ConflictoAsignacion('Solo se puede devolver una asignación activa.')

        herramienta = Herramienta.objects.select_for_update().get(pk=asignacion.herramienta_id)
        a_mantenimiento = (
            requiere_mantenimiento
            or estado_fisico_devolucion == Herramienta.EstadoFisico.MALO
        )

        asignacion.estado = AsignacionHerramienta.Estado.DEVUELTA
        asignacion.fecha_devolucion_real = timezone.now()
        asignacion.recibido_por = recibido_por
        asignacion.estado_fisico_devolucion = estado_fisico_devolucion
        asignacion.horas_uso_periodo = horas_uso_periodo
        asignacion.observaciones_devolucion = observaciones
        asignacion.save()

        herramienta.horas_uso_acumuladas = herramienta.horas_uso_acumuladas + horas_uso_periodo
        herramienta.estado_fisico = estado_fisico_devolucion
        herramienta.estado_operativo = (
            Herramienta.EstadoOperativo.EN_MANTENIMIENTO if a_mantenimiento
            else Herramienta.EstadoOperativo.DISPONIBLE
        )
        herramienta.save(update_fields=[
            'horas_uso_acumuladas', 'estado_fisico', 'estado_operativo', 'fecha_actualizacion',
        ])
        _historial(
            herramienta, HistorialHerramienta.Accion.DEVOLUCION,
            f'Devuelta por {_nombre(asignacion.tecnico)}. Estado físico: {estado_fisico_devolucion}. '
            f'Horas de uso: {horas_uso_periodo}.'
            + (' Pasa a mantenimiento.' if a_mantenimiento else ''),
            recibido_por,
        )
    logger.info(
        'Asignación %s devuelta (herramienta %s) por usuario %s',
        asignacion.pk, herramienta.codigo, recibido_por.pk,
    )
    return asignacion


def anular_asignacion(*, asignacion_id, usuario, motivo):
    """Anula una entrega registrada por error: la herramienta vuelve a estar disponible."""
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError({'motivo': 'El motivo es obligatorio.'})
    with transaction.atomic():
        asignacion = _bloquear_asignacion(asignacion_id)
        if asignacion.estado != AsignacionHerramienta.Estado.ACTIVA:
            raise ConflictoAsignacion(
                'Solo se puede anular una asignación activa; una ya devuelta forma parte del historial.'
            )
        herramienta = Herramienta.objects.select_for_update().get(pk=asignacion.herramienta_id)

        asignacion.estado = AsignacionHerramienta.Estado.ANULADA
        asignacion.motivo_anulacion = motivo
        asignacion.anulada_por = usuario
        asignacion.fecha_anulacion = timezone.now()
        asignacion.save()

        if herramienta.estado_operativo == Herramienta.EstadoOperativo.ASIGNADA:
            herramienta.estado_operativo = Herramienta.EstadoOperativo.DISPONIBLE
            herramienta.save(update_fields=['estado_operativo', 'fecha_actualizacion'])
        _historial(
            herramienta, HistorialHerramienta.Accion.ANULACION_ASIGNACION,
            f'Asignación a {_nombre(asignacion.tecnico)} anulada. Motivo: {motivo}', usuario,
        )
    logger.info('Asignación %s anulada por usuario %s', asignacion.pk, usuario.pk)
    return asignacion


def _bloquear_asignacion(asignacion_id):
    try:
        return (
            AsignacionHerramienta.objects.select_for_update(of=('self',))
            .select_related('tecnico')
            .get(pk=asignacion_id)
        )
    except AsignacionHerramienta.DoesNotExist:
        from django.http import Http404
        raise Http404('Asignación no encontrada.')
