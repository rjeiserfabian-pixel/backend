import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.inventario.models import InventarioStock, MovimientoInventario

from .models import OrdenHistorialEstado, OrdenRepuesto, OrdenServicio, OrdenTrabajo

logger = logging.getLogger(__name__)


@transaction.atomic
def aprobar_cotizacion_orden(
    orden,
    servicios_ids,
    repuestos_ids,
    usuario=None,
    observaciones=None,
    validar_vencimiento=True,
    exigir_esperando_aprobacion=False,
    exigir_seleccion=False,
):
    """
    Registra la aprobacion de una cotizacion y mantiene sincronizadas las
    reservas de inventario. La usan tanto el panel interno como el portal
    publico del cliente para evitar dos caminos de negocio distintos.
    """
    orden = OrdenTrabajo.objects.select_for_update().get(pk=orden.pk)

    if exigir_esperando_aprobacion and orden.estado != OrdenTrabajo.Estado.ESPERANDO_APROBACION:
        raise ValidationError("La cotizacion ya no esta pendiente de aprobacion.")

    if validar_vencimiento and orden.fecha_vencimiento_cotizacion and timezone.now() > orden.fecha_vencimiento_cotizacion:
        raise ValidationError(
            "La cotizacion ha expirado. Por favor, actualice la fecha de vencimiento en los detalles de la orden para proceder."
        )

    try:
        servicios_ids = [int(item_id) for item_id in (servicios_ids or [])]
        repuestos_ids = [int(item_id) for item_id in (repuestos_ids or [])]
    except (TypeError, ValueError):
        raise ValidationError("La seleccion contiene IDs invalidos.")

    servicios_validos = set(OrdenServicio.objects.filter(orden=orden, id__in=servicios_ids).values_list("id", flat=True))
    repuestos_validos = set(OrdenRepuesto.objects.filter(orden=orden, id__in=repuestos_ids).values_list("id", flat=True))

    if len(servicios_validos) != len(set(servicios_ids)) or len(repuestos_validos) != len(set(repuestos_ids)):
        raise ValidationError("La seleccion contiene items que no pertenecen a esta orden.")

    if exigir_seleccion and not servicios_validos and not repuestos_validos:
        raise ValidationError("Debe seleccionar al menos un servicio o repuesto para aprobar.")

    OrdenServicio.objects.filter(orden=orden, id__in=servicios_validos).update(aprobado_cliente=True)
    OrdenServicio.objects.filter(orden=orden).exclude(id__in=servicios_validos).update(aprobado_cliente=False)

    repuestos_a_aprobar = OrdenRepuesto.objects.filter(orden=orden, id__in=repuestos_validos).select_related("repuesto")
    for orp in repuestos_a_aprobar:
        if not orp.aprobado_cliente:
            stock_record = InventarioStock.objects.select_for_update().filter(
                repuesto=orp.repuesto, stock_disponible__gte=orp.cantidad
            ).first()
            if not stock_record:
                raise ValidationError(
                    f"No hay stock disponible suficiente para el repuesto '{orp.repuesto.nombre}'. "
                    f"Cantidad requerida: {orp.cantidad}."
                )

            orp.aprobado_cliente = True
            orp.save(update_fields=["aprobado_cliente"])

            stock_record.stock_disponible -= orp.cantidad
            stock_record.stock_reservado += orp.cantidad
            stock_record.save()

            MovimientoInventario.objects.create(
                repuesto=orp.repuesto,
                ubicacion=stock_record.ubicacion,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                cantidad=-orp.cantidad,
                stock_resultante=stock_record.stock_disponible,
                motivo=f"Reserva para OT-{orden.numero}",
                usuario=usuario,
                referencia_id=orden.id,
                referencia_tipo="OT",
            )

    repuestos_a_desaprobar = OrdenRepuesto.objects.filter(
        orden=orden, aprobado_cliente=True
    ).exclude(id__in=repuestos_validos).select_related("repuesto")
    for orp in repuestos_a_desaprobar:
        if orp.instalado:
            raise ValidationError(
                f"No se puede quitar la aprobacion del repuesto '{orp.repuesto.codigo}': "
                "ya fue instalado. Revierta la instalacion primero."
            )
        stock_record = InventarioStock.objects.select_for_update().filter(repuesto=orp.repuesto).first()
        if stock_record:
            stock_record.stock_disponible += orp.cantidad
            stock_record.stock_reservado -= orp.cantidad
            stock_record.save()

            MovimientoInventario.objects.create(
                repuesto=orp.repuesto,
                ubicacion=stock_record.ubicacion,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                cantidad=orp.cantidad,
                stock_resultante=stock_record.stock_disponible,
                motivo=f"Liberacion de reserva por desaprobacion en OT-{orden.numero}",
                usuario=usuario,
                referencia_id=orden.id,
                referencia_tipo="OT",
            )

    OrdenRepuesto.objects.filter(orden=orden).exclude(id__in=repuestos_validos).update(aprobado_cliente=False)

    orden.estado = OrdenTrabajo.Estado.APROBADO
    orden.save(update_fields=["estado"])

    OrdenHistorialEstado.objects.create(
        orden=orden,
        estado=orden.estado,
        usuario=usuario,
        observaciones=observaciones,
    )

    logger.info(
        "OT-%s aprobada. Servicios: %s, Repuestos: %s, Usuario: %s",
        orden.numero,
        sorted(servicios_validos),
        sorted(repuestos_validos),
        usuario,
    )

    return orden
