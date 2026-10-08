import django.db.models.deletion
import apps.vehiculos.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehiculos', '0005_vehiculo_tipo_combustible'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='VehiculoQR',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_publico', models.CharField(db_index=True, default=apps.vehiculos.models.generar_token_qr_vehiculo, editable=False, max_length=96, unique=True)),
                ('codigo_corto', models.CharField(db_index=True, default=apps.vehiculos.models.generar_codigo_corto_qr, editable=False, max_length=20, unique=True)),
                ('activo', models.BooleanField(db_index=True, default=True)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('fecha_actualizacion', models.DateTimeField(auto_now=True)),
                ('fecha_revocacion', models.DateTimeField(blank=True, null=True)),
                ('creado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='codigos_qr_vehiculo_creados', to=settings.AUTH_USER_MODEL)),
                ('revocado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='codigos_qr_vehiculo_revocados', to=settings.AUTH_USER_MODEL)),
                ('vehiculo', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='codigos_qr', to='vehiculos.vehiculo')),
            ],
            options={
                'db_table': 'vehiculo_qr',
                'ordering': ['-fecha_creacion'],
            },
        ),
        migrations.AddConstraint(
            model_name='vehiculoqr',
            constraint=models.UniqueConstraint(condition=models.Q(('activo', True)), fields=('vehiculo',), name='uniq_qr_activo_por_vehiculo'),
        ),
    ]
