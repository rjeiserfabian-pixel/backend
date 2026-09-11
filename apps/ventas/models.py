import logging
from django.db import models
from django.conf import settings
from django.utils import timezone
from apps.inventario.models import Sucursal, Almacen, Repuesto
from apps.clientes.models import Cliente
from apps.vehiculos.models import Vehiculo

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# CONFIGURACIONES DE CAJA Y FACTURACIÓN
# ──────────────────────────────────────────────

class Caja(models.Model):
    class Tipo(models.TextChoices):
        OPERATIVA   = 'OPERATIVA',   'Caja Operativa'
        CAJA_CHICA  = 'CAJA_CHICA',  'Caja Chica'

    sucursal        = models.ForeignKey(Sucursal, on_delete=models.RESTRICT, related_name='cajas')
    almacen_defecto = models.ForeignKey(Almacen, on_delete=models.RESTRICT, related_name='cajas_defecto', null=True, blank=True)
    nombre          = models.CharField(max_length=150, db_index=True)
    tipo            = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.OPERATIVA, db_index=True)
    estado          = models.BooleanField(default=True)

    class Meta:
        db_table         = 'ventas_caja'
        verbose_name     = 'Caja'
        verbose_name_plural = 'Cajas'
        unique_together  = [('sucursal', 'nombre')]

    def __str__(self):
        return f"{self.nombre} ({self.sucursal.nombre})"


class SesionCaja(models.Model):
    class Estado(models.TextChoices):
        ABIERTA               = 'ABIERTA',               'Abierta'
        CERRADA               = 'CERRADA',               'Cerrada'
        CERRADA_CON_DIFERENCIA = 'CERRADA_CON_DIFERENCIA', 'Cerrada con Diferencia'

    caja               = models.ForeignKey(Caja, on_delete=models.RESTRICT, related_name='sesiones')
    usuario            = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='sesiones_caja')
    fecha_apertura     = models.DateTimeField(auto_now_add=True, db_index=True)
    fecha_cierre       = models.DateTimeField(null=True, blank=True)
    saldo_inicial      = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    saldo_cierre_esperado = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    saldo_cierre_real  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    estado             = models.CharField(max_length=30, choices=Estado.choices, default=Estado.ABIERTA, db_index=True)

    class Meta:
        db_table         = 'ventas_sesion_caja'
        verbose_name     = 'Sesión de Caja'
        verbose_name_plural = 'Sesiones de Caja'
        ordering         = ['-fecha_apertura']
        constraints = [
            # Evita dos sesiones ABIERTA simultáneas en la misma caja (antes
            # solo se validaba con un .filter().exists() sin bloqueo, con
            # condición de carrera real bajo uso concurrente).
            models.UniqueConstraint(
                fields=['caja'],
                condition=models.Q(estado='ABIERTA'),
                name='unica_sesion_abierta_por_caja',
            ),
        ]

    def __str__(self):
        return f"Sesión {self.id} - {self.caja.nombre} ({self.usuario})"


class TipoComprobante(models.Model):
    nombre = models.CharField(max_length=50, unique=True, db_index=True)
    codigo_sunat = models.CharField(max_length=10, null=True, blank=True)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'ventas_tipo_comprobante'
        verbose_name = 'Tipo de Comprobante'
        verbose_name_plural = 'Tipos de Comprobantes'
        ordering = ['id']

    def __str__(self):
        return self.nombre


class SerieComprobante(models.Model):
    sucursal = models.ForeignKey(Sucursal, on_delete=models.RESTRICT, related_name='series_comprobantes')
    tipo_comprobante = models.ForeignKey(TipoComprobante, on_delete=models.RESTRICT, null=True, blank=True, related_name='series')
    serie = models.CharField(max_length=10, db_index=True)  # Ej: F001, B001
    correlativo_actual = models.IntegerField(default=0)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'ventas_serie_comprobante'
        verbose_name = 'Serie de Comprobante'
        verbose_name_plural = 'Series de Comprobantes'
        unique_together = [('sucursal', 'tipo_comprobante', 'serie')]
        ordering = ['id']

    def __str__(self):
        tipo_nombre = self.tipo_comprobante.nombre if self.tipo_comprobante else 'N/A'
        return f"{self.serie} ({tipo_nombre}) - {self.sucursal.nombre}"

    def generar_siguiente_correlativo(self) -> str:
        siguiente = self.correlativo_actual + 1
        return f"{self.serie}-{str(siguiente).zfill(6)}"


class SerieDocumentoInterno(models.Model):
    class TipoDocumento(models.TextChoices):
        RECIBO_INGRESO = 'RECIBO_INGRESO', 'Recibo de Ingreso'
        CREDITO = 'CREDITO', 'Código de Crédito'
        GUIA_REMISION = 'GUIA_REMISION', 'Guía de Remisión'
        
        
    sucursal = models.ForeignKey(Sucursal, on_delete=models.RESTRICT, related_name='series_internas')
    tipo_documento = models.CharField(max_length=50, choices=TipoDocumento.choices)
    prefijo = models.CharField(max_length=10, db_index=True)
    correlativo_actual = models.IntegerField(default=0)
    longitud_correlativo = models.IntegerField(default=6)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'ventas_serie_interna'
        verbose_name = 'Serie Interna'
        verbose_name_plural = 'Series Internas'
        unique_together = [('sucursal', 'tipo_documento')]
        ordering = ['id']

    def __str__(self):
        return f"{self.get_tipo_documento_display()} ({self.prefijo}) - {self.sucursal.nombre}"

    def generar_siguiente_correlativo(self) -> str:
        siguiente = self.correlativo_actual + 1
        return f"{self.prefijo}-{str(siguiente).zfill(self.longitud_correlativo)}"


class MetodoPago(models.Model):
    nombre = models.CharField(max_length=50, unique=True, db_index=True)
    requiere_referencia = models.BooleanField(default=False)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'ventas_metodo_pago'
        verbose_name = 'Método de Pago'
        verbose_name_plural = 'Métodos de Pago'
        ordering = ['id']

    def __str__(self):
        return self.nombre


class Impuesto(models.Model):
    nombre = models.CharField(max_length=50, unique=True, db_index=True)
    tasa = models.DecimalField(max_digits=5, decimal_places=2)  # Ej: 18.00
    codigo_sunat = models.CharField(max_length=10, null=True, blank=True)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'ventas_impuesto'
        verbose_name = 'Impuesto'
        verbose_name_plural = 'Impuestos'

    def __str__(self):
        return f"{self.nombre} ({self.tasa}%)"


# ──────────────────────────────────────────────
# MODELOS TRANSACCIONALES (VENTA)
# ──────────────────────────────────────────────

class Venta(models.Model):
    class Estado(models.TextChoices):
        PRE_VENTA = 'PRE_VENTA', 'Pre Venta (Kiosko)'
        PENDIENTE_PAGO = 'PENDIENTE_PAGO', 'Pendiente de Pago'
        PAGADA = 'PAGADA', 'Pagada'
        AL_CREDITO = 'AL_CREDITO', 'Al Crédito'
        ANULADA = 'ANULADA', 'Anulada'

    class Moneda(models.TextChoices):
        PEN = 'PEN', 'Soles (S/)'
        USD = 'USD', 'Dólares ($)'
        EUR = 'EUR', 'Euros (€)'

    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT, related_name='ventas')
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.RESTRICT, related_name='ventas', null=True, blank=True)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.RESTRICT, related_name='ventas')
    sesion_caja = models.ForeignKey(SesionCaja, on_delete=models.RESTRICT, related_name='ventas', null=True, blank=True)
    
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PRE_VENTA, db_index=True)
    moneda = models.CharField(max_length=3, choices=Moneda.choices, default=Moneda.PEN, db_index=True)
    tipo_cambio = models.DecimalField(max_digits=10, decimal_places=4, default=1.0000)
    tipo_comprobante = models.ForeignKey(TipoComprobante, on_delete=models.RESTRICT, null=True, blank=True)
    serie_correlativo = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    ticket_kiosko = models.CharField(max_length=20, null=True, blank=True, db_index=True)  # Ej: TK-482
    kilometraje = models.IntegerField(null=True, blank=True)  # Kilometraje del vehículo al momento del ingreso

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    igv = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    creado_en = models.DateTimeField(default=timezone.now, db_index=True)
    fecha_emision = models.DateTimeField(null=True, blank=True, db_index=True)
    anulado_en = models.DateTimeField(null=True, blank=True)

    monto_recibido = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    vuelto = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        db_table = 'ventas_venta'
        verbose_name = 'Venta'
        verbose_name_plural = 'Ventas'
        ordering = ['-creado_en']

    def __str__(self):
        return self.serie_correlativo or self.ticket_kiosko or f"Pre-Venta #{self.id}"


class DetalleVenta(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name='detalles')
    repuesto = models.ForeignKey(Repuesto, on_delete=models.RESTRICT, related_name='detalles_venta', null=True, blank=True)
    descripcion_servicio = models.CharField(max_length=255, null=True, blank=True)

    almacen_origen = models.ForeignKey(Almacen, on_delete=models.RESTRICT, related_name='despachos_venta', null=True, blank=True)
    
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    impuesto_aplicado = models.ForeignKey(Impuesto, on_delete=models.RESTRICT, related_name='detalles_venta', null=True, blank=True)
    monto_impuesto = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    subtotal_linea = models.DecimalField(max_digits=10, decimal_places=2)  # cantidad * precio_unitario - descuento

    class Meta:
        db_table = 'ventas_detalle_venta'
        verbose_name = 'Detalle de Venta'
        verbose_name_plural = 'Detalles de Venta'
        constraints = [
            models.CheckConstraint(condition=models.Q(cantidad__gt=0), name='detalle_venta_cantidad_positiva'),
        ]

    def __str__(self):
        nombre = self.repuesto.nombre if self.repuesto else self.descripcion_servicio
        return f"{self.cantidad} x {nombre} (Venta {self.venta_id})"


class MovimientoCaja(models.Model):
    class Tipo(models.TextChoices):
        INGRESO = 'INGRESO', 'Ingreso'
        EGRESO  = 'EGRESO',  'Egreso'

    class Concepto(models.TextChoices):
        # Ingresos automáticos
        VENTA            = 'VENTA',            'Venta'
        COBRO_CUOTA      = 'COBRO_CUOTA',      'Cobro de Cuota'
        REPARACION       = 'REPARACION',       'Servicio / Reparación'
        ANTICIPO         = 'ANTICIPO',         'Anticipo'
        OTROS_INGRESOS   = 'OTROS_INGRESOS',   'Otros Ingresos'
        # Egresos automáticos
        GASTO_OPERATIVO  = 'GASTO_OPERATIVO',  'Gasto Operativo'
        PAGO_PROVEEDOR   = 'PAGO_PROVEEDOR',   'Pago a Proveedor'
        DEVOLUCION       = 'DEVOLUCION',       'Devolución'
        RETIRO           = 'RETIRO',           'Retiro'
        OTROS_EGRESOS    = 'OTROS_EGRESOS',    'Otros Egresos'
        # Manuales (requieren aprobación)
        INGRESO_MANUAL   = 'INGRESO_MANUAL',   'Ingreso Manual (Ajuste)'
        EGRESO_MANUAL    = 'EGRESO_MANUAL',    'Egreso Manual (Gasto)'
        # Transferencias
        TRANSFERENCIA_SALIENTE = 'TRANSFERENCIA_SALIENTE', 'Transferencia Saliente'
        TRANSFERENCIA_ENTRANTE = 'TRANSFERENCIA_ENTRANTE', 'Transferencia Entrante'

    class OrigenMovimiento(models.TextChoices):
        VENTA            = 'VENTA',            'Venta'
        COBRO            = 'COBRO',            'Cobro de Cuota'
        PAGO_PROVEEDOR   = 'PAGO_PROVEEDOR',   'Pago a Proveedor'
        TRANSFERENCIA    = 'TRANSFERENCIA',    'Transferencia entre Cajas'
        AJUSTE_MANUAL    = 'AJUSTE_MANUAL',    'Ajuste Manual'
        APERTURA         = 'APERTURA',         'Apertura de Caja'

    class EstadoMovimiento(models.TextChoices):
        APROBADO  = 'APROBADO',  'Aprobado'
        PENDIENTE = 'PENDIENTE', 'Pendiente de Aprobación'
        RECHAZADO = 'RECHAZADO', 'Rechazado'

    sesion            = models.ForeignKey(SesionCaja, on_delete=models.CASCADE, related_name='movimientos')
    tipo              = models.CharField(max_length=15, choices=Tipo.choices, db_index=True)
    concepto          = models.CharField(max_length=30, choices=Concepto.choices, db_index=True)
    metodo_pago       = models.ForeignKey(MetodoPago, on_delete=models.RESTRICT, related_name='movimientos_caja')
    monto             = models.DecimalField(max_digits=10, decimal_places=2)
    numero_recibo     = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    referencia        = models.CharField(max_length=100, null=True, blank=True)
    # Trazabilidad del origen del movimiento
    origen_movimiento = models.CharField(max_length=20, choices=OrigenMovimiento.choices, default=OrigenMovimiento.AJUSTE_MANUAL, db_index=True)
    referencia_origen = models.CharField(max_length=100, null=True, blank=True, help_text="Ej: V-00125, CXP-0031")
    # Estado (para movimientos manuales que requieren aprobación)
    estado_movimiento = models.CharField(max_length=15, choices=EstadoMovimiento.choices, default=EstadoMovimiento.APROBADO, db_index=True)
    aprobado_por      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_aprobados')
    observacion       = models.TextField(null=True, blank=True)
    venta_origen      = models.ForeignKey('Venta', on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_caja')
    fecha             = models.DateTimeField(auto_now_add=True, db_index=True)
    creado_por        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='movimientos_caja_creados')

    class Meta:
        db_table         = 'ventas_movimiento_caja'
        verbose_name     = 'Movimiento de Caja'
        verbose_name_plural = 'Movimientos de Caja'
        ordering         = ['-fecha']
        constraints = [
            models.CheckConstraint(condition=models.Q(monto__gt=0), name='movimiento_caja_monto_positivo'),
        ]

    def __str__(self):
        return f"[{self.tipo}] {self.monto} - {self.concepto} ({self.metodo_pago.nombre})"

    def save(self, *args, **kwargs):
        if not self.numero_recibo and self.tipo == self.Tipo.INGRESO:
            from .models import SerieDocumentoInterno
            # Intenta obtener la sucursal desde la sesión de caja
            if hasattr(self, 'sesion') and self.sesion and self.sesion.caja and self.sesion.caja.sucursal:
                sucursal = self.sesion.caja.sucursal
                serie = SerieDocumentoInterno.objects.filter(
                    sucursal=sucursal,
                    tipo_documento=SerieDocumentoInterno.TipoDocumento.RECIBO_INGRESO,
                    estado=True
                ).select_for_update().first()
                if serie:
                    self.numero_recibo = serie.generar_siguiente_correlativo()
                    serie.correlativo_actual += 1
                    serie.save(update_fields=['correlativo_actual'])
            
            if not self.numero_recibo:
                # Si no hay serie configurada, generamos un genérico
                from django.db.models import Max
                # Usamos id+1 o count() para no colisionar tan fácil,
                # pero idealmente el cliente debe configurar su serie
                max_id = MovimientoCaja.objects.aggregate(max_id=Max('id'))['max_id'] or 0
                self.numero_recibo = f"RI-{(max_id + 1):06d}"
                
        super().save(*args, **kwargs)


class PagoVenta(models.Model):
    """
    Registra cómo se pagó una venta (puede ser múltiples métodos para una sola venta).
    Esto es el detalle del pago vinculado a la venta, pero el dinero entra por MovimientoCaja.
    """
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name='pagos')
    movimiento_caja = models.OneToOneField(MovimientoCaja, on_delete=models.RESTRICT, related_name='pago_venta_rel')
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_pago = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ventas_pago_venta'
        verbose_name = 'Pago de Venta'
        verbose_name_plural = 'Pagos de Venta'


# ──────────────────────────────────────────────
# MODELOS DE CRÉDITO Y CUENTAS POR COBRAR
# ──────────────────────────────────────────────

class CuentaPorCobrar(models.Model):
    class Frecuencia(models.TextChoices):
        DIARIO = 'DIARIO', 'Diario'
        SEMANAL = 'SEMANAL', 'Semanal'
        QUINCENAL = 'QUINCENAL', 'Quincenal'
        MENSUAL = 'MENSUAL', 'Mensual'

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        PAGADO = 'PAGADO', 'Pagado'
        ATRASADO = 'ATRASADO', 'Atrasado'

    venta = models.OneToOneField(Venta, on_delete=models.RESTRICT, related_name='cuenta_por_cobrar')
    codigo_credito = models.CharField(max_length=50, unique=True, db_index=True)
    frecuencia_pago = models.CharField(max_length=15, choices=Frecuencia.choices)
    
    monto_financiado = models.DecimalField(max_digits=12, decimal_places=2)
    saldo_pendiente = models.DecimalField(max_digits=12, decimal_places=2)
    
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE, db_index=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ventas_cuenta_cobrar'
        verbose_name = 'Cuenta por Cobrar'
        verbose_name_plural = 'Cuentas por Cobrar'

    def __str__(self):
        return f"{self.codigo_credito} - {self.venta.cliente.nombres}"


class CuotaCredito(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        PARCIAL = 'PARCIAL', 'Parcial'
        PAGADA = 'PAGADA', 'Pagada'
        ATRASADA = 'ATRASADA', 'Atrasada'

    cuenta_cobrar = models.ForeignKey(CuentaPorCobrar, on_delete=models.CASCADE, related_name='cuotas')
    numero_cuota = models.PositiveIntegerField()
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    saldo_pendiente = models.DecimalField(max_digits=10, decimal_places=2)
    
    fecha_vencimiento = models.DateField(db_index=True)
    fecha_pago = models.DateField(null=True, blank=True)
    
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.PENDIENTE, db_index=True)

    class Meta:
        db_table = 'ventas_cuota_credito'
        verbose_name = 'Cuota de Crédito'
        verbose_name_plural = 'Cuotas de Crédito'
        unique_together = [('cuenta_cobrar', 'numero_cuota')]
        ordering = ['numero_cuota']

    def __str__(self):
        return f"Cuota {self.numero_cuota} - {self.cuenta_cobrar.codigo_credito}"

import uuid

class PagoCuota(models.Model):
    cuota          = models.ForeignKey(CuotaCredito, on_delete=models.CASCADE, related_name='pagos')
    movimiento_caja = models.OneToOneField(MovimientoCaja, on_delete=models.RESTRICT, related_name='pago_cuota_rel')
    operacion_id   = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    monto          = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_pago     = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table         = 'ventas_pago_cuota'
        verbose_name     = 'Pago de Cuota'
        verbose_name_plural = 'Pagos de Cuota'
        ordering         = ['-fecha_pago']

    def __str__(self):
        return f"Pago {self.monto} - Cuota {self.cuota.numero_cuota}"


# ──────────────────────────────────────────────
# NUEVOS MODELOS: TRANSFERENCIAS Y ARQUEOS
# ──────────────────────────────────────────────

class TransferenciaCaja(models.Model):
    """Audita transferencias de dinero entre cajas (ej. Caja 01 → Caja Chica).
    Al completarse genera dos MovimientoCaja: EGRESO en origen e INGRESO en destino.
    """
    class Estado(models.TextChoices):
        COMPLETADA = 'COMPLETADA', 'Completada'
        RECHAZADA  = 'RECHAZADA',  'Rechazada'

    sesion_origen      = models.ForeignKey(SesionCaja, on_delete=models.RESTRICT, related_name='transferencias_enviadas')
    sesion_destino     = models.ForeignKey(SesionCaja, on_delete=models.RESTRICT, related_name='transferencias_recibidas')
    movimiento_salida  = models.OneToOneField(MovimientoCaja, on_delete=models.RESTRICT, related_name='transferencia_salida', null=True, blank=True)
    movimiento_entrada = models.OneToOneField(MovimientoCaja, on_delete=models.RESTRICT, related_name='transferencia_entrada', null=True, blank=True)
    monto              = models.DecimalField(max_digits=10, decimal_places=2)
    motivo             = models.TextField(null=True, blank=True)
    estado             = models.CharField(max_length=15, choices=Estado.choices, default=Estado.COMPLETADA, db_index=True)
    usuario            = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='transferencias_caja')
    fecha              = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table         = 'cajas_transferencia'
        verbose_name     = 'Transferencia entre Cajas'
        verbose_name_plural = 'Transferencias entre Cajas'
        ordering         = ['-fecha']
        constraints = [
            models.CheckConstraint(condition=models.Q(monto__gt=0), name='transferencia_caja_monto_positivo'),
            models.CheckConstraint(
                condition=~models.Q(sesion_origen=models.F('sesion_destino')),
                name='transferencia_caja_origen_distinto_destino',
            ),
        ]

    def __str__(self):
        return f"Transferencia S/ {self.monto}: {self.sesion_origen.caja.nombre} → {self.sesion_destino.caja.nombre}"


class ArqueoCaja(models.Model):
    """Registra el arqueo físico de una sesión de caja.
    Calcula la diferencia entre el saldo teórico (sistema) y el saldo contado (físico).
    """
    sesion          = models.ForeignKey(SesionCaja, on_delete=models.CASCADE, related_name='arqueos')
    saldo_teorico   = models.DecimalField(max_digits=12, decimal_places=2, help_text="Calculado por el sistema")
    saldo_contado   = models.DecimalField(max_digits=12, decimal_places=2, help_text="Contado físicamente por el cajero")
    diferencia      = models.DecimalField(max_digits=12, decimal_places=2, help_text="saldo_contado - saldo_teorico (negativo = faltante)")
    motivo_diferencia = models.TextField(null=True, blank=True)
    es_cierre_final = models.BooleanField(default=False, help_text="True si este arqueo es el que cierra la sesión")
    usuario         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='arqueos_realizados')
    fecha           = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table         = 'cajas_arqueo'
        verbose_name     = 'Arqueo de Caja'
        verbose_name_plural = 'Arqueos de Caja'
        ordering         = ['-fecha']

    def __str__(self):
        return f"Arqueo Sesión {self.sesion_id} | Diferencia: {self.diferencia}"
