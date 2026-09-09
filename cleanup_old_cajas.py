import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo

def run():
    print("=" * 50)
    print("Limpiando menú antiguo de Ventas y Cajas...")
    print("=" * 50)

    # 1. Renombrar "Ventas y Caja" a "Ventas"
    ventas_padre = Modulo.objects.filter(nombre__icontains='Ventas y Caja').first()
    if ventas_padre:
        ventas_padre.nombre = 'Ventas'
        ventas_padre.save(update_fields=['nombre'])
        print(f"[OK] Renombrado '{ventas_padre.nombre}' a 'Ventas'")
    else:
        print("[-] Módulo 'Ventas y Caja' no encontrado. Puede que ya haya sido renombrado.")

    # 2. Desactivar "Gestión de Caja"
    caja_antiguo = Modulo.objects.filter(codigo='CAJA').first()
    if caja_antiguo:
        caja_antiguo.visible_menu = False
        caja_antiguo.estado = False
        caja_antiguo.save(update_fields=['visible_menu', 'estado'])
        print(f"[OK] Submenú '{caja_antiguo.nombre}' (antiguo) ha sido ocultado y desactivado.")
    else:
        print("[-] Submenú 'Gestión de Caja' (antiguo) no encontrado.")

    print("=" * 50)
    print("Listo.")

if __name__ == '__main__':
    run()
