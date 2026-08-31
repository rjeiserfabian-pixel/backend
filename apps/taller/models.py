import logging
from django.db import models
from django.conf import settings
from apps.vehiculos.models import Vehiculo
from apps.inventario.models import Repuesto

logger = logging.getLogger(__name__)

class OrdenTrabajo(models.Model):
    class Estado(models.TextChoices):
        RECEPCIONADO = 'RECEPCIONADO', 'Recepcionado'
        INSPECCION = 'INSPECCION', 'En Inspección'
        ESPERANDO_APROBACION = 'ESPERANDO_APROBACION', 'Esperando Aprobación'
        APROBADO = 'APROBADO', 'Aprobado / En Ejecución'
        FINALIZADO = 'FINALIZADO', 'Finalizado'
        FACTURADO = 'FACTURADO', 'Facturado'
        CANCELADO = 'CANCELADO', 'Cancelado'

    class TipoServicio(models.TextChoices):
        PREVENTIVO = 'PREVENTIVO', 'Mantenimiento Preventivo'
        CORRECTIVO = 'CORRECTIVO', 'Mantenimiento Correctivo'
        AMBOS = 'AMBOS', 'Preventivo y Correctivo'

    numero = models.CharField(max_length=20, unique=True, db_index=True)
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.RESTRICT, related_name='ordenes_trabajo')
    recepcionista = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='ordenes_recepcionadas')
    mecanico_asignado = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='ordenes_asignadas', null=True, blank=True)
    
    estado = models.CharField(max_length=30, choices=Estado.choices, default=Estado.RECEPCIONADO, db_index=True)
    tipo_servicio = models.CharField(max_length=20, choices=TipoServicio.choices, default=TipoServicio.PREVENTIVO)
    
    kilometraje_ingreso = models.IntegerField(null=True, blank=True)
    motivo_ingreso = models.TextField(help_text="Motivo principal o correctivo reportado por el cliente", null=True, blank=True)
    
    fecha_ingreso = models.DateTimeField(auto_now_add=True, db_index=True)
    fecha_estimada_entrega = models.DateTimeField(null=True, blank=True)
    fecha_finalizacion = models.DateTimeField(null=True, blank=True)
    
    # Campo para almacenar temporalmente el PDF generado de la cotización/hallazgos
    url_cotizacion_pdf = models.URLField(max_length=500, null=True, blank=True)

    class Meta:
        db_table = 'taller_orden_trabajo'
        verbose_name = 'Orden de Trabajo'
        verbose_name_plural = 'Órdenes de Trabajo'
        ordering = ['-fecha_ingreso']

    def __str__(self):
        return f"OT-{self.numero} | {self.vehiculo.placa}"


class Hallazgo(models.Model):
    orden = models.ForeignKey(OrdenTrabajo, on_delete=models.CASCADE, related_name='hallazgos')
    descripcion = models.CharField(max_length=255)
    severidad = models.CharField(max_length=20, choices=[('BAJA', 'Baja'), ('MEDIA', 'Media'), ('ALTA', 'Alta')], default='MEDIA')
    fecha_registro = models.DateTimeField(auto_now_add=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT)

    class Meta:
        db_table = 'taller_hallazgo'
        verbose_name = 'Hallazgo'
        verbose_name_plural = 'Hallazgos'

    def __str__(self):
        return f"Hallazgo de OT-{self.orden.numero}: {self.descripcion}"


class OrdenServicio(models.Model):
    """Mano de obra o servicios a realizar en la OT"""
    orden = models.ForeignKey(OrdenTrabajo, on_delete=models.CASCADE, related_name='servicios')
    descripcion = models.CharField(max_length=255)
    precio_estimado = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    aprobado_cliente = models.BooleanField(default=False, db_index=True)
    completado = models.BooleanField(default=False)
    
    class Meta:
        db_table = 'taller_orden_servicio'
        verbose_name = 'Servicio de OT'
        verbose_name_plural = 'Servicios de OT'

    def __str__(self):
        return self.descripcion


class OrdenRepuesto(models.Model):
    """Repuestos requeridos/utilizados en la OT"""
    orden = models.ForeignKey(OrdenTrabajo, on_delete=models.CASCADE, related_name='repuestos')
    repuesto = models.ForeignKey(Repuesto, on_delete=models.RESTRICT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    aprobado_cliente = models.BooleanField(default=False, db_index=True)
    entregado_por_almacen = models.BooleanField(default=False)
    instalado = models.BooleanField(default=False)

    class Meta:
        db_table = 'taller_orden_repuesto'
        verbose_name = 'Repuesto de OT'
        verbose_name_plural = 'Repuestos de OT'

    def __str__(self):
        return f"{self.cantidad}x {self.repuesto.nombre} (OT-{self.orden.numero})"

class PlantillaPreventiva(models.Model):
    """Servicios estandarizados que se pueden sugerir o añadir dinámicamente en recepción."""
    nombre = models.CharField(max_length=150, unique=True, db_index=True)
    descripcion = models.TextField(null=True, blank=True)
    precio_base = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tiempo_estimado_minutos = models.IntegerField(null=True, blank=True, help_text="Tiempo aproximado que toma el servicio")
    activo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'taller_plantilla_preventiva'
        verbose_name = 'Plantilla Preventiva'
        verbose_name_plural = 'Plantillas Preventivas'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre
