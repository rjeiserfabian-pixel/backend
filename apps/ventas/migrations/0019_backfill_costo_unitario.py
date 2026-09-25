from django.db import migrations


def backfill_costo_unitario(apps, schema_editor):
    """
    Rellena costo_unitario en filas de DetalleVenta creadas antes de este
    campo, usando el precio_compra ACTUAL del repuesto como mejor
    aproximación disponible (no es el costo histórico real si el precio de
    compra cambió desde entonces, pero es preferible a dejarlo vacío en el
    reporte de utilidad). Las líneas de servicio (repuesto null) se dejan
    tal cual, no tienen costo.
    """
    DetalleVenta = apps.get_model('ventas', 'DetalleVenta')

    detalles = list(
        DetalleVenta.objects.filter(repuesto__isnull=False, costo_unitario__isnull=True)
        .select_related('repuesto')
    )
    for detalle in detalles:
        detalle.costo_unitario = detalle.repuesto.precio_compra
    DetalleVenta.objects.bulk_update(detalles, ['costo_unitario'], batch_size=500)


def revertir_backfill(apps, schema_editor):
    # No reversible con precisión (no sabemos cuáles filas tenían el campo
    # vacío antes de esta migración); no hace falta revertir el backfill
    # para poder deshacer el AddField de la migración anterior.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ventas', '0018_detalleventa_costo_unitario'),
    ]

    operations = [
        migrations.RunPython(backfill_costo_unitario, revertir_backfill),
    ]
