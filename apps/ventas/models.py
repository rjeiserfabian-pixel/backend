import logging
from django.db import models
from django.conf import settings
from apps.inventario.models import Sucursal, Almacen, Repuesto
from apps.clientes.models import Cliente
from apps.vehiculos.models import Vehiculo

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# CONFIGURACIONES DE CAJA Y FACTURACIÓN
# ──────────────────────────────────────────────

class Caja(models.Model):
    sucursal = models.ForeignKey(Sucursal, on_delete=models.RESTRICT, related_name='cajas')
    nombre = models.CharField(max_length=150, db_index=True)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'ventas_caja'
        verbose_name = 'Caja'
        verbose_name_plural = 'Cajas'
        unique_together = [('sucursal', 'nombre')]

    def __str__(self):
        return f"{self.nombre} ({self.sucursal.nombre})"


class SesionCaja(models.Model):
    class Estado(models.TextChoices):
        ABIERTA = 'ABIERTA', 'Abierta'
        CERRADA = 'CERRADA', 'Cerrada'

    caja = models.ForeignKey(Caja, on_delete=models.RESTRICT, related_name='sesiones')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='sesiones_caja')
    fecha_apertura = models.DateTimeField(auto_now_add=True, db_index=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    saldo_inicial = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    saldo_cierre_esperado = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    saldo_cierre_real = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.ABIERTA, db_index=True)

    class Meta:
        db_table = 'ventas_sesion_caja'
        verbose_name = 'Sesión de Caja'
        verbose_name_plural = 'Sesiones de Caja'
        ordering = ['-fecha_apertura']

    def __str__(self):
        return f"Sesión {self.id} - {self.caja.nombre} ({self.usuario})"


class SerieComprobante(models.Model):
    class TipoComprobante(models.TextChoices):
        FACTURA = 'FACTURA', 'Factura'
        BOLETA = 'BOLETA', 'Boleta'
        TICKET = 'TICKET', 'Ticket de Venta'
        NOTA_CREDITO = 'NOTA_CREDITO', 'Nota de Crédito'

    sucursal = models.ForeignKey(Sucursal, on_delete=models.RESTRICT, related_name='series_comprobantes')
    tipo_comprobante = models.CharField(max_length=30, choices=TipoComprobante.choices, db_index=True)
    serie = models.CharField(max_length=10, db_index=True)  # Ej: F001, B001
    correlativo_actual = models.IntegerField(default=0)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'ventas_serie_comprobante'
        verbose_name = 'Serie de Comprobante'
        verbose_name_plural = 'Series de Comprobantes'
        unique_together = [('sucursal', 'tipo_comprobante', 'serie')]

    def __str__(self):
        return f"{self.serie} ({self.tipo_comprobante}) - {self.sucursal.nombre}"

    def generar_siguiente_correlativo(self) -> str:
        siguiente = self.correlativo_actual + 1
        return f"{self.serie}-{str(siguiente).zfill(6)}"


class MetodoPago(models.Model):
    nombre = models.CharField(max_length=50, unique=True, db_index=True)
    requiere_referencia = models.BooleanField(default=False)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'ventas_metodo_pago'
        verbose_name = 'Método de Pago'
        verbose_name_plural = 'Métodos de Pago'

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

    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT, related_name='ventas')
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.RESTRICT, related_name='ventas', null=True, blank=True)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.RESTRICT, related_name='ventas')
    sesion_caja = models.ForeignKey(SesionCaja, on_delete=models.RESTRICT, related_name='ventas', null=True, blank=True)
    
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PRE_VENTA, db_index=True)
    tipo_comprobante = models.CharField(max_length=30, choices=SerieComprobante.TipoComprobante.choices, null=True, blank=True)
    serie_correlativo = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    ticket_kiosko = models.CharField(max_length=20, null=True, blank=True, db_index=True)  # Ej: TK-482

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    igv = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    creado_en = models.DateTimeField(auto_now_add=True, db_index=True)
    fecha_emision = models.DateTimeField(null=True, blank=True, db_index=True)
    anulado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'ventas_venta'
        verbose_name = 'Venta'
        verbose_name_plural = 'Ventas'
        ordering = ['-creado_en']

    def __str__(self):
        return self.serie_correlativo or self.ticket_kiosko or f"Pre-Venta #{self.id}"


class DetalleVenta(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name='detalles')
    repuesto = models.ForeignKey(Repuesto, on_delete=models.RESTRICT, related_name='detalles_venta')
    almacen_origen = models.ForeignKey(Almacen, on_delete=models.RESTRICT, related_name='despachos_venta', null=True, blank=True)
    
    cantidad = models.PositiveIntegerField()
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    impuesto_aplicado = models.ForeignKey(Impuesto, on_delete=models.RESTRICT, related_name='detalles_venta', null=True, blank=True)
    monto_impuesto = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    subtotal_linea = models.DecimalField(max_digits=10, decimal_places=2)  # cantidad * precio_unitario - descuento

    class Meta:
        db_table = 'ventas_detalle_venta'
        verbose_name = 'Detalle de Venta'
        verbose_name_plural = 'Detalles de Venta'

    def __str__(self):
        return f"{self.cantidad} x {self.repuesto.nombre} (Venta {self.venta_id})"


class MovimientoCaja(models.Model):
    class Tipo(models.TextChoices):
        INGRESO = 'INGRESO', 'Ingreso'
        EGRESO = 'EGRESO', 'Egreso'

    class Concepto(models.TextChoices):
        VENTA = 'VENTA', 'Venta'
        COBRO_CUOTA = 'COBRO_CUOTA', 'Cobro de Cuota'
        REPARACION = 'REPARACION', 'Servicio / Reparación'
        INGRESO_MANUAL = 'INGRESO_MANUAL', 'Ingreso Manual (Ajuste)'
        EGRESO_MANUAL = 'EGRESO_MANUAL', 'Egreso Manual (Gasto)'

    sesion = models.ForeignKey(SesionCaja, on_delete=models.CASCADE, related_name='movimientos')
    tipo = models.CharField(max_length=15, choices=Tipo.choices, db_index=True)
    concepto = models.CharField(max_length=20, choices=Concepto.choices, db_index=True)
    metodo_pago = models.ForeignKey(MetodoPago, on_delete=models.RESTRICT, related_name='movimientos_caja')
    
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    referencia = models.CharField(max_length=100, null=True, blank=True)  # Nro de operación, etc.
    venta_origen = models.ForeignKey(Venta, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_caja')
    
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT, related_name='movimientos_caja_creados')

    class Meta:
        db_table = 'ventas_movimiento_caja'
        verbose_name = 'Movimiento de Caja'
        verbose_name_plural = 'Movimientos de Caja'
        ordering = ['-fecha']

    def __str__(self):
        return f"[{self.tipo}] {self.monto} - {self.concepto} ({self.metodo_pago.nombre})"


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
