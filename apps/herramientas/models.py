from django.conf import settings
from django.db import models, transaction


class CategoriaHerramienta(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.CharField(max_length=255, blank=True, default='')
    estado = models.BooleanField(default=True, db_index=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'herramientas_categoria'
        verbose_name = 'Categoría de herramienta'
        verbose_name_plural = 'Categorías de herramientas'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Herramienta(models.Model):
    class EstadoOperativo(models.TextChoices):
        DISPONIBLE = 'DISPONIBLE', 'Disponible'
        ASIGNADA = 'ASIGNADA', 'Asignada'
        EN_MANTENIMIENTO = 'EN_MANTENIMIENTO', 'En mantenimiento'
        EN_REPARACION = 'EN_REPARACION', 'En reparación'
        FUERA_DE_SERVICIO = 'FUERA_DE_SERVICIO', 'Fuera de servicio'
        PERDIDA = 'PERDIDA', 'Perdida'
        DADA_DE_BAJA = 'DADA_DE_BAJA', 'Dada de baja'

    class EstadoFisico(models.TextChoices):
        NUEVO = 'NUEVO', 'Nuevo'
        BUENO = 'BUENO', 'Bueno'
        REGULAR = 'REGULAR', 'Regular'
        MALO = 'MALO', 'Malo'

    # Identificación
    codigo = models.CharField(max_length=20, unique=True, editable=False)
    nombre = models.CharField(max_length=150, db_index=True)
    categoria = models.ForeignKey(
        CategoriaHerramienta, on_delete=models.RESTRICT, related_name='herramientas'
    )
    marca = models.CharField(max_length=100, blank=True, default='')
    modelo = models.CharField(max_length=100, blank=True, default='')
    numero_serie = models.CharField(max_length=100, blank=True, default='', db_index=True)

    # Ubicación
    sucursal = models.ForeignKey(
        'inventario.Sucursal', on_delete=models.RESTRICT, related_name='herramientas'
    )
    almacen = models.ForeignKey(
        'inventario.Almacen', on_delete=models.RESTRICT, related_name='herramientas',
        null=True, blank=True,
    )
    ubicacion = models.CharField(max_length=150, blank=True, default='')

    # Compra
    fecha_compra = models.DateField(null=True, blank=True)
    costo_adquisicion = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    proveedor = models.ForeignKey(
        'clientes.Proveedor', on_delete=models.RESTRICT, related_name='herramientas',
        null=True, blank=True,
    )
    garantia_hasta = models.DateField(null=True, blank=True)

    # Estados
    estado_operativo = models.CharField(
        max_length=20, choices=EstadoOperativo.choices,
        default=EstadoOperativo.DISPONIBLE, db_index=True,
    )
    estado_fisico = models.CharField(
        max_length=10, choices=EstadoFisico.choices, default=EstadoFisico.NUEVO,
    )

    # Datos técnicos
    potencia = models.CharField(max_length=50, blank=True, default='')
    voltaje = models.CharField(max_length=50, blank=True, default='')
    observaciones = models.TextField(blank=True, default='')
    horas_uso_acumuladas = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Control
    estado = models.BooleanField(default=True, db_index=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='herramientas_creadas',
        null=True, blank=True,
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'herramientas_herramienta'
        verbose_name = 'Herramienta'
        verbose_name_plural = 'Herramientas'
        ordering = ['codigo']
        indexes = [
            models.Index(fields=['sucursal', 'estado_operativo']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(horas_uso_acumuladas__gte=0),
                name='herramienta_horas_no_negativas',
            ),
        ]

    def __str__(self):
        return f'{self.codigo} - {self.nombre}'

    def save(self, *args, **kwargs):
        if not self.codigo:
            with transaction.atomic():
                self.codigo = self._generar_codigo()
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    @classmethod
    def _generar_codigo(cls):
        """HER-0001, HER-0002... Bloquea la última fila para evitar duplicados concurrentes;
        la unicidad de `codigo` en BD es la red de seguridad final."""
        ultimo = (
            cls.objects.select_for_update().order_by('-id').values_list('codigo', flat=True).first()
        )
        try:
            siguiente = int(ultimo.split('-')[1]) + 1 if ultimo else 1
        except (IndexError, ValueError):
            siguiente = cls.objects.count() + 1
        return f'HER-{siguiente:04d}'


class HistorialHerramienta(models.Model):
    class Accion(models.TextChoices):
        CREACION = 'CREACION', 'Creación'
        EDICION = 'EDICION', 'Edición'
        CAMBIO_ESTADO = 'CAMBIO_ESTADO', 'Cambio de estado'
        BAJA = 'BAJA', 'Baja del registro'
        ENTREGA = 'ENTREGA', 'Entrega a técnico'
        DEVOLUCION = 'DEVOLUCION', 'Devolución'
        ANULACION_ASIGNACION = 'ANULACION_ASIGNACION', 'Asignación anulada'
        MANTENIMIENTO_INICIO = 'MANTENIMIENTO_INICIO', 'Inicio de mantenimiento'
        MANTENIMIENTO_FIN = 'MANTENIMIENTO_FIN', 'Fin de mantenimiento'
        INCIDENCIA = 'INCIDENCIA', 'Incidencia'

    herramienta = models.ForeignKey(
        Herramienta, on_delete=models.CASCADE, related_name='historial'
    )
    accion = models.CharField(max_length=30, choices=Accion.choices)
    detalle = models.TextField(blank=True, default='')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='historial_herramientas',
        null=True, blank=True,
    )
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'herramientas_historial'
        verbose_name = 'Historial de herramienta'
        verbose_name_plural = 'Historial de herramientas'
        ordering = ['-fecha', '-id']


class AsignacionHerramienta(models.Model):
    """Entrega de una herramienta a un técnico y su devolución.

    Una asignación nunca se borra: se devuelve o se anula, y queda como registro."""

    class Estado(models.TextChoices):
        ACTIVA = 'ACTIVA', 'Activa'
        DEVUELTA = 'DEVUELTA', 'Devuelta'
        ANULADA = 'ANULADA', 'Anulada'
        NO_DEVUELTA = 'NO_DEVUELTA', 'No devuelta (robo o pérdida)'

    herramienta = models.ForeignKey(
        Herramienta, on_delete=models.RESTRICT, related_name='asignaciones'
    )
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='herramientas_asignadas'
    )
    entregado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='herramientas_entregadas'
    )
    recibido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='herramientas_recibidas',
        null=True, blank=True,
    )
    estado = models.CharField(
        max_length=12, choices=Estado.choices, default=Estado.ACTIVA, db_index=True
    )

    fecha_entrega = models.DateTimeField(auto_now_add=True, db_index=True)
    fecha_devolucion_esperada = models.DateField(null=True, blank=True)
    fecha_devolucion_real = models.DateTimeField(null=True, blank=True)

    estado_fisico_entrega = models.CharField(max_length=10, choices=Herramienta.EstadoFisico.choices)
    estado_fisico_devolucion = models.CharField(
        max_length=10, choices=Herramienta.EstadoFisico.choices, blank=True, default=''
    )
    horas_uso_periodo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    observaciones_entrega = models.TextField(blank=True, default='')
    observaciones_devolucion = models.TextField(blank=True, default='')

    motivo_anulacion = models.CharField(max_length=255, blank=True, default='')
    anulada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='asignaciones_anuladas',
        null=True, blank=True,
    )
    fecha_anulacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'herramientas_asignacion'
        verbose_name = 'Asignación de herramienta'
        verbose_name_plural = 'Asignaciones de herramientas'
        ordering = ['-fecha_entrega', '-id']
        indexes = [
            models.Index(fields=['tecnico', 'estado']),
            models.Index(fields=['herramienta', 'estado']),
        ]
        constraints = [
            # Garantía final contra entregas dobles: una sola asignación activa por herramienta.
            models.UniqueConstraint(
                fields=['herramienta'],
                condition=models.Q(estado='ACTIVA'),
                name='asignacion_unica_activa_por_herramienta',
            ),
            models.CheckConstraint(
                condition=models.Q(horas_uso_periodo__gte=0),
                name='asignacion_horas_no_negativas',
            ),
        ]

    def __str__(self):
        return f'{self.herramienta.codigo} -> {self.tecnico_id} ({self.estado})'


# Modelos de la fase 3 (se registran al importarlos aquí)
from .models_mantenimiento import (  # noqa: E402,F401
    IncidenciaHerramienta,
    PlanMantenimientoHerramienta,
    RegistroMantenimiento,
)
