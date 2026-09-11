import logging
from django.db import models
from django.conf import settings

from apps.clientes.models import Cliente, Transportista
from apps.vehiculos.models import VehiculoTransporte
from apps.seguridad.models import Distrito

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# MODELOS EXISTENTES (con cambios estructurales)
# ──────────────────────────────────────────────

class UnidadMedida(models.Model):
    nombre = models.CharField(max_length=50, unique=True, db_index=True)
    abreviatura = models.CharField(max_length=10)
    permite_decimales = models.BooleanField(default=False)
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'unidad_medida'
        verbose_name = 'Unidad de Medida'
        verbose_name_plural = 'Unidades de Medida'

    def __str__(self):
        return f"{self.nombre} ({self.abreviatura})"


class Categoria(models.Model):
    nombre = models.CharField(max_length=100, unique=True, db_index=True)
    margen_minimo_esperado = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=20.00,
        help_text="Margen mínimo esperado en porcentaje (ej. 20.00 para 20%)"
    )
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
    unidad_medida = models.ForeignKey(UnidadMedida, on_delete=models.RESTRICT, related_name='repuestos', null=True, blank=True)
    viscosidad = models.CharField(max_length=50, null=True, blank=True)
    es_granel = models.BooleanField(default=False)
    
    # Impuesto asignado por defecto a este repuesto (ej. IGV 18%)
    tipo_igv = models.ForeignKey('ventas.Impuesto', on_delete=models.RESTRICT, related_name='repuestos', null=True, blank=True)


    # Campo legacy eliminado.

    # 4 Tipos de Precios (Globales para todas las sucursales)
    precio_compra = models.DecimalField(max_digits=10, decimal_places=2)
    precio_por_mayor = models.DecimalField(max_digits=10, decimal_places=2)
    precio_cash = models.DecimalField(max_digits=10, decimal_places=2)
    precio_lista = models.DecimalField(max_digits=10, decimal_places=2)

    alerta_precio = models.BooleanField(
        default=False,
        help_text="Se marca automáticamente si el margen cae por debajo del mínimo de la categoría al registrar una compra"
    )
    estado = models.BooleanField(default=True)

    class Meta:
        db_table = 'repuesto'
        verbose_name = 'Repuesto'
        verbose_name_plural = 'Repuestos'
        constraints = [
            models.CheckConstraint(condition=models.Q(precio_compra__gte=0), name='repuesto_precio_compra_no_negativo'),
            models.CheckConstraint(condition=models.Q(precio_por_mayor__gte=0), name='repuesto_precio_por_mayor_no_negativo'),
            models.CheckConstraint(condition=models.Q(precio_cash__gte=0), name='repuesto_precio_cash_no_negativo'),
            models.CheckConstraint(condition=models.Q(precio_lista__gte=0), name='repuesto_precio_lista_no_negativo'),
        ]

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

    @property
    def stock_minimo_global(self):
        """Suma del stock mínimo requerido en todas las ubicaciones."""
        return sum(s.stock_minimo for s in self.inventario_stock.all())


class AplicacionRepuesto(models.Model):
    repuesto = models.ForeignKey(Repuesto, on_delete=models.CASCADE, related_name='aplicaciones')
    marca_vehiculo = models.CharField(max_length=100, db_index=True)
    modelo_vehiculo = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    motor = models.CharField(max_length=100, null=True, blank=True)
    anio_desde = models.IntegerField(null=True, blank=True)   # Año inicial de compatibilidad (opcional)
    anio_hasta = models.IntegerField(null=True, blank=True)   # Año final de compatibilidad (opcional)

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
    direccion = models.CharField(max_length=255, null=True, blank=True)
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
    stock_disponible = models.DecimalField(max_digits=12, decimal_places=2, default=0)   # Listo para vender/usar
    stock_reservado = models.DecimalField(max_digits=12, decimal_places=2, default=0)    # Asignado a OT/ventas en curso
    stock_merma = models.DecimalField(max_digits=12, decimal_places=2, default=0)        # Dañado o en cuarentena
    stock_minimo = models.DecimalField(max_digits=12, decimal_places=2, default=5)       # Nivel de alerta para reposición
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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stock_disponible__gte=0), name='inventario_stock_disponible_no_negativo'
            ),
            models.CheckConstraint(
                condition=models.Q(stock_reservado__gte=0), name='inventario_stock_reservado_no_negativo'
            ),
            models.CheckConstraint(
                condition=models.Q(stock_merma__gte=0), name='inventario_stock_merma_no_negativo'
            ),
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
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)  # Positivo = entrada, negativo = salida
    stock_resultante = models.DecimalField(max_digits=12, decimal_places=2)  # Stock disponible DESPUÉS del movimiento (snapshot)
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
        return f"[{self.tipo_movimiento}] {self.repuesto.codigo} | {self.cantidad:+.2f} → {self.motivo}"


class TrasladoInventario(models.Model):
    """
    Cabecera de un traslado de mercadería entre dos almacenes/ubicaciones.
    """
    fecha_traslado = models.DateTimeField(auto_now_add=True)
    almacen_origen = models.ForeignKey(Almacen, on_delete=models.RESTRICT, related_name='traslados_origen')
    almacen_destino = models.ForeignKey(Almacen, on_delete=models.RESTRICT, related_name='traslados_destino')
    observaciones = models.TextField(null=True, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.RESTRICT,
        related_name='traslados_realizados'
    )
    estado = models.CharField(max_length=20, default='COMPLETADO')

    class Meta:
        db_table = 'traslado_inventario'
        verbose_name = 'Traslado de Inventario'
        verbose_name_plural = 'Traslados de Inventario'
        ordering = ['-fecha_traslado']

    def __str__(self):
        return f"Traslado #{self.id} | {self.almacen_origen.nombre} -> {self.almacen_destino.nombre}"


class TrasladoInventarioDetalle(models.Model):
    """
    Detalle de los repuestos trasladados, incluyendo de qué ubicación exacta salieron y a cuál entraron.
    """
    traslado = models.ForeignKey(TrasladoInventario, on_delete=models.CASCADE, related_name='detalles')
    repuesto = models.ForeignKey(Repuesto, on_delete=models.RESTRICT, related_name='traslados_detalle')
    ubicacion_origen = models.ForeignKey(UbicacionFisica, on_delete=models.RESTRICT, related_name='detalles_traslado_origen')
    ubicacion_destino = models.ForeignKey(UbicacionFisica, on_delete=models.RESTRICT, related_name='detalles_traslado_destino')
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'traslado_inventario_detalle'
        verbose_name = 'Detalle de Traslado'
        verbose_name_plural = 'Detalles de Traslado'
        constraints = [
            models.CheckConstraint(condition=models.Q(cantidad__gt=0), name='traslado_detalle_cantidad_positiva'),
        ]

    def __str__(self):
        return f"Detalle Traslado #{self.traslado.id} - {self.repuesto.nombre} ({self.cantidad})"

# ──────────────────────────────────────────────
# MODELOS NUEVOS: GUÍAS DE REMISIÓN
# ──────────────────────────────────────────────

class GuiaRemision(models.Model):
    class Estado(models.TextChoices):
        CREADA = 'CREADA', 'Creada'
        EN_TRASLADO = 'EN_TRASLADO', 'En Traslado'
        COMPLETADA = 'COMPLETADA', 'Completada'

    sucursal = models.ForeignKey('inventario.Sucursal', on_delete=models.RESTRICT, related_name='guias_remision')
    serie = models.ForeignKey('ventas.SerieDocumentoInterno', on_delete=models.RESTRICT, null=True, blank=True)
    correlativo = models.IntegerField(default=0)
    fecha_emision = models.DateTimeField(auto_now_add=True)
    fecha_traslado = models.DateField()
    
    cliente = models.ForeignKey(Cliente, on_delete=models.RESTRICT, null=True, blank=True)
    
    # Origen y Destino
    ubigeo_partida = models.ForeignKey(Distrito, on_delete=models.RESTRICT, related_name='guias_partida')
    punto_partida = models.CharField(max_length=255)
    ubigeo_llegada = models.ForeignKey(Distrito, on_delete=models.RESTRICT, related_name='guias_llegada')
    punto_llegada = models.CharField(max_length=255)
    
    motivo_traslado = models.CharField(max_length=255)
    observaciones = models.TextField(null=True, blank=True)
    
    # Transporte (Se asignan al dar salida)
    transportista = models.ForeignKey(Transportista, on_delete=models.RESTRICT, null=True, blank=True)
    vehiculo = models.ForeignKey(VehiculoTransporte, on_delete=models.RESTRICT, null=True, blank=True)
    
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.CREADA)

    class Meta:
        db_table = 'inventario_guia_remision'
        verbose_name = 'Guía de Remisión'
        verbose_name_plural = 'Guías de Remisión'

    def __str__(self):
        serie_str = self.serie.prefijo if self.serie else "GR"
        return f"{serie_str}-{str(self.correlativo).zfill(6)}"

class GuiaRemisionDetalle(models.Model):
    guia = models.ForeignKey(GuiaRemision, on_delete=models.CASCADE, related_name='detalles')
    repuesto = models.ForeignKey(Repuesto, on_delete=models.RESTRICT)
    cantidad = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'inventario_guia_remision_detalle'
        verbose_name = 'Detalle de Guía de Remisión'
        verbose_name_plural = 'Detalles de Guía de Remisión'
        constraints = [
            models.CheckConstraint(condition=models.Q(cantidad__gt=0), name='guia_remision_detalle_cantidad_positiva'),
        ]

    def __str__(self):
        return f"Detalle {self.id} - {self.repuesto.nombre}"

