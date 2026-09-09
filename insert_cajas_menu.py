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
    print("Insertando modulo de Cajas en el menu...")
    print("=" * 50)

    # -- Módulo Principal --
    modulo, created = Modulo.objects.get_or_create(
        codigo='CAJAS',
        defaults={
            'nombre': 'Cajas',
            'icono': 'Wallet',
            'ruta': '/cajas',
            'orden': 11,
            'visible_menu': True,
            'estado': True,
        }
    )
    if created:
        print(f"[OK] Modulo principal 'Cajas' creado (id={modulo.id_modulo}).")
    else:
        modulo.icono = 'Wallet'
        modulo.save(update_fields=['icono'])
        print(f"[-]  Modulo principal 'Cajas' ya existia (id={modulo.id_modulo}). Icono actualizado.")

    # -- Sub-módulos --
    submodulos = [
        {
            'codigo': 'CAJAS_DASHBOARD',
            'nombre': 'Dashboard Cajas',
            'ruta': '/cajas',
            'icono': 'LayoutDashboard',
            'orden': 1,
        },
        {
            'codigo': 'CAJAS_TRANSFERENCIAS',
            'nombre': 'Transferencias',
            'ruta': '/cajas/transferencias',
            'icono': 'ArrowRightLeft',
            'orden': 2,
        }
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
    print("Listo! El modulo de Cajas ya aparece en el menu.")
    print("=" * 50)


if __name__ == '__main__':
    run()
