from django.db import models

class Vehiculo(models.Model):
    placa = models.CharField(max_length=15, unique=True, db_index=True)
    marca = models.CharField(max_length=100)
    modelo = models.CharField(max_length=100)
    serie = models.CharField(max_length=100, null=True, blank=True)
    color = models.CharField(max_length=50, null=True, blank=True)
    motor = models.CharField(max_length=100, null=True, blank=True)
    estado = models.BooleanField(default=True) # Soft delete

    class Meta:
        db_table = 'vehiculo'
        verbose_name = 'Vehiculo'
        verbose_name_plural = 'Vehiculos'

    def __str__(self):
        return f"{self.placa} - {self.marca} {self.modelo}"
