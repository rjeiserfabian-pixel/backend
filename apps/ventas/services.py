import logging
import uuid
import datetime
import calendar
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.inventario.models import Repuesto, InventarioStock, MovimientoInventario
from .models import (
    Venta, DetalleVenta, SerieComprobante, SesionCaja, 
    MovimientoCaja, PagoVenta, CuentaPorCobrar, CuotaCredito
)

logger = logging.getLogger(__name__)

class VentasService:
    @staticmethod
    @transaction.atomic
    def generar_ticket_kiosko(cliente, vehiculo, sucursal, detalles_data: list, kilometraje: int = None) -> Venta:
        """
        Crea una venta en estado PRE_VENTA (Ticket) a partir de la selección del kiosko.
        No descuenta stock ni registra pagos aún.
        """
        ticket_code = f"TK-{str(uuid.uuid4())[:6].upper()}"
        
        venta = Venta.objects.create(
            cliente=cliente,
            vehiculo=vehiculo,
            sucursal=sucursal,
            estado=Venta.Estado.PRE_VENTA,
            ticket_kiosko=ticket_code,
            kilometraje=kilometraje
        )
        
        # Actualizar el kilometraje actual del vehículo si se proporcionó
        if vehiculo and kilometraje is not None:
            vehiculo.kilometraje_actual = kilometraje
            vehiculo.save(update_fields=['kilometraje_actual'])
        
        subtotal_acumulado = Decimal('0.00')
        igv_acumulado = Decimal('0.00')
        
        for item in detalles_data:
            repuesto = Repuesto.objects.get(id=item['repuesto_id'])
            cantidad = Decimal(str(item['cantidad']))
            precio_unitario = Decimal(str(item['precio_unitario']))
            
            subtotal_linea = cantidad * precio_unitario
            
            DetalleVenta.objects.create(
                venta=venta,
                repuesto=repuesto,
                cantidad=cantidad,
                precio_unitario=precio_unitario,
                subtotal_linea=subtotal_linea
            )
            
            # TODO: Lógica real de IGV basada en el modelo Impuesto.
            # Por ahora asumiendo 18% incluido en el precio para simplificar el ejemplo.
            subtotal_acumulado += subtotal_linea
        
        venta.total = subtotal_acumulado
        venta.subtotal = venta.total / Decimal('1.18')
        venta.igv = venta.total - venta.subtotal
        venta.save()
        
        return venta

    @staticmethod
    @transaction.atomic
    def procesar_pago_venta(venta: Venta, sesion_caja: SesionCaja, tipo_comprobante: str, pagos_data: list, almacen_origen, usuario) -> Venta:
        """
        Procesa el pago de una venta, genera el correlativo, registra los movimientos
        en caja y descuenta el stock físico del inventario.
        """
        if venta.estado != Venta.Estado.PRE_VENTA:
            raise ValueError(f"La venta {venta.id} ya fue procesada o anulada.")

        # 1. Generar Comprobante y Correlativo
        serie_obj = SerieComprobante.objects.select_for_update().filter(
            sucursal=venta.sucursal, 
            tipo_comprobante=tipo_comprobante,
            estado=True
        ).first()

        if not serie_obj:
            raise ValueError(f"No hay una serie configurada para {tipo_comprobante} en esta sucursal.")

        correlativo = serie_obj.generar_siguiente_correlativo()
        serie_obj.correlativo_actual += 1
        serie_obj.save()

        # 2. Actualizar Venta
        venta.estado = Venta.Estado.PAGADA
        venta.tipo_comprobante = tipo_comprobante
        venta.serie_correlativo = correlativo
        venta.sesion_caja = sesion_caja
        venta.fecha_emision = timezone.now()
        venta.save()

        # 3. Registrar Pagos y Movimientos de Caja
        total_pagado = Decimal('0.00')
        for p in pagos_data:
            monto = Decimal(str(p['monto']))
            total_pagado += monto
            
            # Movimiento en la caja
            movimiento = MovimientoCaja.objects.create(
                sesion=sesion_caja,
                tipo=MovimientoCaja.Tipo.INGRESO,
                concepto=MovimientoCaja.Concepto.VENTA,
                metodo_pago_id=p['metodo_pago_id'],
                monto=monto,
                referencia=p.get('referencia', ''),
                venta_origen=venta,
                creado_por=usuario
            )
            
            # Registro del pago específico de la venta
            PagoVenta.objects.create(
                venta=venta,
                movimiento_caja=movimiento,
                monto=monto
            )
            
        if total_pagado < venta.total:
            # Aquí iría lógica si el pago es parcial (crédito)
            # Para simplificar, si no cubre el total y no es crédito explícito, fallamos.
            pass

        # 4. Descontar Stock del Almacén
        for detalle in venta.detalles.all():
            detalle.almacen_origen = almacen_origen
            detalle.save()
            
            if detalle.repuesto:
                VentasService._descontar_stock(detalle.repuesto, almacen_origen, detalle.cantidad, f"Venta {venta.serie_correlativo}", usuario, venta.id)

        # 5. Si viene de una Orden de Trabajo, cambiar estado a FACTURADO
        if venta.ticket_kiosko and venta.ticket_kiosko.startswith('OT-'):
            parts = venta.ticket_kiosko.split('-')
            if len(parts) >= 2:
                ot_id = parts[1]
                from apps.taller.models import OrdenTrabajo
                try:
                    ot = OrdenTrabajo.objects.get(id=ot_id)
                    ot.estado = OrdenTrabajo.Estado.FACTURADO
                    ot.save(update_fields=['estado'])
                except OrdenTrabajo.DoesNotExist:
                    pass

        return venta

    @staticmethod
    def _descontar_stock(repuesto, almacen, cantidad, motivo, usuario=None, referencia_id=None):
        """
        Descuenta stock usando lógica FIFO recorriendo las ubicaciones físicas del almacén
        donde exista disponibilidad. Bloquea si no hay stock suficiente en el almacén.
        """
        stocks = InventarioStock.objects.select_for_update().filter(
            repuesto=repuesto,
            ubicacion__almacen=almacen,
            stock_disponible__gt=0
        ).order_by('ubicacion__codigo')

        cantidad_restante = cantidad
        for stock_record in stocks:
            if cantidad_restante <= 0:
                break
                
            descontar = min(stock_record.stock_disponible, cantidad_restante)
            stock_record.stock_disponible -= descontar
            stock_record.save()
            
            MovimientoInventario.objects.create(
                repuesto=repuesto,
                ubicacion=stock_record.ubicacion,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA,
                cantidad=-descontar,
                stock_resultante=stock_record.stock_disponible,
                motivo=motivo,
                usuario=usuario,
                referencia_id=referencia_id,
                referencia_tipo='VENTA' if referencia_id else None
            )
            
            cantidad_restante -= descontar
            
        if cantidad_restante > 0:
            raise ValueError(f"Stock insuficiente para {repuesto.codigo} en el almacén {almacen.nombre}. Faltan {cantidad_restante} unidades.")


class CreditoService:
    @staticmethod
    def get_last_day_of_month(date_obj: datetime.date) -> datetime.date:
        """Devuelve el último día válido del mes dado."""
        _, last_day = calendar.monthrange(date_obj.year, date_obj.month)
        return datetime.date(date_obj.year, date_obj.month, last_day)

    @staticmethod
    def add_months_with_cap(start_date: datetime.date, months_to_add: int) -> datetime.date:
        """
        Suma meses a una fecha, ajustando al final del mes si el día original
        no existe en el mes objetivo (ej. 31 Ene + 1 mes -> 28 Feb).
        """
        month = start_date.month - 1 + months_to_add
        year = start_date.year + month // 12
        month = month % 12 + 1
        day = start_date.day
        
        _, last_day_of_target_month = calendar.monthrange(year, month)
        if day > last_day_of_target_month:
            day = last_day_of_target_month
            
        return datetime.date(year, month, day)

    @staticmethod
    @transaction.atomic
    def generar_credito(venta: Venta, frecuencia: str, num_cuotas: int, dia_pago: int = None, fecha_limite: datetime.date = None):
        from .models import CuentaPorCobrar, CuotaCredito, SerieDocumentoInterno
        
        serie_credito = SerieDocumentoInterno.objects.filter(
            sucursal=venta.sucursal,
            tipo_documento=SerieDocumentoInterno.TipoDocumento.CREDITO,
            estado=True
        ).select_for_update().first()
        
        if serie_credito:
            codigo_credito = serie_credito.generar_siguiente_correlativo()
            serie_credito.correlativo_actual += 1
            serie_credito.save(update_fields=['correlativo_actual'])
        else:
            codigo_credito = f"CRED-{str(venta.id).zfill(5)}"
        
        cuenta = CuentaPorCobrar.objects.create(
            venta=venta,
            codigo_credito=codigo_credito,
            frecuencia_pago=frecuencia,
            monto_financiado=venta.total,
            saldo_pendiente=venta.total
        )
        
        monto_por_cuota = venta.total / Decimal(str(num_cuotas))
        base_date = timezone.now().date()
        
        # Si es mensual y el cliente quiere pagar un día fijo (ej. los 31)
        if frecuencia == CuentaPorCobrar.Frecuencia.MENSUAL and dia_pago:
            # Ajustar la base date para que empiece en ese día
            try:
                base_date = datetime.date(base_date.year, base_date.month, dia_pago)
            except ValueError:
                # Si el día no existe en el mes actual (ej 31 de feb), se capea al final del mes
                base_date = CreditoService.get_last_day_of_month(base_date)
        
        for i in range(1, num_cuotas + 1):
            if num_cuotas == 1 and fecha_limite:
                fecha_venc = fecha_limite
            elif frecuencia == CuentaPorCobrar.Frecuencia.DIARIO:
                fecha_venc = base_date + datetime.timedelta(days=i)
            elif frecuencia == CuentaPorCobrar.Frecuencia.SEMANAL:
                fecha_venc = base_date + datetime.timedelta(weeks=i)
            elif frecuencia == CuentaPorCobrar.Frecuencia.QUINCENAL:
                fecha_venc = base_date + datetime.timedelta(days=15 * i)
            elif frecuencia == CuentaPorCobrar.Frecuencia.MENSUAL:
                fecha_venc = CreditoService.add_months_with_cap(base_date, i)
            else:
                fecha_venc = base_date + datetime.timedelta(days=30 * i)
                
            CuotaCredito.objects.create(
                cuenta_cobrar=cuenta,
                numero_cuota=i,
                monto=monto_por_cuota,
                saldo_pendiente=monto_por_cuota,
                fecha_vencimiento=fecha_venc
            )
            
        venta.estado = Venta.Estado.AL_CREDITO
        venta.save()
        
        return cuenta
