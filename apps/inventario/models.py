from django.db import models

class Categoria(models.Model):
    nombre = models.CharField(max_length=100, unique=True, db_index=True)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'categoria_repuesto'
        verbose_name = 'Categoria de Repuesto'
        verbose_name_plural = 'Categorias de Repuestos'

    def __str__(self):
        return self.nombre


class MarcaRepuesto(models.Model):
    nombre = models.CharField(max_length=100, unique=True, db_index=True)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'marca_repuesto'
        verbose_name = 'Marca de Repuesto'
        verbose_name_plural = 'Marcas de Repuestos'

    def __str__(self):
        return self.nombre


class Repuesto(models.Model):
    codigo = models.CharField(max_length=50, unique=True, db_index=True)
    nombre = models.CharField(max_length=200)
    categoria = models.ForeignKey(Categoria, on_delete=models.RESTRICT, related_name='repuestos')
    marca = models.ForeignKey(MarcaRepuesto, on_delete=models.RESTRICT, related_name='repuestos')
    stock = models.IntegerField(default=0)
    
    # 4 Tipos de Precios
    precio_compra = models.DecimalField(max_digits=10, decimal_places=2)
    precio_por_mayor = models.DecimalField(max_digits=10, decimal_places=2)
    precio_cash = models.DecimalField(max_digits=10, decimal_places=2)
    precio_lista = models.DecimalField(max_digits=10, decimal_places=2)
    
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'repuesto'
        verbose_name = 'Repuesto'
        verbose_name_plural = 'Repuestos'

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class AplicacionRepuesto(models.Model):
    repuesto = models.ForeignKey(Repuesto, on_delete=models.CASCADE, related_name='aplicaciones')
    marca_vehiculo = models.CharField(max_length=100, db_index=True)
    modelo_vehiculo = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    motor = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table = 'aplicacion_repuesto'
        verbose_name = 'Aplicacion de Repuesto'
        verbose_name_plural = 'Aplicaciones de Repuestos'

    def __str__(self):
        return f"{self.repuesto.nombre} para {self.marca_vehiculo} {self.modelo_vehiculo or ''}"
