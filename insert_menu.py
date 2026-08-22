import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo

modulo_ventas, created = Modulo.objects.get_or_create(
    codigo='VENTAS',
    defaults={
        'nombre': 'Ventas y Caja',
        'icono': 'tags',
        'orden': 40,
        'visible_menu': True,
        'estado': True
    }
)

Modulo.objects.get_or_create(
    codigo='CAJA_POS',
    defaults={
        'id_modulo_padre': modulo_ventas,
        'nombre': 'Caja / Punto de Venta',
        'icono': 'dashboard',
        'ruta': '/caja',
        'orden': 10,
        'visible_menu': True,
        'estado': True
    }
)

Modulo.objects.get_or_create(
    codigo='CAJA_CONFIG',
    defaults={
        'id_modulo_padre': modulo_ventas,
        'nombre': 'Configuración',
        'icono': 'settings',
        'ruta': '/ventas/configuracion',
        'orden': 20,
        'visible_menu': True,
        'estado': True
    }
)

print("Módulos de Ventas creados correctamente.")
