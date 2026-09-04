import os
import django
import sys

# Configurar el entorno de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo

def insert_menu():
    try:
        padre = Modulo.objects.filter(nombre='Contactos').first()
        if not padre:
            print("Error: No se encontró el módulo padre 'Contactos'")
            return

        modulo, created = Modulo.objects.get_or_create(
            codigo='TRANSPORTISTAS',
            defaults={
                'nombre': 'Transportistas',
                'ruta': '/contactos/transportistas',
                'icono': 'package',
                'id_modulo_padre': padre,
                'orden': 3
            }
        )
        if created:
            print("Módulo 'Transportistas' creado exitosamente.")
        else:
            print("Módulo 'Transportistas' ya existía.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    insert_menu()
