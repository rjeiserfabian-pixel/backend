import secrets

from django.conf import settings
from django.db import models


def generar_token_qr_vehiculo():
    return secrets.token_urlsafe(32)


def generar_codigo_corto_qr():
    return f"VQ-{secrets.token_hex(4).upper()}"

class Vehiculo(models.Model):
    placa = models.CharField(max_length=15, unique=True, db_index=True)
    marca = models.CharField(max_length=100)
    modelo = models.CharField(max_length=100)
    clase = models.CharField(max_length=100, null=True, blank=True)
    tipo = models.CharField(max_length=100, null=True, blank=True)
    uso = models.CharField(max_length=100, null=True, blank=True)
    anio_fabricacion = models.IntegerField(null=True, blank=True)
    numero_asientos = models.IntegerField(null=True, blank=True)
    numero_serie = models.CharField(max_length=100, null=True, blank=True)
    color = models.CharField(max_length=50, null=True, blank=True)
    numero_motor = models.CharField(max_length=100, null=True, blank=True)
    kilometraje_actual = models.IntegerField(null=True, blank=True)
    tipo_combustible = models.CharField(
        max_length=10,
        choices=[('GASOLINA', 'Gasolinero'), ('PETROLEO', 'Petrolero')],
        null=True, blank=True,
    )

    # Relación M:N con clientes para mantener trazabilidad histórica
    clientes = models.ManyToManyField('clientes.Cliente', related_name='vehiculos', blank=True)
    
    estado = models.BooleanField(default=True) # Soft delete

    class Meta:
        db_table = 'vehiculo'
        verbose_name = 'Vehiculo'
        verbose_name_plural = 'Vehiculos'

    def __str__(self):
        return f"{self.placa} - {self.marca} {self.modelo}"


class VehiculoQR(models.Model):
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE, related_name='codigos_qr')
    token_publico = models.CharField(
        max_length=96,
        unique=True,
        db_index=True,
        default=generar_token_qr_vehiculo,
        editable=False,
    )
    codigo_corto = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        default=generar_codigo_corto_qr,
        editable=False,
    )
    activo = models.BooleanField(default=True, db_index=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    fecha_revocacion = models.DateTimeField(null=True, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='codigos_qr_vehiculo_creados',
    )
    revocado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='codigos_qr_vehiculo_revocados',
    )

    class Meta:
        db_table = 'vehiculo_qr'
        ordering = ['-fecha_creacion']
        constraints = [
            models.UniqueConstraint(
                fields=['vehiculo'],
                condition=models.Q(activo=True),
                name='uniq_qr_activo_por_vehiculo',
            ),
        ]

    def __str__(self):
        estado = 'activo' if self.activo else 'revocado'
        return f"{self.codigo_corto} - {self.vehiculo.placa} ({estado})"


class MantenimientoVehiculo(models.Model):
    class Tipo(models.TextChoices):
        ACEITE = 'ACEITE', 'Cambio de aceite'
        FILTRO_ACEITE = 'FILTRO_ACEITE', 'Filtro de aceite'
        FILTRO_AIRE = 'FILTRO_AIRE', 'Filtro de aire'
        FILTRO_COMBUSTIBLE = 'FILTRO_COMBUSTIBLE', 'Filtro de combustible'

    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE, related_name='mantenimientos')
    tipo = models.CharField(max_length=25, choices=Tipo.choices)
    fecha_realizado = models.DateField()
    kilometraje_realizado = models.PositiveIntegerField()
    intervalo_km = models.PositiveIntegerField(null=True, blank=True)
    intervalo_meses = models.PositiveSmallIntegerField(null=True, blank=True)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vehiculo_mantenimiento'
        ordering = ['-fecha_realizado', '-id']
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(intervalo_km__isnull=False, intervalo_km__gt=0)
                           | models.Q(intervalo_meses__isnull=False, intervalo_meses__gt=0)),
                name='mantenimiento_intervalo_requerido',
            ),
            models.CheckConstraint(
                condition=models.Q(intervalo_km__isnull=True) | models.Q(intervalo_km__gt=0),
                name='mantenimiento_km_positivo',
            ),
            models.CheckConstraint(
                condition=models.Q(intervalo_meses__isnull=True) | models.Q(intervalo_meses__gt=0),
                name='mantenimiento_meses_positivo',
            ),
        ]


class VehiculoTransporte(models.Model):
    placa = models.CharField(max_length=15, unique=True, db_index=True)
    marca = models.CharField(max_length=100)
    modelo = models.CharField(max_length=100, null=True, blank=True)
    certificado_inscripcion = models.CharField(max_length=50, null=True, blank=True)
    configuracion_vehicular = models.CharField(max_length=50, null=True, blank=True)
    carga_util = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    peso_bruto = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estado = models.BooleanField(default=True) # Soft delete

    class Meta:
        db_table = 'vehiculo_transporte'
        verbose_name = 'Vehículo de Transporte'
        verbose_name_plural = 'Vehículos de Transporte'
        ordering = ['-id']

    def __str__(self):
        return f"{self.placa} - {self.marca} {self.modelo}"
