from django.db import models

class Cliente(models.Model):
    dni = models.CharField(max_length=15, unique=True, db_index=True)
    nombres = models.CharField(max_length=150)
    apellidos = models.CharField(max_length=150)
    telefono = models.CharField(max_length=20, null=True, blank=True)
    direccion = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    estado = models.BooleanField(default=True) # Soft delete

    class Meta:
        db_table = 'cliente'
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'

    def __str__(self):
        return f"{self.dni} - {self.nombres} {self.apellidos}"


class Proveedor(models.Model):
    TIPO_DOCUMENTO_CHOICES = [
        ('DNI', 'DNI'),
        ('RUC', 'RUC'),
    ]
    tipo_documento = models.CharField(max_length=3, choices=TIPO_DOCUMENTO_CHOICES, default='RUC')
    numero_documento = models.CharField(max_length=15, unique=True, db_index=True)
    nombre_o_razon_social = models.CharField(max_length=200)
    direccion = models.CharField(max_length=255, null=True, blank=True)
    telefono = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    estado = models.BooleanField(default=True) # Soft delete

    class Meta:
        db_table = 'proveedor'
        verbose_name = 'Proveedor'
        verbose_name_plural = 'Proveedores'
        ordering = ['-id']

    def __str__(self):
        return f"{self.tipo_documento} {self.numero_documento} - {self.nombre_o_razon_social}"


class Transportista(models.Model):
    TIPO_DOCUMENTO_CHOICES = [
        ('DNI', 'DNI'),
        ('RUC', 'RUC'),
    ]
    tipo_documento = models.CharField(max_length=3, choices=TIPO_DOCUMENTO_CHOICES, default='RUC')
    numero_documento = models.CharField(max_length=15, unique=True, db_index=True)
    nombre_o_razon_social = models.CharField(max_length=200)
    direccion = models.CharField(max_length=255, null=True, blank=True)
    telefono = models.CharField(max_length=20, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    licencia_conducir = models.CharField(max_length=20, null=True, blank=True)
    categoria_licencia = models.CharField(max_length=10, null=True, blank=True)
    estado = models.BooleanField(default=True) # Soft delete

    class Meta:
        db_table = 'transportista'
        verbose_name = 'Transportista'
        verbose_name_plural = 'Transportistas'
        ordering = ['-id']

    def __str__(self):
        return f"{self.tipo_documento} {self.numero_documento} - {self.nombre_o_razon_social}"
