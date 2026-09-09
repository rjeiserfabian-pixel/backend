"""
fix_movimientos_origen.py
Corrige el campo 'origen_movimiento' de los MovimientoCaja históricos
que quedaron como AJUSTE_MANUAL por defecto antes de la migración de Cajas.

Mapeo:
  concepto=COBRO_CUOTA       → origen=COBRO
  concepto=PAGO_VENTA        → origen=VENTA
  concepto=APERTURA          → origen=APERTURA
  todo lo demás              → se deja como AJUSTE_MANUAL (correcto)
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.ventas.models import MovimientoCaja

MAPEO = {
    'COBRO_CUOTA':        'COBRO',
    'PAGO_VENTA':         'VENTA',
    'VENTA':              'VENTA',
    'APERTURA':           'APERTURA',
}

total = 0
for concepto, nuevo_origen in MAPEO.items():
    qs = MovimientoCaja.objects.filter(
        concepto=concepto,
        origen_movimiento='AJUSTE_MANUAL',
    )
    count = qs.count()
    if count > 0:
        qs.update(origen_movimiento=nuevo_origen)
        print(f"[OK] concepto={concepto} -> origen_movimiento={nuevo_origen} ({count} registros actualizados)")
        total += count
    else:
        print(f"[-]  concepto={concepto} sin registros que corregir")

print(f"\nTotal: {total} movimientos corregidos.")
