import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo

def run():
    try:
        # Fetching Inventario by code or just filtering
        inventario = Modulo.objects.filter(nombre__icontains='Inventario').first()
        if not inventario:
            print("Módulo INVENTARIO no encontrado. Listando todos los módulos:")
            for m in Modulo.objects.filter(id_modulo_padre__isnull=True):
                print(f"- {m.codigo}: {m.nombre}")
            return
            
        print(f"Inventario encontrado: {inventario.codigo}")
        
        # Create or update IGV module
        igv, created = Modulo.objects.get_or_create(
            codigo='IMPUESTOS',
            defaults={
                'id_modulo_padre': inventario,
                'nombre': 'Impuestos (IGV)',
                'icono': 'tags', 
                'ruta': '/inventario/impuestos',
                'orden': 90,
                'visible_menu': True,
                'estado': True
            }
        )
        if not created:
            igv.id_modulo_padre = inventario
            igv.ruta = '/inventario/impuestos'
            igv.icono = 'tags'
            igv.save()
            print("Módulo Impuestos (IGV) actualizado bajo Inventario.")
        else:
            print("Módulo Impuestos (IGV) creado bajo Inventario.")
            
    except Exception as e:
        print("Error:", e)

if __name__ == '__main__':
    run()
