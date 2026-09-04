import os
import django
import sys

# Configurar el entorno de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo

def insert_menu():
    try:
        padre = Modulo.objects.filter(codigo='CONFIG').first()
        if not padre:
            print("Error: No se encontró el módulo padre 'Configuración'")
            return

        modulo, created = Modulo.objects.get_or_create(
            codigo='VEHICULOS_TRANSPORTE',
            defaults={
                'nombre': 'Vehículos de Transporte',
                'ruta': '/configuracion/vehiculos-transporte',
                'icono': 'truck',
                'id_modulo_padre': padre,
                'orden': 10
            }
        )
        if created:
            print("Módulo 'Vehículos de Transporte' creado exitosamente.")
        else:
            print("Módulo 'Vehículos de Transporte' ya existía.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    insert_menu()
