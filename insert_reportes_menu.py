"""
insert_reportes_menu.py
Registra el módulo de Reportes y sus 7 sub-módulos en la tabla de menú del sistema.
Ejecución (desde la carpeta backend, con el entorno activado):
  python insert_reportes_menu.py
El script es idempotente: puede ejecutarse varias veces sin duplicar registros.
"""
import os
import sys
import django

# Forzar UTF-8 en la salida de la consola (Windows cp1252 no tiene todos los chars)
if sys.stdout.encoding.lower().startswith('cp'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo


def run():
    print("=" * 50)
    print("Insertando modulo de Reportes en el menu...")
    print("=" * 50)

    # -- Módulo Principal --
    modulo, created = Modulo.objects.get_or_create(
        codigo='REPORTES',
        defaults={
            'nombre': 'Reportes',
            'icono': 'filetext',
            'ruta': '/reportes',
            'orden': 10,
            'visible_menu': True,
            'estado': True,
        }
    )
    if created:
        print(f"[OK] Modulo principal 'Reportes' creado (id={modulo.id_modulo}).")
    else:
        modulo.icono = 'filetext'
        modulo.save(update_fields=['icono'])
        print(f"[-]  Modulo principal 'Reportes' ya existia (id={modulo.id_modulo}). Icono actualizado.")

    # -- Sub-módulos --
    submodulos = [
        {
            'codigo': 'REPORTES_CAJA',
            'nombre': 'Reporte de Caja',
            'ruta': '/reportes/caja',
            'icono': 'banknote',
            'orden': 1,
        },
        {
            'codigo': 'REPORTES_VENTAS',
            'nombre': 'Reporte de Ventas',
            'ruta': '/reportes/ventas',
            'icono': 'shoppingcart',
            'orden': 2,
        },
        {
            'codigo': 'REPORTES_PRODUCTOS',
            'nombre': 'Reporte de Productos',
            'ruta': '/reportes/productos',
            'icono': 'package',
            'orden': 3,
        },
        {
            'codigo': 'REPORTES_CLIENTES',
            'nombre': 'Reporte de Clientes',
            'ruta': '/reportes/clientes',
            'icono': 'users',
            'orden': 4,
        },
        {
            'codigo': 'REPORTES_COMPRAS',
            'nombre': 'Reporte Compra',
            'ruta': '/reportes/compras',
            'icono': 'truck',
            'orden': 5,
        },
        {
            'codigo': 'REPORTES_AVANZADO',
            'nombre': 'Reporte Avanzado',
            'ruta': '/reportes/avanzado',
            'icono': 'list-checks',
            'orden': 6,
        },
        {
            'codigo': 'REPORTES_VEHICULOS',
            'nombre': 'Reporte Vehiculo',
            'ruta': '/reportes/vehiculos',
            'icono': 'car',
            'orden': 7,
        },
    ]

    print("\nCreando sub-modulos:")
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
                'estado': True,
            }
        )
        if sub_created:
            print(f"  [OK] '{sub.nombre}' creado -> {sub_data['ruta']}")
        else:
            sub.ruta = sub_data['ruta']
            sub.nombre = sub_data['nombre']
            sub.icono = sub_data['icono']
            sub.id_modulo_padre = modulo
            sub.save(update_fields=['ruta', 'nombre', 'icono', 'id_modulo_padre'])
            print(f"  [-]  '{sub.nombre}' ya existia. Datos actualizados.")

    print("\n" + "=" * 50)
    print("Listo! El modulo de Reportes ya aparece en el menu.")
    print("=" * 50)


if __name__ == '__main__':
    run()

