import os
import sys

# Asegurar que Django se inicialice correctamente desde el directorio raíz
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')

import django
django.setup()

from apps.seguridad.models import Modulo

def run():
    Modulo.objects.get_or_create(
        codigo='CLIENTES',
        defaults={
            'nombre': 'Clientes',
            'icono': 'Users',
            'ruta': '/clientes',
            'orden': 10,
            'visible_menu': True,
            'estado': True
        }
    )
    
    # Verificar si vehiculos ya existe para actualizarlo
    vehiculo, created = Modulo.objects.get_or_create(
        codigo='VEHICULOS',
        defaults={
            'nombre': 'Vehículos',
            'icono': 'Car',
            'ruta': '/vehiculos',
            'orden': 20,
            'visible_menu': True,
            'estado': True
        }
    )
    if not created:
        vehiculo.ruta = '/vehiculos'
        vehiculo.icono = 'Car'
        vehiculo.visible_menu = True
        vehiculo.save()

    print("Módulos de Clientes y Vehículos insertados correctamente en la BD.")

if __name__ == '__main__':
    run()
