import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo, Permiso

def run():
    print("Iniciando inserción de módulo Taller...")
    
    # Asegurar que existe el módulo padre "Taller"
    modulo_taller, created = Modulo.objects.get_or_create(
        codigo='TALLER',
        defaults={
            'nombre': 'Taller',
            'icono': 'wrench',
            'orden': 40,
            'visible_menu': True
        }
    )
    if created:
        print("Módulo padre Taller creado.")

    # Módulo de Recepción / Órdenes
    modulo_ordenes, created = Modulo.objects.get_or_create(
        codigo='ORDENES_TRABAJO',
        defaults={
            'id_modulo_padre': modulo_taller,
            'nombre': 'Órdenes de Trabajo',
            'icono': 'clipboard-list', # Icono nuevo a agregar en DashboardLayout
            'ruta': '/taller/ordenes',
            'orden': 10,
            'visible_menu': True
        }
    )
    
    if created:
        print("Submódulo Órdenes de Trabajo creado.")
        
        # Crear permisos base
        acciones = ['VER', 'CREAR', 'EDITAR', 'ELIMINAR', 'APROBAR']
        for accion in acciones:
            Permiso.objects.get_or_create(
                id_modulo=modulo_ordenes,
                codigo=f'ORDENES_TRABAJO.{accion}',
                defaults={'nombre': f'{accion} Órdenes de Trabajo', 'accion': accion}
            )
        print("Permisos para Órdenes de Trabajo creados.")

    # Módulo de Plantillas Preventivas
    modulo_plantillas, created = Modulo.objects.get_or_create(
        codigo='PLANTILLAS_TALLER',
        defaults={
            'id_modulo_padre': modulo_taller,
            'nombre': 'Plantillas de Servicio',
            'icono': 'list-checks', 
            'ruta': '/taller/plantillas',
            'orden': 20,
            'visible_menu': True
        }
    )
    
    if created:
        print("Submódulo Plantillas creado.")
        
        # Crear permisos base
        acciones = ['VER', 'CREAR', 'EDITAR', 'ELIMINAR']
        for accion in acciones:
            Permiso.objects.get_or_create(
                id_modulo=modulo_plantillas,
                codigo=f'PLANTILLAS_TALLER.{accion}',
                defaults={'nombre': f'{accion} Plantillas', 'accion': accion}
            )

    # Módulo de Tipos de Servicio
    modulo_tipos_servicio, created = Modulo.objects.get_or_create(
        codigo='TIPOS_SERVICIO',
        defaults={
            'id_modulo_padre': modulo_taller,
            'nombre': 'Tipos de Servicio',
            'icono': 'tags', 
            'ruta': '/taller/tipos-servicio',
            'orden': 30,
            'visible_menu': True
        }
    )
    
    if created:
        print("Submódulo Tipos de Servicio creado.")
        
        # Crear permisos base
        acciones = ['VER', 'CREAR', 'EDITAR', 'ELIMINAR']
        for accion in acciones:
            Permiso.objects.get_or_create(
                id_modulo=modulo_tipos_servicio,
                codigo=f'TIPOS_SERVICIO.{accion}',
                defaults={'nombre': f'{accion} Tipos de Servicio', 'accion': accion}
            )
    
    print("Módulos de Taller insertados correctamente.")

if __name__ == '__main__':
    run()
