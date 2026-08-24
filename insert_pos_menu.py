import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo

def run():
    try:
        ventas_padre = Modulo.objects.filter(nombre__icontains='Ventas y Caja').first()
        if not ventas_padre:
            print("Módulo 'Ventas y Caja' no encontrado. Listando todos los módulos principales:")
            for m in Modulo.objects.filter(id_modulo_padre__isnull=True):
                print(f"- {m.codigo}: {m.nombre}")
            
            # Buscando por codigo
            ventas_padre = Modulo.objects.filter(codigo='VENTAS').first()
            
        if not ventas_padre:
            return

        print(f"Módulo Ventas Padre encontrado: {ventas_padre.nombre}")
        
        # 1. Modificar Caja
        caja, created = Modulo.objects.get_or_create(
            codigo='CAJA',
            defaults={
                'id_modulo_padre': ventas_padre,
                'nombre': 'Gestión de Caja',
                'icono': 'Banknote',
                'ruta': '/caja',
                'orden': 10,
                'visible_menu': True,
                'estado': True
            }
        )
        if not created:
            caja.nombre = 'Gestión de Caja'
            caja.icono = 'Banknote'
            caja.ruta = '/caja'
            caja.id_modulo_padre = ventas_padre
            caja.save()
            
        # 2. Modificar/Crear Punto de Venta
        pos, created = Modulo.objects.get_or_create(
            codigo='POS',
            defaults={
                'id_modulo_padre': ventas_padre,
                'nombre': 'Punto de Venta (POS)',
                'icono': 'Store',
                'ruta': '/ventas/pos',
                'orden': 15,
                'visible_menu': True,
                'estado': True
            }
        )
        if not created:
            pos.nombre = 'Punto de Venta (POS)'
            pos.icono = 'Store'
            pos.ruta = '/ventas/pos'
            pos.id_modulo_padre = ventas_padre
            pos.save()
            
        print("Módulos CAJA y POS actualizados correctamente.")
            
    except Exception as e:
        print("Error:", e)

if __name__ == '__main__':
    run()
