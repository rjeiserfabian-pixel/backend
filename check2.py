import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()
from apps.ventas.models import MovimientoCaja
print('Conceptos distintos con origen=AJUSTE_MANUAL:')
for row in MovimientoCaja.objects.filter(origen_movimiento='AJUSTE_MANUAL').values('concepto').distinct():
    c = row['concepto']
    count = MovimientoCaja.objects.filter(origen_movimiento='AJUSTE_MANUAL', concepto=c).count()
    print(f'  concepto={c} ({count} registros)')
