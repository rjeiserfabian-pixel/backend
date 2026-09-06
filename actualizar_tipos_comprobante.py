import os
import django
import sys

# Configurar el entorno de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.compras.models import Compra, TipoComprobanteCompra
from apps.seguridad.models import Modulo

def run():
    print("Iniciando migración de Tipos de Comprobante para Compras...")

    # 1. Crear tipos por defecto
    tipos_defecto = ['Factura', 'Boleta', 'Guía de Remisión', 'Ticket']
    tipo_objetos = {}
    
    for nombre in tipos_defecto:
        obj, created = TipoComprobanteCompra.objects.get_or_create(nombre=nombre)
        tipo_objetos[nombre] = obj
        if created:
            print(f"Creado nuevo tipo: {nombre}")

    # 2. Migrar las compras existentes
    compras = Compra.objects.all()
    actualizadas = 0
    for compra in compras:
        # El CharField puede contener 'Guia', hay que mapearlo a 'Guía de Remisión' u otro si es necesario.
        nombre_buscar = compra.tipo_comprobante
        if not nombre_buscar:
            continue
            
        if nombre_buscar == 'Guia':
            nombre_buscar = 'Guía de Remisión'
            
        if nombre_buscar in tipo_objetos:
            compra.tipo_comprobante_fk = tipo_objetos[nombre_buscar]
            compra.save(update_fields=['tipo_comprobante_fk'])
            actualizadas += 1
        else:
            # Si hay un tipo no estándar, lo creamos
            obj, _ = TipoComprobanteCompra.objects.get_or_create(nombre=nombre_buscar)
            tipo_objetos[nombre_buscar] = obj
            compra.tipo_comprobante_fk = obj
            compra.save(update_fields=['tipo_comprobante_fk'])
            actualizadas += 1
            
    print(f"Se actualizaron {actualizadas} registros de Compra.")

    # 3. Insertar el módulo en el menú (id=39)
    try:
        modulo_padre = Modulo.objects.get(id_modulo=38) # Compras
        
        # Eliminar si existe algo con ID 39 para evitar colisiones (aunque ya lo borramos)
        Modulo.objects.filter(id_modulo=39).delete()
        
        # Crear el nuevo módulo
        nuevo_modulo = Modulo(
            id_modulo=39,
            id_modulo_padre=modulo_padre,
            codigo='COMPRAS_TIPO_COMPROBANTE',
            nombre='Tipos de Comprobante',
            icono='file-text',
            ruta='/compras/tipos-comprobante',
            orden=1, # Se puede ajustar
            visible_menu=True,
            estado=True
        )
        # Force insert with specific ID
        nuevo_modulo.save(force_insert=True)
        print("Módulo 'Tipos de Comprobante' insertado con éxito en el menú (ID: 39).")
        
    except Modulo.DoesNotExist:
        print("Error: No se encontró el módulo padre 'Compras' (ID 38). No se insertó el menú.")

if __name__ == '__main__':
    run()
