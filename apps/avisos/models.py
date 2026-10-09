from django.conf import settings
from django.db import models
from django.utils import timezone


class AvisoCliente(models.Model):
    """
    Aviso pendiente para un cliente (recordatorio de cita, vehículo listo, mantenimiento,
    documento por vencer, cuota por cobrar...). El sistema deja el mensaje redactado y el
    personal lo envía por WhatsApp con un clic; aquí solo se registra el estado.
    """

    class Tipo(models.TextChoices):
        CITA = 'CITA', 'Recordatorio de cita'
        VEHICULO_LISTO = 'VEHICULO_LISTO', 'Vehículo listo'
        COTIZACION = 'COTIZACION', 'Cotización por vencer'
        MANTENIMIENTO = 'MANTENIMIENTO', 'Mantenimiento próximo'
        DOCUMENTO = 'DOCUMENTO', 'Documento por vencer'
        CUOTA = 'CUOTA', 'Cobranza de cuota'

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        ENVIADO = 'ENVIADO', 'Enviado'
        DESCARTADO = 'DESCARTADO', 'Descartado'

    tipo = models.CharField(max_length=20, choices=Tipo.choices, db_index=True)
    # Identifica el hecho que originó el aviso (ej. "cita:12"); junto con el tipo evita duplicados.
    referencia = models.CharField(max_length=120)
    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.CASCADE, related_name='avisos')
    cliente_nombre = models.CharField(max_length=200)
    telefono = models.CharField(max_length=20, blank=True, default='', help_text='Solo dígitos, con código de país (51...).')
    mensaje = models.TextField()
    fecha_evento = models.DateField(null=True, blank=True, db_index=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE, db_index=True)
    descartado_automatico = models.BooleanField(default=False)
    atendido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='avisos_atendidos'
    )
    atendido_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'aviso_cliente'
        verbose_name = 'Aviso a cliente'
        verbose_name_plural = 'Avisos a clientes'
        ordering = ['fecha_evento', '-id']
        constraints = [
            models.UniqueConstraint(fields=['tipo', 'referencia'], name='aviso_unico_por_hecho'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.cliente_nombre}'
