import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo

def run():
    try:
        inventario = Modulo.objects.filter(nombre__icontains='Inventario').first()
        if not inventario:
            print("Módulo INVENTARIO no encontrado.")
            return
            
        print(f"Inventario encontrado: {inventario.codigo}")
        
        m, created = Modulo.objects.get_or_create(
            codigo='UNIDADES',
            defaults={
                'id_modulo_padre': inventario,
                'nombre': 'Unidades',
                'icono': 'package', 
                'ruta': '/inventario/unidades',
                'orden': 35, # after marcas (which might be 20 or 30)
                'visible_menu': True,
                'estado': True
            }
        )
        if not created:
            m.id_modulo_padre = inventario
            m.ruta = '/inventario/unidades'
            m.icono = 'package'
            m.save()
            print("Módulo Unidades actualizado bajo Inventario.")
        else:
            print("Módulo Unidades creado bajo Inventario.")
            
    except Exception as e:
        print("Error:", e)

if __name__ == '__main__':
    run()
