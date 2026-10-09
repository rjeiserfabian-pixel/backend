"""Modelos de mantenimiento e incidencias (fase 3). Se importan desde models.py
para que Django los registre dentro de la app `herramientas`."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from .models import Herramienta


class PlanMantenimientoHerramienta(models.Model):
    """Regla de mantenimiento periódico: vence por días, por horas de uso, o por lo que ocurra primero.

    `proxima_fecha` y `proximas_horas` se calculan al guardar para poder filtrarlos en la base de datos."""

    class Tipo(models.TextChoices):
        PREVENTIVO = 'PREVENTIVO', 'Preventivo'
        CALIBRACION = 'CALIBRACION', 'Calibración'
        LIMPIEZA = 'LIMPIEZA', 'Limpieza'
        CAMBIO_PIEZAS = 'CAMBIO_PIEZAS', 'Cambio de piezas (discos, carbones, etc.)'
        OTRO = 'OTRO', 'Otro'

    DIAS_AVISO = 7
    PORCENTAJE_AVISO_HORAS = Decimal('0.10')

    herramienta = models.ForeignKey(
        Herramienta, on_delete=models.RESTRICT, related_name='planes_mantenimiento'
    )
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=15, choices=Tipo.choices, default=Tipo.PREVENTIVO)
    intervalo_dias = models.PositiveIntegerField(null=True, blank=True)
    intervalo_horas = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    ultima_fecha = models.DateField(default=timezone.localdate)
    ultimas_horas = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    proxima_fecha = models.DateField(null=True, blank=True, editable=False, db_index=True)
    proximas_horas = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, editable=False
    )
    activo = models.BooleanField(default=True, db_index=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='planes_herramienta_creados',
        null=True, blank=True,
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'herramientas_plan_mantenimiento'
        verbose_name = 'Plan de mantenimiento'
        verbose_name_plural = 'Planes de mantenimiento'
        ordering = ['proxima_fecha', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(intervalo_dias__isnull=False) | models.Q(intervalo_horas__isnull=False),
                name='plan_requiere_algun_intervalo',
            ),
            models.CheckConstraint(
                condition=models.Q(intervalo_dias__isnull=True) | models.Q(intervalo_dias__gt=0),
                name='plan_intervalo_dias_positivo',
            ),
            models.CheckConstraint(
                condition=models.Q(intervalo_horas__isnull=True) | models.Q(intervalo_horas__gt=0),
                name='plan_intervalo_horas_positivo',
            ),
        ]

    def __str__(self):
        return f'{self.herramienta.codigo} - {self.nombre}'

    def recalcular(self):
        self.proxima_fecha = (
            self.ultima_fecha + timedelta(days=self.intervalo_dias) if self.intervalo_dias else None
        )
        self.proximas_horas = (
            self.ultimas_horas + self.intervalo_horas if self.intervalo_horas else None
        )

    def save(self, *args, **kwargs):
        self.recalcular()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'proxima_fecha', 'proximas_horas'}
        super().save(*args, **kwargs)

    def estado_vencimiento(self, hoy=None):
        """Devuelve VENCIDO, POR_VENCER o AL_DIA. Misma regla que los filtros en base de datos."""
        hoy = hoy or timezone.localdate()
        horas = self.herramienta.horas_uso_acumuladas
        if (self.proxima_fecha and self.proxima_fecha < hoy) or (
            self.proximas_horas is not None and horas >= self.proximas_horas
        ):
            return 'VENCIDO'
        if (self.proxima_fecha and self.proxima_fecha <= hoy + timedelta(days=self.DIAS_AVISO)) or (
            self.proximas_horas is not None
            and horas >= self.proximas_horas - self.intervalo_horas * self.PORCENTAJE_AVISO_HORAS
        ):
            return 'POR_VENCER'
        return 'AL_DIA'


class RegistroMantenimiento(models.Model):
    """Trabajo de mantenimiento o reparación. El costo es solo informativo (no genera egreso en caja)."""

    class Tipo(models.TextChoices):
        PREVENTIVO = 'PREVENTIVO', 'Preventivo'
        CORRECTIVO = 'CORRECTIVO', 'Correctivo (reparación)'

    class Estado(models.TextChoices):
        EN_CURSO = 'EN_CURSO', 'En curso'
        FINALIZADO = 'FINALIZADO', 'Finalizado'
        CANCELADO = 'CANCELADO', 'Cancelado'

    herramienta = models.ForeignKey(
        Herramienta, on_delete=models.RESTRICT, related_name='mantenimientos'
    )
    plan = models.ForeignKey(
        PlanMantenimientoHerramienta, on_delete=models.RESTRICT, related_name='registros',
        null=True, blank=True,
    )
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    estado = models.CharField(
        max_length=12, choices=Estado.choices, default=Estado.EN_CURSO, db_index=True
    )
    descripcion = models.TextField()
    fecha_inicio = models.DateTimeField(auto_now_add=True, db_index=True)
    fecha_fin = models.DateTimeField(null=True, blank=True)
    realizado_por = models.CharField(max_length=150, blank=True, default='')
    costo = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    repuestos_usados = models.TextField(blank=True, default='')
    resultado = models.TextField(blank=True, default='')
    horas_herramienta_al_cierre = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    motivo_cancelacion = models.CharField(max_length=255, blank=True, default='')
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='mantenimientos_creados'
    )
    cerrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='mantenimientos_cerrados',
        null=True, blank=True,
    )

    class Meta:
        db_table = 'herramientas_mantenimiento'
        verbose_name = 'Registro de mantenimiento'
        verbose_name_plural = 'Registros de mantenimiento'
        ordering = ['-fecha_inicio', '-id']
        indexes = [models.Index(fields=['herramienta', 'estado'])]
        constraints = [
            models.UniqueConstraint(
                fields=['herramienta'],
                condition=models.Q(estado='EN_CURSO'),
                name='un_mantenimiento_en_curso_por_herramienta',
            ),
            models.CheckConstraint(
                condition=models.Q(costo__isnull=True) | models.Q(costo__gte=0),
                name='mantenimiento_costo_no_negativo',
            ),
        ]


class IncidenciaHerramienta(models.Model):
    class Tipo(models.TextChoices):
        DANO = 'DANO', 'Daño'
        ROBO = 'ROBO', 'Robo'
        PERDIDA = 'PERDIDA', 'Pérdida'
        OTRO = 'OTRO', 'Otro'

    class Estado(models.TextChoices):
        ABIERTA = 'ABIERTA', 'Abierta'
        RESUELTA = 'RESUELTA', 'Resuelta'

    class Decision(models.TextChoices):
        REPARAR = 'REPARAR', 'Reparar'
        DAR_DE_BAJA = 'DAR_DE_BAJA', 'Dar de baja'
        REPONER = 'REPONER', 'Reponer (comprar otra)'
        SIN_ACCION = 'SIN_ACCION', 'Sin acción'

    herramienta = models.ForeignKey(
        Herramienta, on_delete=models.RESTRICT, related_name='incidencias'
    )
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    estado = models.CharField(
        max_length=10, choices=Estado.choices, default=Estado.ABIERTA, db_index=True
    )
    descripcion = models.TextField()
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='incidencias_como_responsable',
        null=True, blank=True,
    )
    reportada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='incidencias_reportadas'
    )
    decision = models.CharField(max_length=12, choices=Decision.choices, blank=True, default='')
    notas_resolucion = models.TextField(blank=True, default='')
    resuelta_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='incidencias_resueltas',
        null=True, blank=True,
    )
    fecha_resolucion = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'herramientas_incidencia'
        verbose_name = 'Incidencia de herramienta'
        verbose_name_plural = 'Incidencias de herramientas'
        ordering = ['-fecha', '-id']
        indexes = [models.Index(fields=['herramienta', 'estado'])]
