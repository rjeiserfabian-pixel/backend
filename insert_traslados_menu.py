import os
import sys

# Asegurar que Django se inicialice correctamente desde el directorio raíz
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')

import django
django.setup()

from apps.seguridad.models import Modulo

def run():
    try:
        # Fetching Inventario by code or just filtering
        inventario = Modulo.objects.filter(nombre__icontains='Inventario').first()
        if not inventario:
            print("Módulo INVENTARIO no encontrado.")
            return

        m, created = Modulo.objects.get_or_create(
            codigo='TRASLADOS_ALMACEN',
            defaults={
                'nombre': 'Movimiento de Almacén',
                'icono': 'ArrowRightLeft',
                'id_modulo_padre': inventario,
                'ruta': '/inventario/traslados',
                'orden': 45,  # Entre Kardex y Almacenes (o similar)
                'visible_menu': True,
                'estado': True
            }
        )
        if not created:
            m.nombre = 'Movimiento de Almacén'
            m.icono = 'ArrowRightLeft'
            m.id_modulo_padre = inventario
            m.ruta = '/inventario/traslados'
            m.orden = 45
            m.visible_menu = True
            m.save()
            print("Módulo Movimiento de Almacén actualizado bajo Inventario.")
        else:
            print("Módulo Movimiento de Almacén creado bajo Inventario.")
            
    except Exception as e:
        print(f"Error al insertar el menú: {str(e)}")

if __name__ == '__main__':
    run()
