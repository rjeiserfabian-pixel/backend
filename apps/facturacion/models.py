from django.conf import settings
from django.db import models
from django.utils import timezone


class ComprobanteElectronico(models.Model):
    """
    Un documento (factura, boleta, nota de crédito/débito o guía de remisión)
    preparado en el sistema para enviarse a la API SUNAT propia del taller.

    El envío NUNCA es automático: se crea en PENDIENTE_ENVIO al preparar el
    documento (ver FacturacionService) y solo cambia de estado cuando un
    usuario dispara explícitamente la acción de emitir/reintentar/anular.
    Cada intento de comunicación queda en ComprobanteEnvioLog para auditoría.
    """

    class TipoDocumento(models.TextChoices):
        FACTURA = '01', 'Factura Electrónica'
        BOLETA = '03', 'Boleta de Venta Electrónica'
        NOTA_CREDITO = '07', 'Nota de Crédito'
        NOTA_DEBITO = '08', 'Nota de Débito'
        GUIA_REMISION_REMITENTE = '09', 'Guía de Remisión Remitente'

    class Estado(models.TextChoices):
        PENDIENTE_ENVIO = 'PENDIENTE_ENVIO', 'Pendiente de Envío'
        ENVIADO = 'ENVIADO', 'Enviado (procesando)'
        ACEPTADO = 'ACEPTADO', 'Aceptado por SUNAT'
        RECHAZADO = 'RECHAZADO', 'Rechazado por SUNAT'
        OBSERVADO = 'OBSERVADO', 'Observado por SUNAT'
        ERROR_CONEXION = 'ERROR_CONEXION', 'Error de Conexión'
        BAJA_SOLICITADA = 'BAJA_SOLICITADA', 'Baja Solicitada'
        BAJA_ACEPTADA = 'BAJA_ACEPTADA', 'Anulado (Baja Aceptada)'

    tipo_documento = models.CharField(max_length=2, choices=TipoDocumento.choices, db_index=True)
    serie = models.CharField(max_length=10, db_index=True)
    numero = models.CharField(max_length=15, db_index=True)

    sucursal = models.ForeignKey(
        'inventario.Sucursal', on_delete=models.RESTRICT, related_name='comprobantes_electronicos'
    )
    venta = models.ForeignKey(
        'ventas.Venta', on_delete=models.RESTRICT, null=True, blank=True,
        related_name='comprobantes_electronicos'
    )
    guia_remision = models.ForeignKey(
        'inventario.GuiaRemision', on_delete=models.RESTRICT, null=True, blank=True,
        related_name='comprobantes_electronicos'
    )
    comprobante_relacionado = models.ForeignKey(
        'self', on_delete=models.RESTRICT, null=True, blank=True, related_name='notas_generadas',
        help_text='Comprobante original al que afecta esta nota de crédito/débito.'
    )
    # Compartido entre nota de crédito/débito (catálogo 09 de motivos) y guía
    # de remisión (catálogo de motivo de traslado): mismo tipo de dato, no
    # amerita dos columnas separadas.
    motivo_codigo = models.CharField(max_length=2, blank=True, null=True)

    # Snapshot del cliente al momento de preparar el documento: la bandeja no
    # depende de que la Venta/Cliente originales no cambien después, y las
    # notas de crédito/débito (que no tienen Venta propia) reusan estos datos.
    cliente_tipo_documento = models.CharField(
        max_length=3, choices=[('DNI', 'DNI'), ('RUC', 'RUC')], default='DNI'
    )
    cliente_documento = models.CharField(max_length=15, blank=True, default='')
    cliente_nombre = models.CharField(max_length=200, blank=True, default='')
    moneda = models.CharField(max_length=3, default='PEN')
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Datos adicionales especificos del tipo de documento que no ameritan
    # columnas propias (ej. guía: peso_total, cantidad_bultos, ubigeo_partida,
    # ubigeo_llegada, motivo_traslado_codigo — ingresados por el usuario al
    # emitir porque el catálogo de Distrito todavía no guarda código ubigeo).
    datos_adicionales = models.JSONField(null=True, blank=True, default=dict)

    estado = models.CharField(
        max_length=20, choices=Estado.choices, default=Estado.PENDIENTE_ENVIO, db_index=True
    )
    payload_enviado = models.JSONField(null=True, blank=True)
    respuesta_api = models.JSONField(null=True, blank=True)
    mensaje_error = models.TextField(blank=True, default='')
    pdf_url = models.CharField(max_length=500, blank=True, default='')
    xml_url = models.CharField(max_length=500, blank=True, default='')
    cdr_url = models.CharField(max_length=500, blank=True, default='')
    hash_cdr = models.CharField(max_length=255, blank=True, default='')
    ticket_sunat = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Ticket para consultar el resultado asíncrono de una Comunicación de Baja.'
    )

    intentos = models.PositiveIntegerField(default=0)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='comprobantes_electronicos_creados'
    )
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='comprobantes_electronicos_enviados'
    )
    creado_en = models.DateTimeField(default=timezone.now, db_index=True)
    fecha_envio = models.DateTimeField(null=True, blank=True)
    fecha_respuesta = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'facturacion_comprobante_electronico'
        verbose_name = 'Comprobante Electrónico'
        verbose_name_plural = 'Comprobantes Electrónicos'
        ordering = ['-creado_en']
        constraints = [
            models.UniqueConstraint(
                fields=['tipo_documento', 'serie', 'numero'],
                name='comprobante_electronico_unico_tipo_serie_numero',
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_documento_display()} {self.serie}-{self.numero} [{self.estado}]"

    def cliente_tipo_documento_codigo(self) -> str:
        return '6' if self.cliente_tipo_documento == 'RUC' else '1'


class ComprobanteElectronicoDetalle(models.Model):
    """
    Ítems de una nota de crédito/débito, ingresados manualmente por el
    usuario (a diferencia de factura/boleta/guía, que derivan sus ítems de
    DetalleVenta/GuiaRemisionDetalle en el momento de armar el payload).
    """
    comprobante = models.ForeignKey(ComprobanteElectronico, on_delete=models.CASCADE, related_name='detalles')
    codigo_producto = models.CharField(max_length=50, blank=True, default='')
    descripcion = models.CharField(max_length=255)
    codigo_sunat = models.CharField(max_length=20, blank=True, default='')
    codigo_unidad = models.CharField(max_length=10, blank=True, default='NIU')
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)
    # Precio unitario SIN IGV (neto): así es como lo espera la API SUNAT en
    # el campo "precio_base" del ítem (ver payloads.py para la conversión
    # equivalente cuando el ítem viene de una Venta con precios con IGV incluido).
    precio_base = models.DecimalField(max_digits=12, decimal_places=2)
    tipo_igv_codigo = models.CharField(max_length=2, default='10')

    class Meta:
        db_table = 'facturacion_comprobante_detalle'
        verbose_name = 'Detalle de Comprobante Electrónico'
        verbose_name_plural = 'Detalles de Comprobante Electrónico'
        constraints = [
            models.CheckConstraint(condition=models.Q(cantidad__gt=0), name='comprobante_detalle_cantidad_positiva'),
        ]

    def __str__(self):
        return f"{self.cantidad} x {self.descripcion}"


class ComprobanteEnvioLog(models.Model):
    """Historial de cada intento de comunicación con la API SUNAT para un comprobante."""

    comprobante = models.ForeignKey(ComprobanteElectronico, on_delete=models.CASCADE, related_name='logs')
    endpoint = models.CharField(max_length=50)
    payload_enviado = models.JSONField(null=True, blank=True)
    respuesta_api = models.JSONField(null=True, blank=True)
    exitoso = models.BooleanField(default=False)
    mensaje = models.TextField(blank=True, default='')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='envios_comprobante_electronico'
    )
    fecha = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'facturacion_comprobante_envio_log'
        verbose_name = 'Registro de Envío'
        verbose_name_plural = 'Registros de Envío'
        ordering = ['-fecha']

    def __str__(self):
        return f"Envío {self.comprobante_id} a {self.endpoint} [{'OK' if self.exitoso else 'ERROR'}]"
