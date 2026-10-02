import logging
from django.db import models
from django.conf import settings
from apps.vehiculos.models import Vehiculo
from apps.inventario.models import Repuesto

logger = logging.getLogger(__name__)

class TipoServicio(models.Model):
    nombre = models.CharField(max_length=50, unique=True, db_index=True)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'taller_tipo_servicio'
        verbose_name = 'Tipo de Servicio'
        verbose_name_plural = 'Tipos de Servicio'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Cita(models.Model):
    class Estado(models.TextChoices):
        SOLICITADA = 'SOLICITADA', 'Solicitada'
        CONFIRMADA = 'CONFIRMADA', 'Confirmada'
        REPROGRAMADA = 'REPROGRAMADA', 'Reprogramada'
        RECEPCIONADA = 'RECEPCIONADA', 'Recepcionada'
        CANCELADA = 'CANCELADA', 'Cancelada'
        NO_ASISTIO = 'NO_ASISTIO', 'No asistio'

    class Origen(models.TextChoices):
        LLAMADA = 'LLAMADA', 'Llamada'
        WHATSAPP = 'WHATSAPP', 'WhatsApp'
        PORTAL = 'PORTAL', 'Portal'
        PRESENCIAL = 'PRESENCIAL', 'Presencial'
        OTRO = 'OTRO', 'Otro'

    numero = models.CharField(max_length=20, unique=True, db_index=True)
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.RESTRICT, related_name='citas')
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.RESTRICT, related_name='citas')
    sucursal = models.ForeignKey('inventario.Sucursal', on_delete=models.RESTRICT, related_name='citas')
    tipo_servicio = models.ForeignKey(TipoServicio, on_delete=models.RESTRICT, related_name='citas', null=True, blank=True)
    fecha_inicio = models.DateTimeField(db_index=True)
    fecha_fin = models.DateTimeField(db_index=True)
    duracion_minutos = models.PositiveIntegerField(default=60)
    origen = models.CharField(max_length=20, choices=Origen.choices, default=Origen.LLAMADA, db_index=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.CONFIRMADA, db_index=True)
    mecanico_preferido = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name='citas_preferidas',
        null=True,
        blank=True,
    )
    asesor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name='citas_registradas',
        null=True,
        blank=True,
    )
    motivo = models.TextField(null=True, blank=True)
    observaciones_cliente = models.TextField(null=True, blank=True)
    observaciones_internas = models.TextField(null=True, blank=True)
    kilometraje_estimado = models.IntegerField(null=True, blank=True)
    orden_trabajo = models.OneToOneField(
        'taller.OrdenTrabajo',
        on_delete=models.SET_NULL,
        related_name='cita_origen',
        null=True,
        blank=True,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name='citas_creadas',
        null=True,
        blank=True,
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'taller_cita'
        verbose_name = 'Cita'
        verbose_name_plural = 'Citas'
        ordering = ['fecha_inicio']
        indexes = [
            models.Index(fields=['sucursal', 'fecha_inicio'], name='idx_cita_sucursal_inicio'),
            models.Index(fields=['estado', 'fecha_inicio'], name='idx_cita_estado_inicio'),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(duracion_minutos__gt=0), name='cita_duracion_positiva'),
            models.CheckConstraint(condition=models.Q(fecha_fin__gt=models.F('fecha_inicio')), name='cita_fecha_fin_mayor_inicio'),
        ]

    def __str__(self):
        return f"Cita-{self.numero} | {self.vehiculo.placa}"


class CitaHistorial(models.Model):
    class Accion(models.TextChoices):
        CREACION = 'CREACION', 'Creacion'
        EDICION = 'EDICION', 'Edicion'
        REPROGRAMACION = 'REPROGRAMACION', 'Reprogramacion'
        CAMBIO_ESTADO = 'CAMBIO_ESTADO', 'Cambio de estado'
        RECEPCION = 'RECEPCION', 'Recepcion'
        CANCELACION = 'CANCELACION', 'Cancelacion'
        NO_ASISTIO = 'NO_ASISTIO', 'No asistio'

    cita = models.ForeignKey(Cita, on_delete=models.CASCADE, related_name='historial')
    accion = models.CharField(max_length=30, choices=Accion.choices, db_index=True)
    estado_anterior = models.CharField(max_length=20, choices=Cita.Estado.choices, null=True, blank=True)
    estado_nuevo = models.CharField(max_length=20, choices=Cita.Estado.choices, null=True, blank=True)
    fecha_inicio_anterior = models.DateTimeField(null=True, blank=True)
    fecha_inicio_nueva = models.DateTimeField(null=True, blank=True)
    observacion = models.TextField(null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, null=True, blank=True)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'taller_cita_historial'
        verbose_name = 'Historial de Cita'
        verbose_name_plural = 'Historial de Citas'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['cita', '-fecha'], name='idx_cita_historial_fecha'),
        ]

    def __str__(self):
        return f"{self.cita.numero} | {self.accion}"


class ListaEsperaCita(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        CONTACTADO = 'CONTACTADO', 'Contactado'
        CONVERTIDO = 'CONVERTIDO', 'Convertido a cita'
        DESCARTADO = 'DESCARTADO', 'Descartado'

    sucursal = models.ForeignKey('inventario.Sucursal', on_delete=models.RESTRICT, related_name='lista_espera_citas')
    tipo_servicio = models.ForeignKey(TipoServicio, on_delete=models.RESTRICT, related_name='lista_espera_citas', null=True, blank=True)
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.RESTRICT, related_name='lista_espera_citas', null=True, blank=True)
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.RESTRICT, related_name='lista_espera_citas', null=True, blank=True)
    documento = models.CharField(max_length=15, db_index=True)
    nombres = models.CharField(max_length=150)
    apellidos = models.CharField(max_length=150, blank=True, default='')
    telefono = models.CharField(max_length=20)
    email = models.EmailField(null=True, blank=True)
    placa = models.CharField(max_length=15, db_index=True)
    marca = models.CharField(max_length=100, blank=True, default='')
    modelo = models.CharField(max_length=100, blank=True, default='')
    fecha_preferida = models.DateField(db_index=True)
    hora_preferida = models.TimeField(null=True, blank=True)
    duracion_minutos = models.PositiveIntegerField(default=60)
    motivo = models.TextField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE, db_index=True)
    cita_convertida = models.OneToOneField(Cita, on_delete=models.SET_NULL, null=True, blank=True, related_name='origen_lista_espera')
    observaciones_internas = models.TextField(null=True, blank=True)
    creado_desde_portal = models.BooleanField(default=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'taller_lista_espera_cita'
        verbose_name = 'Lista de Espera de Cita'
        verbose_name_plural = 'Lista de Espera de Citas'
        ordering = ['-fecha_creacion']
        indexes = [
            models.Index(fields=['sucursal', 'estado', 'fecha_preferida'], name='idx_lista_espera_sucursal'),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(duracion_minutos__gt=0), name='lista_espera_duracion_positiva'),
        ]

    def __str__(self):
        return f"{self.placa} | {self.fecha_preferida} | {self.estado}"


class ConfiguracionAgendaSucursal(models.Model):
    sucursal = models.OneToOneField('inventario.Sucursal', on_delete=models.CASCADE, related_name='configuracion_agenda')
    intervalo_minutos = models.PositiveIntegerField(default=30)
    capacidad_simultanea = models.PositiveIntegerField(default=3)
    activo = models.BooleanField(default=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'taller_configuracion_agenda_sucursal'
        verbose_name = 'Configuracion de Agenda por Sucursal'
        verbose_name_plural = 'Configuraciones de Agenda por Sucursal'
        constraints = [
            models.CheckConstraint(condition=models.Q(intervalo_minutos__gt=0), name='agenda_intervalo_positivo'),
            models.CheckConstraint(condition=models.Q(capacidad_simultanea__gt=0), name='agenda_capacidad_positiva'),
        ]

    def __str__(self):
        return f"Agenda {self.sucursal.nombre}"


class HorarioAgendaSucursal(models.Model):
    class DiaSemana(models.IntegerChoices):
        LUNES = 0, 'Lunes'
        MARTES = 1, 'Martes'
        MIERCOLES = 2, 'Miercoles'
        JUEVES = 3, 'Jueves'
        VIERNES = 4, 'Viernes'
        SABADO = 5, 'Sabado'
        DOMINGO = 6, 'Domingo'

    configuracion = models.ForeignKey(ConfiguracionAgendaSucursal, on_delete=models.CASCADE, related_name='horarios')
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_inicio = models.TimeField(default='08:00')
    hora_fin = models.TimeField(default='18:00')
    cerrado = models.BooleanField(default=False)

    class Meta:
        db_table = 'taller_horario_agenda_sucursal'
        verbose_name = 'Horario de Agenda'
        verbose_name_plural = 'Horarios de Agenda'
        ordering = ['dia_semana']
        unique_together = [('configuracion', 'dia_semana')]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cerrado=True) | models.Q(hora_fin__gt=models.F('hora_inicio')),
                name='agenda_horario_fin_mayor_inicio_o_cerrado',
            ),
        ]

    def __str__(self):
        return f"{self.configuracion.sucursal.nombre} - {self.get_dia_semana_display()}"


class BloqueoAgendaSucursal(models.Model):
    sucursal = models.ForeignKey('inventario.Sucursal', on_delete=models.CASCADE, related_name='bloqueos_agenda')
    fecha_inicio = models.DateTimeField(db_index=True)
    fecha_fin = models.DateTimeField(db_index=True)
    motivo = models.CharField(max_length=180)
    activo = models.BooleanField(default=True, db_index=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'taller_bloqueo_agenda_sucursal'
        verbose_name = 'Bloqueo de Agenda'
        verbose_name_plural = 'Bloqueos de Agenda'
        ordering = ['fecha_inicio']
        constraints = [
            models.CheckConstraint(condition=models.Q(fecha_fin__gt=models.F('fecha_inicio')), name='bloqueo_agenda_fin_mayor_inicio'),
        ]

    def __str__(self):
        return f"{self.sucursal.nombre} | {self.motivo}"


class OrdenTrabajo(models.Model):
    class Estado(models.TextChoices):
        RECEPCIONADO = 'RECEPCIONADO', 'Recepcionado'
        INSPECCION = 'INSPECCION', 'En Inspección'
        ESPERANDO_APROBACION = 'ESPERANDO_APROBACION', 'Esperando Aprobación'
        APROBADO = 'APROBADO', 'Aprobado / En Ejecución'
        FINALIZADO = 'FINALIZADO', 'Finalizado'
        FACTURADO = 'FACTURADO', 'Facturado'
        CANCELADO = 'CANCELADO', 'Cancelado'

    numero = models.CharField(max_length=20, unique=True, db_index=True)
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.RESTRICT, related_name='ordenes_trabajo', null=True, blank=False)
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.RESTRICT, related_name='ordenes_trabajo')
    recepcionista = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='ordenes_recepcionadas')
    mecanico_asignado = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='ordenes_asignadas', null=True, blank=True)
    
    estado = models.CharField(max_length=30, choices=Estado.choices, default=Estado.RECEPCIONADO, db_index=True)
    
    # Nuevo campo dinámico
    tipo_servicio = models.ForeignKey(TipoServicio, on_delete=models.RESTRICT, related_name='ordenes', null=True, blank=True)
    
    kilometraje_ingreso = models.IntegerField(null=True, blank=True)
    motivo_ingreso = models.TextField(help_text="Motivo principal o correctivo reportado por el cliente", null=True, blank=True)
    
    fecha_ingreso = models.DateTimeField(auto_now_add=True, db_index=True)
    fecha_estimada_entrega = models.DateTimeField(null=True, blank=True)
    fecha_finalizacion = models.DateTimeField(null=True, blank=True)
    
    # Campo para almacenar temporalmente el PDF generado de la cotización/hallazgos
    url_cotizacion_pdf = models.URLField(max_length=500, null=True, blank=True)

    # Número de la Proforma/Cotización (serie administrable en Configuración >
    # Series Internas). Se genera una sola vez, en el primer PDF, y se
    # reutiliza en reimpresiones. Si la sucursal no tiene serie PROFORMA
    # configurada, se sigue usando el número de la OT como respaldo.
    numero_cotizacion = models.CharField(max_length=20, null=True, blank=True)
    
    # Nivel transaccional: Fecha límite para aprobar la cotización
    fecha_vencimiento_cotizacion = models.DateTimeField(
        null=True, 
        blank=True,
        help_text="Nivel transaccional: Fecha límite para que el cliente apruebe la cotización."
    )

    class Meta:
        db_table = 'taller_orden_trabajo'
        verbose_name = 'Orden de Trabajo'
        verbose_name_plural = 'Órdenes de Trabajo'
        ordering = ['-fecha_ingreso']

    def __str__(self):
        return f"OT-{self.numero} | {self.vehiculo.placa}"


class OrdenHistorialEstado(models.Model):
    class MotivoCategoria(models.TextChoices):
        RECHAZO_CLIENTE = 'RECHAZO_CLIENTE', 'Cliente rechazó la cotización'
        ERROR_REGISTRO = 'ERROR_REGISTRO', 'Error en el registro'
        DUPLICADO = 'DUPLICADO', 'Orden duplicada'
        OTRO = 'OTRO', 'Otro motivo'

    orden = models.ForeignKey(OrdenTrabajo, on_delete=models.CASCADE, related_name='historial_estados')
    estado = models.CharField(max_length=30, choices=OrdenTrabajo.Estado.choices)
    fecha_registro = models.DateTimeField(auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, null=True, blank=True)
    observaciones = models.TextField(null=True, blank=True)
    # Solo aplica a transiciones a CANCELADO: distingue "el cliente dijo que no"
    # de un error interno, para poder reportar después por qué se pierden órdenes.
    motivo_categoria = models.CharField(max_length=30, choices=MotivoCategoria.choices, null=True, blank=True)

    class Meta:
        db_table = 'taller_orden_historial_estado'
        verbose_name = 'Historial de Estado'
        verbose_name_plural = 'Historial de Estados'
        ordering = ['fecha_registro']

    def __str__(self):
        return f"OT-{self.orden.numero} -> {self.get_estado_display()} ({self.fecha_registro.strftime('%d/%m/%Y %H:%M')})"



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
    # Hallazgo de inspección que dio origen a este servicio (si se generó con
    # "Convertir a Servicio"). Null si el servicio se agregó directamente.
    hallazgo_origen = models.ForeignKey(
        Hallazgo, on_delete=models.SET_NULL, null=True, blank=True, related_name='servicios_generados'
    )
    
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

    @property
    def total(self):
        return self.cantidad * self.precio_unitario

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


class PlantillaCorrectiva(models.Model):
    """
    Servicios correctivos estandarizados para seleccionar en la recepción.
    Permite registrar rápidamente los problemas/fallas reportados por el cliente
    mediante un checklist, en lugar de escribirlos manualmente.
    """
    nombre = models.CharField(max_length=150, unique=True, db_index=True)
    descripcion = models.TextField(null=True, blank=True)
    precio_base = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    tiempo_estimado_minutos = models.IntegerField(
        null=True, blank=True,
        help_text="Tiempo aproximado estimado para este correctivo"
    )
    activo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'taller_plantilla_correctiva'
        verbose_name = 'Plantilla Correctiva'
        verbose_name_plural = 'Plantillas Correctivas'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre
