import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')

import django
django.setup()

from apps.seguridad.models import Modulo

def run():
    # 1. Crear el módulo padre "Configuración"
    config_modulo, created = Modulo.objects.get_or_create(
        codigo='CONFIG',
        defaults={
            'nombre': 'Configuracion',
            'icono': 'settings',
            'ruta': None,
            'orden': 90,
            'visible_menu': True,
            'estado': True
        }
    )
    if not created:
        config_modulo.nombre = 'Configuracion'
        config_modulo.icono = 'settings'
        config_modulo.save()
    
    print(f"Módulo padre 'Configuracion' listo con ID: {config_modulo.id_modulo}")

    # 2. Actualizar 'EMPRESA'
    try:
        empresa_mod = Modulo.objects.get(codigo='EMPRESA')
        empresa_mod.id_modulo_padre = config_modulo
        empresa_mod.nombre = 'Empresa'
        empresa_mod.save()
        print("Módulo 'Empresa' actualizado y asignado a 'Configuracion'.")
    except Modulo.DoesNotExist:
        print("Módulo EMPRESA no encontrado.")

    # 3. Actualizar 'UBIGEO'
    try:
        ubigeo_mod = Modulo.objects.get(codigo='UBIGEO')
        ubigeo_mod.id_modulo_padre = config_modulo
        ubigeo_mod.save()
        print("Módulo 'Ubicaciones' actualizado y asignado a 'Configuracion'.")
    except Modulo.DoesNotExist:
        print("Módulo UBIGEO no encontrado.")

if __name__ == '__main__':
    run()
