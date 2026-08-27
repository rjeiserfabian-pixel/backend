import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')

import django
django.setup()

from apps.seguridad.models import Modulo

def run():
    empresa, created = Modulo.objects.get_or_create(
        codigo='EMPRESA',
        defaults={
            'nombre': 'Configuración de Empresa',
            'icono': 'settings',
            'ruta': '/seguridad/empresa',
            'orden': 98,
            'visible_menu': True,
            'estado': True
        }
    )
    if not created:
        empresa.ruta = '/seguridad/empresa'
        empresa.icono = 'settings'
        empresa.visible_menu = True
        empresa.save()

    print("Módulo de Configuración de Empresa insertado correctamente en la BD.")

if __name__ == '__main__':
    run()
