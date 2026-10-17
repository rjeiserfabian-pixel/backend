from django.db import migrations, models
import django.db.models.deletion


def completar(apps, schema_editor):
    Cuenta = apps.get_model('ventas', 'CuentaPorCobrar')
    alias = schema_editor.connection.alias
    for cuenta in Cuenta.objects.using(alias).select_related('venta', 'venta__vehiculo').iterator(chunk_size=500):
        venta = cuenta.venta
        Cuenta.objects.using(alias).filter(pk=cuenta.pk).update(
            cliente_id=venta.cliente_id, sucursal_id=venta.sucursal_id,
            vehiculo_id=venta.vehiculo_id, moneda=venta.moneda,
            placa_original=venta.vehiculo.placa if venta.vehiculo_id else '',
        )
    # PostgreSQL difiere comprobaciones FK hasta el fin de la transacción.
    # Resolverlas antes de ALTER FIELD evita eventos de disparador pendientes
    # cuando la migración se ejecuta sobre cuentas ya existentes.
    if schema_editor.connection.vendor == 'postgresql':
        with schema_editor.connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def impedir_reversion(apps, schema_editor):
    if apps.get_model('ventas', 'CuentaPorCobrar').objects.using(schema_editor.connection.alias).filter(origen='CREDITO_ANTERIOR').exists():
        raise RuntimeError('Existen créditos anteriores. Conserve la estructura compatible y corrija hacia adelante; no elimine las deudas para revertir.')


class Migration(migrations.Migration):
    dependencies = [('ventas', '0023_detallecreditoanterior_and_more')]
    operations = [
        migrations.RunPython(completar, impedir_reversion),
        migrations.AlterField(model_name='cuentaporcobrar', name='cliente', field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='cuentas_cobrar', to='clientes.cliente')),
        migrations.AlterField(model_name='cuentaporcobrar', name='sucursal', field=models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='cuentas_cobrar', to='inventario.sucursal')),
    ]
