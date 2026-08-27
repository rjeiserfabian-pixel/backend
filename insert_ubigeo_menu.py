import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')

import django
django.setup()

from apps.seguridad.models import Modulo

def run():
    ubigeo, created = Modulo.objects.get_or_create(
        codigo='UBIGEO',
        defaults={
            'nombre': 'Ubicaciones',
            'icono': 'map-pin',
            'ruta': '/seguridad/ubigeo',
            'orden': 95,
            'visible_menu': True,
            'estado': True
        }
    )
    if not created:
        ubigeo.ruta = '/seguridad/ubigeo'
        ubigeo.icono = 'map-pin'
        ubigeo.visible_menu = True
        ubigeo.save()

    print("Módulo de Ubicaciones insertado correctamente en la BD.")

if __name__ == '__main__':
    run()
