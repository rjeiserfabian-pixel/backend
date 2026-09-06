import os
import django
import sys

# Configurar el entorno de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo

def run():
    print("Insertando módulo de Compras...")
    
    # Módulo Principal
    modulo, created = Modulo.objects.get_or_create(
        codigo='COMPRAS',
        defaults={
            'nombre': 'Compras',
            'icono': 'shopping-cart',
            'ruta': '/compras',
            'orden': 4
        }
    )
    if not created:
        modulo.icono = 'shopping-cart' # Shopping cart o store icon
        modulo.save()

    # Submódulos
    submodulos = [
        {
            'codigo': 'COMPRAS_NUEVA',
            'nombre': 'Nueva Compra',
            'ruta': '/compras/nueva',
            'icono': 'file-text',
            'orden': 1
        },
        {
            'codigo': 'COMPRAS_HISTORIAL',
            'nombre': 'Historial Compras',
            'ruta': '/compras',
            'icono': 'list',
            'orden': 2
        },
        {
            'codigo': 'COMPRAS_CXP',
            'nombre': 'Cuentas por Pagar',
            'ruta': '/compras/cuentas-por-pagar',
            'icono': 'credit-card',
            'orden': 3
        }
    ]

    for sub_data in submodulos:
        sub, sub_created = Modulo.objects.get_or_create(
            codigo=sub_data['codigo'],
            defaults={
                'id_modulo_padre': modulo,
                'nombre': sub_data['nombre'],
                'ruta': sub_data['ruta'],
                'icono': sub_data['icono'],
                'orden': sub_data['orden'],
                'visible_menu': True,
                'estado': True
            }
        )
        if sub_created:
            print(f"  - Submódulo '{sub.nombre}' creado.")
        else:
            print(f"  - Submódulo '{sub.nombre}' ya existía.")


    print("¡Listo!")

if __name__ == '__main__':
    run()
