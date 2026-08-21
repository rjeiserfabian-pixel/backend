"""
Migración de datos: Inventario Inicial
=======================================
Crea una Sucursal, Almacén y Ubicación física por defecto,
y transfiere el stock actual de cada Repuesto al nuevo modelo
InventarioStock, generando el primer movimiento de Kardex como
"INVENTARIO_INICIAL" para cada uno.

NOTA: El campo `stock` en Repuesto se mantiene como legacy y no se
elimina en esta migración. Una vez validado que InventarioStock es
la fuente de verdad, se puede crear una migración posterior para
removerlo si se desea.
"""
from django.db import migrations


def crear_datos_iniciales(apps, schema_editor):
    Sucursal = apps.get_model('inventario', 'Sucursal')
    Almacen = apps.get_model('inventario', 'Almacen')
    UbicacionFisica = apps.get_model('inventario', 'UbicacionFisica')
    InventarioStock = apps.get_model('inventario', 'InventarioStock')
    MovimientoInventario = apps.get_model('inventario', 'MovimientoInventario')
    Repuesto = apps.get_model('inventario', 'Repuesto')

    # 1. Crear la Sucursal Principal por defecto
    sucursal, _ = Sucursal.objects.get_or_create(
        nombre='Sucursal Principal',
        defaults={'direccion': 'Dirección de la empresa', 'estado': True}
    )

    # 2. Crear el Almacén Principal por defecto
    almacen, _ = Almacen.objects.get_or_create(
        sucursal=sucursal,
        nombre='Almacén Principal',
        defaults={'descripcion': 'Almacén creado automáticamente durante la migración inicial', 'estado': True}
    )

    # 3. Crear la Ubicación General por defecto
    ubicacion, _ = UbicacionFisica.objects.get_or_create(
        almacen=almacen,
        codigo='GENERAL',
        defaults={'descripcion': 'Ubicación general del almacén principal'}
    )

    # 4. Por cada repuesto existente, crear su InventarioStock y su primer movimiento de Kardex
    repuestos = Repuesto.objects.all()

    stock_a_crear = []
    movimientos_a_crear = []

    for repuesto in repuestos:
        stock_actual = repuesto.stock or 0

        # Crear el registro de stock en la nueva tabla (evitar duplicados con get_or_create)
        inv_stock, creado = InventarioStock.objects.get_or_create(
            repuesto=repuesto,
            ubicacion=ubicacion,
            defaults={'stock_disponible': stock_actual}
        )

        if not creado:
            # Si ya existía (re-runs), actualizamos
            inv_stock.stock_disponible = stock_actual
            inv_stock.save()

        # Solo crear el movimiento de Kardex si hay stock para registrar
        if stock_actual > 0:
            movimientos_a_crear.append(
                MovimientoInventario(
                    repuesto=repuesto,
                    ubicacion=ubicacion,
                    tipo_movimiento='INVENTARIO_INICIAL',
                    cantidad=stock_actual,
                    stock_resultante=stock_actual,
                    motivo='Inventario inicial - Migración automática del sistema',
                    usuario=None,  # Movimiento del sistema
                )
            )

    # Inserción masiva para eficiencia (bulk_create en lugar de bucle con .create())
    if movimientos_a_crear:
        MovimientoInventario.objects.bulk_create(movimientos_a_crear)


def revertir_datos_iniciales(apps, schema_editor):
    """
    Reversión: elimina los datos creados por esta migración.
    Los datos legacy en Repuesto.stock no se tocan.
    """
    InventarioStock = apps.get_model('inventario', 'InventarioStock')
    MovimientoInventario = apps.get_model('inventario', 'MovimientoInventario')
    UbicacionFisica = apps.get_model('inventario', 'UbicacionFisica')
    Almacen = apps.get_model('inventario', 'Almacen')
    Sucursal = apps.get_model('inventario', 'Sucursal')

    MovimientoInventario.objects.filter(motivo__contains='Migración automática').delete()
    InventarioStock.objects.all().delete()
    UbicacionFisica.objects.filter(codigo='GENERAL').delete()
    Almacen.objects.filter(nombre='Almacén Principal').delete()
    Sucursal.objects.filter(nombre='Sucursal Principal').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('inventario', '0002_multi_almacen_kardex'),
    ]

    operations = [
        migrations.RunPython(crear_datos_iniciales, revertir_datos_iniciales),
    ]
