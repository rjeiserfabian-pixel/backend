import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.ventas.models import MovimientoCaja

print("Primeros 10 movimientos en la base de datos:")
for m in MovimientoCaja.objects.select_related('sesion__caja').order_by('-fecha')[:10]:
    print(f"  ID={m.id} | tipo={m.tipo} | concepto={m.concepto} | origen={m.origen_movimiento} | estado={m.estado_movimiento} | monto={m.monto} | referencia={m.referencia_origen}")

print("\nChoices de origen_movimiento disponibles:")
print(MovimientoCaja.OrigenMovimiento.choices)
