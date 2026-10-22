import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taller_core.settings')
django.setup()

from apps.seguridad.models import Modulo, Permiso, Rol, RolPermiso
from apps.taller.models import AccesorioPertenencia

def run():
    print("Iniciando registro de submódulo Accesorios y Pertenencias...")

    # 1. Módulo Padre TALLER
    modulo_taller = Modulo.objects.filter(codigo='TALLER').first()
    if not modulo_taller:
        print("Error: No se encontró el módulo padre TALLER")
        return

    # Ajustar órdenes
    Modulo.objects.filter(codigo='VEHICULOS').update(orden=1)
    Modulo.objects.filter(codigo='CITAS').update(orden=2)
    Modulo.objects.filter(codigo='ORDENES_TRABAJO').update(orden=3)
    Modulo.objects.filter(codigo='PLANTILLAS_TALLER').update(orden=4)

    # 2. Submódulo Accesorios y Pertenencias
    modulo_acc, created = Modulo.objects.update_or_create(
        codigo='ACCESORIOS_TALLER',
        defaults={
            'id_modulo_padre': modulo_taller,
            'nombre': 'Accesorios y Pertenencias',
            'icono': 'shield-check',
            'ruta': '/taller/accesorios',
            'orden': 5,
            'visible_menu': True,
            'estado': True,
            'permiso_ver': 'ACCESORIOS_TALLER.VER_MENU',
        }
    )
    print(f"Submódulo Accesorios y Pertenencias {'creado' if created else 'actualizado'}.")

    Modulo.objects.filter(codigo='TIPOS_SERVICIO').update(orden=6)

    # 3. Permisos
    permisos_def = [
        ('ACCESORIOS_TALLER.VER_MENU', 'Mostrar "Accesorios y Pertenencias" en el menú', 'VER_MENU'),
        ('ACCESORIOS_TALLER.VER', 'Ver Accesorios y Pertenencias', 'VER'),
        ('ACCESORIOS_TALLER.CREAR', 'Crear Accesorios y Pertenencias', 'CREAR'),
        ('ACCESORIOS_TALLER.EDITAR', 'Editar Accesorios y Pertenencias', 'EDITAR'),
        ('ACCESORIOS_TALLER.ELIMINAR', 'Eliminar Accesorios y Pertenencias', 'ELIMINAR'),
    ]

    permisos_creados = []
    for cod, nom, acc in permisos_def:
        p, _ = Permiso.objects.update_or_create(
            codigo=cod,
            defaults={
                'id_modulo': modulo_acc,
                'nombre': nom,
                'accion': acc,
                'estado': True,
                'grupo_padre': 'Taller',
                'grupo_submodulo': 'Accesorios y Pertenencias',
            }
        )
        permisos_creados.append(p)
    print("Permisos registrados.")

    # 4. Asignar al Rol Administrador
    admin_rol = Rol.objects.filter(codigo='ADMINISTRADOR').first()
    if admin_rol:
        for p in permisos_creados:
            RolPermiso.objects.get_or_create(id_rol=admin_rol, id_permiso=p)
        print("Permisos asignados al rol ADMINISTRADOR.")

    # Asignar también a roles como JEFE_TALLER o RECEPCIONISTA si existen
    otros_roles = Rol.objects.filter(codigo__in=['JEFE_TALLER', 'RECEPCIONISTA', 'RECEPCIONISTA_TALLER', 'ASESOR_SERVICIO'])
    for rol in otros_roles:
        for p in permisos_creados:
            RolPermiso.objects.get_or_create(id_rol=rol, id_permiso=p)
        print(f"Permisos asignados al rol {rol.codigo}.")

    # 5. Pre-poblar accesorios típicos si la tabla está vacía
    if AccesorioPertenencia.objects.count() == 0:
        accesorios_base = [
            ("Extintor", "Extintor de incendios cargado y vigente", 1),
            ("Llanta de repuesto", "Neumático de repuesto en buen estado", 2),
            ("Gata hidráulica y palanca", "Gata mecánica o hidráulica para elevación", 3),
            ("Llave de ruedas", "Llave cruz o en L para pernos de rueda", 4),
            ("Triángulos de seguridad", "Par de triángulos o conos reflectivos", 5),
            ("Botiquín de primeros auxilios", "Botiquín normativo vehicular", 6),
            ("Cables de pasar corriente", "Juego de cables para batería con pinzas", 7),
            ("Tarjeta de propiedad", "Documentación vehicular en guantera o custodia", 8),
            ("Radio / Pantalla táctil", "Equipo de sonido o infoentretenimiento original o desmontable", 9),
            ("Herramientas básicas", "Estuche o caja de herramientas manuales", 10),
            ("Antena de radio", "Antena de techo o desmontable", 11),
            ("Encendedor / Adaptador 12V", "Adaptador USB o encendedor original", 12),
        ]
        for nombre, desc, orden in accesorios_base:
            AccesorioPertenencia.objects.create(
                nombre=nombre,
                descripcion=desc,
                orden=orden,
                activo=True
            )
        print(f"Se crearon {len(accesorios_base)} accesorios iniciales.")
    else:
        print("El catálogo de accesorios ya contiene elementos.")

    print("¡Proceso finalizado con éxito!")

if __name__ == '__main__':
    run()
