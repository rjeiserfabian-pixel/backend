import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')

import django
django.setup()

from apps.seguridad.models import Modulo

def run():
    # Obtener el módulo padre "Configuración"
    try:
        config_modulo = Modulo.objects.get(codigo='CONFIG')
    except Modulo.DoesNotExist:
        print("Error: El módulo padre 'Configuracion' (CONFIG) no existe.")
        return

    # Crear el submódulo "Cuentas Bancarias"
    cuentas_modulo, created = Modulo.objects.get_or_create(
        codigo='CUENTAS_BANCARIAS',
        defaults={
            'nombre': 'Cuentas Bancarias',
            'icono': 'credit-card', # icono de lucide
            'ruta': '/seguridad/cuentas-bancarias',
            'id_modulo_padre': config_modulo,
            'orden': 40,
            'visible_menu': True,
            'estado': True
        }
    )
    
    if not created:
        cuentas_modulo.nombre = 'Cuentas Bancarias'
        cuentas_modulo.icono = 'credit-card'
        cuentas_modulo.ruta = '/seguridad/cuentas-bancarias'
        cuentas_modulo.id_modulo_padre = config_modulo
        cuentas_modulo.save()
        print("Módulo 'Cuentas Bancarias' actualizado.")
    else:
        print("Módulo 'Cuentas Bancarias' creado.")

if __name__ == '__main__':
    run()
