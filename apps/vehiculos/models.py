from django.db import models

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
    
    # Relación M:N con clientes para mantener trazabilidad histórica
    clientes = models.ManyToManyField('clientes.Cliente', related_name='vehiculos', blank=True)
    
    estado = models.BooleanField(default=True) # Soft delete

    class Meta:
        db_table = 'vehiculo'
        verbose_name = 'Vehiculo'
        verbose_name_plural = 'Vehiculos'

    def __str__(self):
        return f"{self.placa} - {self.marca} {self.modelo}"


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
