from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('seguridad', '0018_permiso_configurar_agenda_citas')]

    operations = [
        migrations.CreateModel(
            name='ConfiguracionAccesoPublico',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url_base', models.URLField(blank=True, default='', max_length=255)),
                ('fecha_actualizacion', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'configuracion_acceso_publico'},
        ),
    ]
