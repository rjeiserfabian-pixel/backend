from django.db import migrations


def crear(apps, schema_editor):
    Modulo = apps.get_model('seguridad', 'Modulo')
    Permiso = apps.get_model('seguridad', 'Permiso')
    Rol = apps.get_model('seguridad', 'Rol')
    RolPermiso = apps.get_model('seguridad', 'RolPermiso')
    modulo = Modulo.objects.filter(codigo='CUENTAS').first()
    if not modulo:
        return  # Instalaciones nuevas lo crean con cargar_datos_iniciales.
    for accion, nombre in [('REGISTRAR_ANTERIOR', 'Registrar crédito anterior'), ('EDITAR_ANTERIOR', 'Corregir crédito anterior'), ('ANULAR_ANTERIOR', 'Anular crédito anterior')]:
        permiso, _ = Permiso.objects.get_or_create(codigo='CUENTAS.POR_COBRAR.' + accion, defaults={'id_modulo': modulo, 'nombre': nombre, 'accion': accion, 'estado': True, 'grupo_padre': 'Cuentas', 'grupo_submodulo': 'Por Cobrar'})
        admin = Rol.objects.filter(codigo='ADMINISTRADOR').first()
        if admin:
            RolPermiso.objects.get_or_create(id_rol=admin, id_permiso=permiso, defaults={'alcance': 'GLOBAL'})


class Migration(migrations.Migration):
    dependencies = [('seguridad', '0019_configuracion_acceso_publico')]
    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
