import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()
from apps.ventas.models import MovimientoCaja
print('Todos los conceptos y origenes distintos:')
for row in MovimientoCaja.objects.values('concepto', 'origen_movimiento').distinct().order_by('concepto'):
    count = MovimientoCaja.objects.filter(concepto=row['concepto'], origen_movimiento=row['origen_movimiento']).count()
    print(f"  concepto={row['concepto']:25s} | origen={row['origen_movimiento']:20s} | ({count})")
