import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.ventas.models import Caja, SesionCaja

print(f"Total cajas: {Caja.objects.count()}")
for c in Caja.objects.all():
    print(f"Caja: {c.nombre}, Estado: {c.estado}, Tipo: {c.tipo}, Sucursal: {c.sucursal_id}")

print(f"Total sesiones abiertas: {SesionCaja.objects.filter(estado='ABIERTA').count()}")
for s in SesionCaja.objects.filter(estado='ABIERTA'):
    print(f"Sesion: {s.id}, Caja: {s.caja.nombre}, Estado: {s.estado}")
