from decimal import Decimal
import django.core.validators
from django.db import models
from django.conf import settings
from apps.clientes.models import Proveedor
from apps.inventario.models import Repuesto

class TipoComprobanteCompra(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    estado_activo = models.BooleanField(default=True, db_index=True)
    
    class Meta:
        db_table = 'tipo_comprobante_compra'
        verbose_name = 'Tipo de Comprobante de Compra'
        verbose_name_plural = 'Tipos de Comprobantes de Compras'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

class Compra(models.Model):

    TIPO_PAGO_CHOICES = [
        ('Contado', 'Contado'),
        ('Credito', 'Crédito'),
    ]
    ESTADO_CHOICES = [
        ('Completada', 'Completada'),
        ('Anulada', 'Anulada'),
    ]

    proveedor = models.ForeignKey(Proveedor, on_delete=models.RESTRICT, related_name='compras')
    fecha_emision = models.DateField(db_index=True)
    tipo_comprobante_fk = models.ForeignKey(TipoComprobanteCompra, on_delete=models.RESTRICT, null=True, blank=True, related_name='compras')
    serie = models.CharField(max_length=10)
    numero_comprobante = models.CharField(max_length=20, db_index=True)
    tipo_pago = models.CharField(max_length=15, choices=TIPO_PAGO_CHOICES, default='Contado')
    
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    igv = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='Completada')
    observaciones = models.TextField(null=True, blank=True)
    
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'compra'
        verbose_name = 'Compra'
        verbose_name_plural = 'Compras'
        ordering = ['-fecha_emision', '-id']

    def __str__(self):
        tipo_nombre = self.tipo_comprobante_fk.nombre if self.tipo_comprobante_fk else 'Comprobante'
        return f"{tipo_nombre} {self.serie}-{self.numero_comprobante} | {self.proveedor.nombre_o_razon_social}"


class DetalleCompra(models.Model):
    compra = models.ForeignKey(Compra, on_delete=models.CASCADE, related_name='detalles')
    repuesto = models.ForeignKey(Repuesto, on_delete=models.RESTRICT, related_name='detalles_compra')
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, help_text="Costo unitario de compra")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'detalle_compra'
        verbose_name = 'Detalle de Compra'
        verbose_name_plural = 'Detalles de Compras'

    def __str__(self):
        return f"{self.cantidad} x {self.repuesto.codigo} ({self.compra})"


class CuentaPorPagar(models.Model):
    ESTADO_CHOICES = [
        ('Pendiente', 'Pendiente'),
        ('Parcial', 'Pago Parcial'),
        ('Pagada', 'Pagada'),
        ('Anulada', 'Anulada'),
    ]

    compra = models.OneToOneField(Compra, on_delete=models.CASCADE, related_name='cuenta_por_pagar')
    proveedor = models.ForeignKey(Proveedor, on_delete=models.RESTRICT, related_name='cuentas_por_pagar')
    
    monto_total = models.DecimalField(max_digits=12, decimal_places=2)
    monto_pagado = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    saldo_pendiente = models.DecimalField(max_digits=12, decimal_places=2)
    
    fecha_vencimiento = models.DateField(db_index=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='Pendiente', db_index=True)
    
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cuenta_por_pagar'
        verbose_name = 'Cuenta por Pagar'
        verbose_name_plural = 'Cuentas por Pagar'
        ordering = ['fecha_vencimiento']

    def __str__(self):
        return f"Deuda a {self.proveedor} | Saldo: {self.saldo_pendiente} | Vence: {self.fecha_vencimiento}"


class PagoCuenta(models.Model):
    cuenta_por_pagar = models.ForeignKey(CuentaPorPagar, on_delete=models.CASCADE, related_name='pagos')
    monto_abonado = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[django.core.validators.MinValueValidator(Decimal('0.01'))]
    )
    fecha_pago = models.DateField(db_index=True)
    metodo_pago = models.CharField(max_length=50) # Ej. Efectivo, Transferencia, Yape, etc.
    referencia = models.CharField(max_length=100, null=True, blank=True, help_text="Nro de operación, etc.")

    # Si afecta_caja=True, el pago sale de la sesión de caja abierta del
    # usuario (se descuenta como egreso real de efectivo/caja, igual que un
    # cobro de cuota de venta). Si es False, es un pago hecho fuera de caja
    # (ej. transferencia bancaria directa desde la cuenta de la empresa) y no
    # debe afectar el saldo de ninguna caja.
    afecta_caja = models.BooleanField(default=True)
    movimiento_caja = models.OneToOneField(
        'ventas.MovimientoCaja', on_delete=models.RESTRICT,
        null=True, blank=True, related_name='pago_cuenta_rel'
    )

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.RESTRICT)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pago_cuenta_por_pagar'
        verbose_name = 'Pago de Cuenta por Pagar'
        verbose_name_plural = 'Pagos de Cuentas por Pagar'
        ordering = ['-fecha_pago', '-id']
        constraints = [
            models.CheckConstraint(condition=models.Q(monto_abonado__gt=0), name='pago_cuenta_monto_positivo'),
        ]

    def __str__(self):
        return f"Pago {self.monto_abonado} a {self.cuenta_por_pagar} el {self.fecha_pago}"
