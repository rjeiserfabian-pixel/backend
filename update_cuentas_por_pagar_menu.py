import os
import django
import sys

# Configurar el entorno de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo

def run():
    print("Actualizando menú de Cuentas por Pagar...")
    
    # Buscar el módulo 'Cuentas'
    try:
        modulo_cuentas = Modulo.objects.get(nombre='Cuentas')
    except Modulo.DoesNotExist:
        print("Error: No se encontró el módulo padre 'Cuentas'.")
        return
    except Modulo.MultipleObjectsReturned:
        modulo_cuentas = Modulo.objects.filter(nombre='Cuentas').first()
        
    print(f"Módulo Cuentas encontrado con ID: {modulo_cuentas.id_modulo}")

    # Buscar el submódulo 'COMPRAS_CXP'
    try:
        submodulo_cxp = Modulo.objects.get(codigo='COMPRAS_CXP')
        
        # Actualizar padre y nombre
        submodulo_cxp.id_modulo_padre = modulo_cuentas
        submodulo_cxp.nombre = 'Por Pagar'
        submodulo_cxp.save()
        
        print("Submódulo actualizado correctamente.")
    except Modulo.DoesNotExist:
        print("Error: No se encontró el submódulo 'COMPRAS_CXP'.")

if __name__ == '__main__':
    run()
