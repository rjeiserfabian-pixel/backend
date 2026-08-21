import logging
from django.db import models
from django.conf import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# MODELOS EXISTENTES (sin cambios estructurales)
# ──────────────────────────────────────────────

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

    # Campo legacy mantenido para compatibilidad durante la migración.
    # Una vez ejecutado el script de datos iniciales, se puede deprecar.
    stock = models.IntegerField(default=0)

    # 4 Tipos de Precios (Globales para todas las sucursales)
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

    @property
    def stock_total_disponible(self):
        """Suma del stock disponible en TODAS las ubicaciones del catálogo."""
        return sum(s.stock_disponible for s in self.inventario_stock.all())

    @property
    def stock_total_fisico(self):
        """Suma del stock físico total (disponible + reservado + merma) en todas las ubicaciones."""
        total = 0
        for s in self.inventario_stock.all():
            total += s.stock_disponible + s.stock_reservado + s.stock_merma
        return total


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


# ──────────────────────────────────────────────
# NUEVOS MODELOS: ESTRUCTURA FÍSICA MULTI-ALMACÉN
# ──────────────────────────────────────────────

class Sucursal(models.Model):
    """Representa un local físico de la empresa (ej. Taller Principal, Taller Norte)."""
    nombre = models.CharField(max_length=150, unique=True, db_index=True)
    direccion = models.CharField(max_length=255, null=True, blank=True)
    estado = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sucursal'
        verbose_name = 'Sucursal'
        verbose_name_plural = 'Sucursales'

    def __str__(self):
        return self.nombre


class Almacen(models.Model):
    """Cada sucursal puede tener uno o más almacenes (ej. Almacén Central, Herramientas)."""
    sucursal = models.ForeignKey(Sucursal, on_delete=models.RESTRICT, related_name='almacenes')
    nombre = models.CharField(max_length=150, db_index=True)
    descripcion = models.CharField(max_length=255, null=True, blank=True)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'almacen'
        verbose_name = 'Almacen'
        verbose_name_plural = 'Almacenes'
        # Un nombre de almacén debe ser único dentro de la misma sucursal
        unique_together = [('sucursal', 'nombre')]

    def __str__(self):
        return f"{self.sucursal.nombre} → {self.nombre}"


class UbicacionFisica(models.Model):
    """
    Punto exacto dentro de un almacén.
    Código generado automáticamente si no se provee (ej. A-12-3 = Pasillo A, Estante 12, Nivel 3).
    """
    almacen = models.ForeignKey(Almacen, on_delete=models.RESTRICT, related_name='ubicaciones')
    codigo = models.CharField(max_length=50, db_index=True)  # Ej. "A-12-3"
    pasillo = models.CharField(max_length=20, null=True, blank=True)
    estante = models.CharField(max_length=20, null=True, blank=True)
    casillero = models.CharField(max_length=20, null=True, blank=True)
    descripcion = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'ubicacion_fisica'
        verbose_name = 'Ubicacion Fisica'
        verbose_name_plural = 'Ubicaciones Fisicas'
        # El código debe ser único dentro de cada almacén
        unique_together = [('almacen', 'codigo')]

    def __str__(self):
        return f"{self.almacen} / {self.codigo}"


# ──────────────────────────────────────────────
# NUEVOS MODELOS: STOCK LÓGICO Y KARDEX
# ──────────────────────────────────────────────

class InventarioStock(models.Model):
    """
    Tabla intermedia que vincula un Repuesto del catálogo con una UbicacionFisica exacta.
    Un mismo repuesto puede tener múltiples registros si está en varias ubicaciones.
    Índices compuestos en (repuesto, ubicacion) para consultas rápidas.
    """
    repuesto = models.ForeignKey(Repuesto, on_delete=models.RESTRICT, related_name='inventario_stock')
    ubicacion = models.ForeignKey(UbicacionFisica, on_delete=models.RESTRICT, related_name='inventario_stock')
    stock_disponible = models.IntegerField(default=0)   # Listo para vender/usar
    stock_reservado = models.IntegerField(default=0)    # Asignado a OT/ventas en curso
    stock_merma = models.IntegerField(default=0)        # Dañado o en cuarentena
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'inventario_stock'
        verbose_name = 'Inventario Stock'
        verbose_name_plural = 'Inventario Stocks'
        unique_together = [('repuesto', 'ubicacion')]
        # Índice compuesto para consultas multi-filtro frecuentes
        indexes = [
            models.Index(fields=['repuesto', 'ubicacion'], name='idx_stock_repuesto_ubicacion'),
        ]

    def __str__(self):
        return f"{self.repuesto.codigo} @ {self.ubicacion.codigo} | Disp: {self.stock_disponible}"

    @property
    def stock_fisico_total(self):
        return self.stock_disponible + self.stock_reservado + self.stock_merma


class MovimientoInventario(models.Model):
    """
    Kardex de inventario: registro inmutable de CADA movimiento de stock.
    Nunca se edita ni elimina; si hay un error se genera un movimiento de compensación.
    Garantiza trazabilidad 100% de quién, cuándo y por qué cambió el stock.
    """

    class TipoMovimiento(models.TextChoices):
        ENTRADA = 'ENTRADA', 'Entrada'
        SALIDA = 'SALIDA', 'Salida'
        TRASLADO_ENTRADA = 'TRASLADO_ENTRADA', 'Traslado Entrada'
        TRASLADO_SALIDA = 'TRASLADO_SALIDA', 'Traslado Salida'
        AJUSTE_POSITIVO = 'AJUSTE_POSITIVO', 'Ajuste Positivo'
        AJUSTE_NEGATIVO = 'AJUSTE_NEGATIVO', 'Ajuste Negativo'
        RESERVA = 'RESERVA', 'Reserva'
        LIBERACION_RESERVA = 'LIBERACION_RESERVA', 'Liberación de Reserva'
        MERMA = 'MERMA', 'Merma'
        INVENTARIO_INICIAL = 'INVENTARIO_INICIAL', 'Inventario Inicial'

    repuesto = models.ForeignKey(Repuesto, on_delete=models.RESTRICT, related_name='movimientos')
    # db_index=True en ubicacion: se consulta frecuentemente por ubicación para ver el kardex local
    ubicacion = models.ForeignKey(UbicacionFisica, on_delete=models.RESTRICT, related_name='movimientos', db_index=True)
    tipo_movimiento = models.CharField(max_length=30, choices=TipoMovimiento.choices, db_index=True)
    cantidad = models.IntegerField()  # Positivo = entrada, negativo = salida
    stock_resultante = models.IntegerField()  # Stock disponible DESPUÉS del movimiento (snapshot)
    motivo = models.CharField(max_length=255)  # Ej. "Venta #102", "OT #55", "Ajuste de inventario"
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name='movimientos_inventario',
        null=True,  # null para movimientos del sistema (ej. migración inicial)
        blank=True,
    )
    referencia_id = models.PositiveIntegerField(null=True, blank=True)  # ID de la OT/Venta origen
    referencia_tipo = models.CharField(max_length=50, null=True, blank=True)  # 'OT', 'VENTA', etc.
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'movimiento_inventario'
        verbose_name = 'Movimiento de Inventario'
        verbose_name_plural = 'Movimientos de Inventario'
        ordering = ['-fecha']
        # Índice compuesto para el kardex por repuesto y fecha (consulta más común)
        indexes = [
            models.Index(fields=['repuesto', '-fecha'], name='idx_movimiento_repuesto_fecha'),
            models.Index(fields=['ubicacion', '-fecha'], name='idx_movimiento_ubicacion_fecha'),
        ]

    def __str__(self):
        return f"[{self.tipo_movimiento}] {self.repuesto.codigo} | {self.cantidad:+d} → {self.motivo}"
