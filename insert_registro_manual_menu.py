import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
try:
    django.setup()
except Exception as e:
    # If taller_core.settings fails, try config.settings just in case
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()

from apps.seguridad.models import Modulo

def insert_menu():
    padre = Modulo.objects.filter(codigo='VENTAS').first()
    if not padre:
        padre = Modulo.objects.filter(nombre__icontains='Venta').first()
    
    if padre:
        modulo, created = Modulo.objects.get_or_create(
            codigo='REGISTRO_MANUAL_VENTAS',
            defaults={
                'id_modulo_padre': padre,
                'nombre': 'Registro Manual',
                'icono': 'fileText',
                'ruta': '/ventas/registro-manual',
                'orden': 99,
                'visible_menu': True,
                'estado': True
            }
        )
        if created:
            print("Módulo registrado con éxito bajo padre:", padre.nombre)
        else:
            print("Módulo ya existía. Actualizando datos...")
            modulo.id_modulo_padre = padre
            modulo.nombre = 'Registro Manual'
            modulo.icono = 'fileText'
            modulo.ruta = '/ventas/registro-manual'
            modulo.visible_menu = True
            modulo.estado = True
            modulo.save()
            print("Módulo actualizado con éxito.")
    else:
        print("No se encontró el módulo padre Ventas.")

if __name__ == '__main__':
    insert_menu()
