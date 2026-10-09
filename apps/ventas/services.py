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
    MovimientoCaja, PagoVenta, CuentaPorCobrar, CuotaCredito, Impuesto,
    MetodoPago, PagoCuota, TipoComprobante
)

logger = logging.getLogger(__name__)

class VentasService:
    @staticmethod
    def obtener_tasa_impuesto() -> Decimal:
        """
        Tasa (en %) del impuesto activo a usar para descomponer un total en
        subtotal + impuesto. Antes era un 18% hardcodeado en 3 sitios; ahora
        se lee del catálogo Impuesto (Ventas > Impuestos).
        """
        impuesto = Impuesto.objects.filter(estado=True, nombre__iexact='IGV').first()
        if not impuesto:
            impuesto = Impuesto.objects.filter(estado=True).order_by('id').first()
        if not impuesto:
            logger.warning("No hay ningun Impuesto activo configurado; usando 18%% por defecto.")
            return Decimal('18.00')
        return impuesto.tasa

    @staticmethod
    def descomponer_total_con_impuesto(total: Decimal) -> tuple[Decimal, Decimal]:
        """Dado un total con impuesto incluido, devuelve (subtotal, monto_impuesto)."""
        tasa = VentasService.obtener_tasa_impuesto()
        factor = Decimal('1') + (tasa / Decimal('100'))
        subtotal = (total / factor).quantize(Decimal('0.01'))
        igv = total - subtotal
        return subtotal, igv

    @staticmethod
    def preparar_pagos_para_caja(pagos_data: list, total_venta: Decimal) -> tuple[list, Decimal, Decimal]:
        """
        Normaliza pagos de venta para caja.

        `monto_recibido` conserva lo que entrego el cliente, pero los movimientos
        de caja deben sumar solo el total real de la venta. Si hay vuelto, se
        descuenta del pago en efectivo.
        """
        total_venta = Decimal(str(total_venta)).quantize(Decimal('0.01'))
        pagos_normalizados = []
        total_recibido = Decimal('0.00')

        for pago in pagos_data or []:
            metodo_pago_id = pago.get('metodo_pago_id') or pago.get('metodo_id')
            if not metodo_pago_id:
                raise ValueError("Cada pago debe indicar un metodo de pago.")

            metodo_pago = MetodoPago.objects.filter(id=metodo_pago_id, estado=True).first()
            if not metodo_pago:
                raise ValueError("Uno de los metodos de pago no existe o esta inactivo.")

            try:
                monto = Decimal(str(pago.get('monto') or 0)).quantize(Decimal('0.01'))
            except Exception as exc:
                raise ValueError("El monto de pago no es valido.") from exc

            if monto <= 0:
                raise ValueError("Cada pago debe tener un monto mayor a cero.")

            referencia = (pago.get('referencia') or '').strip()
            if metodo_pago.requiere_referencia and not referencia:
                raise ValueError(f"El metodo de pago {metodo_pago.nombre} requiere referencia.")

            pagos_normalizados.append({
                'metodo_pago': metodo_pago,
                'monto_recibido': monto,
                'monto_caja': monto,
                'referencia': referencia,
            })
            total_recibido += monto

        if not pagos_normalizados:
            raise ValueError("Debe indicar al menos un metodo de pago.")

        if total_recibido < total_venta:
            raise ValueError(f"El monto pagado ({total_recibido}) es menor al total de la venta ({total_venta}).")

        vuelto = (total_recibido - total_venta).quantize(Decimal('0.01'))
        if vuelto > 0:
            vuelto_restante = vuelto
            for pago in pagos_normalizados:
                nombre_metodo = (pago['metodo_pago'].nombre or '').strip().lower()
                es_efectivo = 'efectivo' in nombre_metodo or 'cash' in nombre_metodo
                if not es_efectivo:
                    continue

                descuento = min(pago['monto_caja'], vuelto_restante)
                pago['monto_caja'] -= descuento
                vuelto_restante -= descuento
                if vuelto_restante <= 0:
                    break

            if vuelto_restante > 0:
                raise ValueError("El vuelto solo puede descontarse de pagos en efectivo.")

        pagos_caja = [p for p in pagos_normalizados if p['monto_caja'] > 0]
        if sum(p['monto_caja'] for p in pagos_caja) != total_venta:
            raise ValueError("Los pagos aplicados a caja no cuadran con el total de la venta.")

        return pagos_caja, total_recibido, vuelto

    @staticmethod
    @transaction.atomic
    def generar_ticket_kiosko(cliente, vehiculo, sucursal, detalles_data: list, kilometraje: int = None, kiosko=None) -> Venta:
        """
        Crea una venta en estado PRE_VENTA (Ticket) a partir de la selección del kiosko.
        No descuenta stock ni registra pagos aún.

        `kiosko` (KioskoTerminal), cuando viene informado, es la fuente de verdad
        de la sucursal: sobreescribe el parámetro `sucursal` para que el ticket
        quede atado a la sucursal real del terminal físico, sin depender de un
        dato que pudo venir manipulado desde el navegador del kiosko.
        """
        if kiosko is not None:
            sucursal = kiosko.sucursal

        ticket_code = f"TK-{str(uuid.uuid4())[:6].upper()}"

        venta = Venta.objects.create(
            cliente=cliente,
            vehiculo=vehiculo,
            sucursal=sucursal,
            estado=Venta.Estado.PRE_VENTA,
            ticket_kiosko=ticket_code,
            kilometraje=kilometraje,
            kiosko=kiosko,
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
                costo_unitario=repuesto.precio_compra,
                subtotal_linea=subtotal_linea
            )
            
            subtotal_acumulado += subtotal_linea

        venta.total = subtotal_acumulado
        venta.subtotal, venta.igv = VentasService.descomponer_total_con_impuesto(venta.total)
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
        pagos_caja, monto_recibido, vuelto = VentasService.preparar_pagos_para_caja(pagos_data, venta.total)
        venta.monto_recibido = monto_recibido
        venta.vuelto = vuelto
        venta.save()

        # 3. Registrar Pagos y Movimientos de Caja
        total_pagado = Decimal('0.00')
        for p in pagos_caja:
            monto = p['monto_caja']
            total_pagado += monto
            
            # Movimiento en la caja
            movimiento = MovimientoCaja.objects.create(
                sesion=sesion_caja,
                tipo=MovimientoCaja.Tipo.INGRESO,
                concepto=MovimientoCaja.Concepto.VENTA,
                metodo_pago=p['metodo_pago'],
                monto=monto,
                referencia=p['referencia'],
                origen_movimiento=MovimientoCaja.OrigenMovimiento.VENTA,
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
        # Si la venta viene de una Orden de Trabajo, el stock de sus repuestos ya
        # salió del inventario al aprobarlos (RESERVA) e instalarlos (SALIDA) en
        # el taller — descontar de nuevo aquí duplicaba la salida del mismo
        # repuesto físico. Ver misma corrección en procesar_venta_directa (views.py).
        es_de_orden_trabajo = bool(venta.ticket_kiosko and venta.ticket_kiosko.startswith('OT-'))
        for detalle in venta.detalles.all():
            detalle.almacen_origen = almacen_origen
            detalle.save()

            if detalle.repuesto and not es_de_orden_trabajo:
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
    @transaction.atomic
    def cobrar_venta_directa(data, usuario) -> Venta:
        """
        Cobro del POS y del Registro Manual (POST /api/ventas/transacciones/directa/).

        Crea la venta (o toma un pedido PRE_VENTA existente si viene `venta_id`), toma el siguiente
        correlativo, registra los pagos en la caja abierta del usuario, descuenta el stock y, si la
        venta es al crédito, genera la cuenta por cobrar. Todo ocurre en una sola transacción: ante
        cualquier error (se lanza una excepción con el motivo) no queda NADA guardado.

        Este es el único camino de cobro que usa el sistema. (`procesar_pago_venta` es una variante
        antigua que ninguna pantalla utiliza.)
        """
        from apps.clientes.models import Cliente
        from apps.inventario.models import Almacen, Sucursal

        # Para POS y Registro Manual
        es_registro_manual = data.get('es_registro_manual', False)
        fecha_manual = data.get('fecha_manual')

        cliente = Cliente.objects.get(id=data['cliente_id'])
        sucursal = Sucursal.objects.get(id=data['sucursal_id'])

        # 1. Crear Venta
        fecha_venta = timezone.now()
        if es_registro_manual and fecha_manual:
            from django.utils.dateparse import parse_datetime
            parsed_date = parse_datetime(fecha_manual)
            if parsed_date:
                fecha_venta = parsed_date

        moneda = data.get('moneda', 'PEN')
        tipo_cambio = data.get('tipo_cambio')
        if not tipo_cambio or tipo_cambio == '':
            tipo_cambio = 1.0000
        monto_recibido = data.get('monto_recibido', 0.00)
        vuelto = data.get('vuelto', 0.00)

        venta_id = data.get('venta_id')
        if venta_id:
            venta = Venta.objects.get(id=venta_id)
            # IMPORTANTE: No borramos ni recreamos los detalles porque
            # pueden contener descripciones de servicios del Taller o Kiosko
            # que son de solo lectura en el POS.
            # La sucursal de una venta ya existente (Kiosko/OT) NO se reasigna
            # aquí: quedó fijada en su creación. Antes se sobreescribía con la
            # sucursal activa del cajero, así que un ticket generado en la
            # sucursal A podía terminar cobrado y descontando stock en la
            # sucursal B solo porque el cajero tenía otra sucursal seleccionada
            # en su pantalla (bug real detectado).
            if venta.sucursal_id != sucursal.id:
                raise ValueError(
                    f"Este ticket pertenece a la sucursal '{venta.sucursal.nombre}'. "
                    f"Cambia tu sucursal activa a esa para poder cobrarlo."
                )
            venta.cliente = cliente
            venta.moneda = moneda
            venta.tipo_cambio = tipo_cambio
            venta.monto_recibido = monto_recibido
            venta.vuelto = vuelto
            venta.creado_en = fecha_venta
            venta.save()
        else:
            venta = Venta.objects.create(
                cliente=cliente,
                sucursal=sucursal,
                estado=Venta.Estado.PRE_VENTA,
                ticket_kiosko=f"POS-{str(uuid.uuid4())[:6].upper()}",
                creado_en=fecha_venta,
                moneda=moneda,
                tipo_cambio=tipo_cambio,
                monto_recibido=monto_recibido,
                vuelto=vuelto
            )
        # Detalles (Solo para Venta Directa nueva)
        if not venta_id:
            subtotal_acumulado = Decimal('0.00')
            for item in data.get('detalles', []):
                repuesto = Repuesto.objects.get(id=item['repuesto_id'])
                cantidad = Decimal(str(item['cantidad']))
                precio = Decimal(str(item['precio_venta']))
                sub = cantidad * precio
                subtotal_acumulado += sub
                DetalleVenta.objects.create(
                    venta=venta, repuesto=repuesto, cantidad=cantidad,
                    precio_unitario=precio, costo_unitario=repuesto.precio_compra, subtotal_linea=sub
                )

            venta.total = subtotal_acumulado
            venta.subtotal, venta.igv = VentasService.descomponer_total_con_impuesto(venta.total)
            venta.save()

        # 2. Procesar (Caja, Stock, etc)
        tipo_comprobante = TipoComprobante.objects.get(id=data['tipo_comprobante_id'])

        # Correlativo — select_for_update() evita que dos ventas concurrentes
        # lean el mismo correlativo_actual y generen números duplicados.
        serie_obj = SerieComprobante.objects.select_for_update().filter(id=data['serie_id']).first()
        correlativo = serie_obj.generar_siguiente_correlativo()
        serie_obj.correlativo_actual += 1
        serie_obj.save()

        venta.estado = Venta.Estado.AL_CREDITO if data.get('condicion_pago') == 'CREDITO' else Venta.Estado.PAGADA
        venta.tipo_comprobante = tipo_comprobante
        venta.serie_correlativo = correlativo
        venta.fecha_emision = fecha_venta

        # Movimientos de Caja (saltar si es registro manual)
        sesion = None
        if not es_registro_manual:
            # La sesión se determina por el usuario autenticado (no por un ID
            # enviado desde el cliente): evita depender de un caché de frontend
            # desincronizado y evita que un cliente pueda enviar el ID de una
            # sesión ajena.
            sesion = SesionCaja.objects.filter(usuario=usuario, estado=SesionCaja.Estado.ABIERTA).first()
            if not sesion:
                raise ValueError("Sesión de caja abierta requerida para venta normal.")
            venta.sesion_caja = sesion

        pagos_caja = []
        if not es_registro_manual and sesion and data.get('pagos') and venta.estado != Venta.Estado.AL_CREDITO:
            pagos_caja, monto_recibido_calculado, vuelto_calculado = VentasService.preparar_pagos_para_caja(
                data['pagos'],
                venta.total
            )

            if vuelto_calculado > 0:
                venta.monto_recibido = monto_recibido_calculado
                venta.vuelto = vuelto_calculado

        venta.save()

        # Registrar pagos (ignorar si es venta al crédito, ya que los pagos se harán por cuotas)
        for p in pagos_caja:
            movimiento = MovimientoCaja.objects.create(
                sesion=sesion,
                tipo=MovimientoCaja.Tipo.INGRESO,
                concepto=MovimientoCaja.Concepto.VENTA,
                metodo_pago=p['metodo_pago'],
                monto=p['monto_caja'],
                referencia=p['referencia'],
                origen_movimiento=MovimientoCaja.OrigenMovimiento.VENTA,
                venta_origen=venta,
                creado_por=usuario
            )
            PagoVenta.objects.create(
                venta=venta,
                movimiento_caja=movimiento,
                monto=p['monto_caja']
            )

        # 2. Procesar (Stock, etc)
        almacen_origen = None
        almacen_origen_id = data.get('almacen_origen_id')

        if almacen_origen_id:
            almacen_origen = Almacen.objects.filter(id=almacen_origen_id, sucursal=sucursal).first()

        if not almacen_origen:
            if sesion and sesion.caja.almacen_defecto and sesion.caja.almacen_defecto.sucursal_id == sucursal.id:
                almacen_origen = sesion.caja.almacen_defecto
            else:
                almacen_origen = sucursal.almacenes.first()

        if not almacen_origen:
            raise ValueError("La sucursal no tiene almacenes configurados.")

        # 3. Descontar stock (solo para repuestos físicos, no servicios)
        # Si la venta viene de una Orden de Trabajo, sus repuestos ya salieron
        # del inventario al aprobarlos (RESERVA) e instalarlos (SALIDA) en el
        # taller — descontar de nuevo aquí duplicaba/triplicaba la salida del
        # mismo repuesto físico (bug real detectado: Reserva + Instalación +
        # Venta restaban 3 veces la misma unidad).
        es_de_orden_trabajo = bool(venta.ticket_kiosko and venta.ticket_kiosko.startswith('OT-'))
        if not es_de_orden_trabajo:
            for det in venta.detalles.all():
                if not det.repuesto:
                    continue  # Los servicios no tienen stock físico
                VentasService._descontar_stock(
                    repuesto=det.repuesto,
                    almacen=almacen_origen,
                    cantidad=det.cantidad,
                    motivo=f"Venta {venta.serie_correlativo}",
                    usuario=usuario,
                    referencia_id=venta.id
                )

        # 4. Si viene de una Orden de Trabajo, cambiar estado a FACTURADO
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

        # 5. Si la venta es al crédito, generar CuentaPorCobrar
        if venta.estado == Venta.Estado.AL_CREDITO:
            fecha_limite_str = data.get('fecha_limite')
            fecha_limite = None
            if fecha_limite_str:
                try:
                    fecha_limite = datetime.datetime.strptime(fecha_limite_str, '%Y-%m-%d').date()
                except ValueError:
                    pass

            # La fecha de vencimiento debe ser estrictamente posterior a
            # hoy: una venta al crédito que vence el mismo día que se
            # crea queda "atrasada" desde el día siguiente sin que el
            # cliente haya tenido plazo real para pagar.
            if not fecha_limite or fecha_limite <= timezone.localdate():
                raise ValueError("La fecha de vencimiento del crédito debe ser posterior a hoy.")

            CreditoService.generar_credito(
                venta=venta,
                frecuencia=CuentaPorCobrar.Frecuencia.MENSUAL,
                num_cuotas=1,
                fecha_limite=fecha_limite
            )

        try:
            from apps.facturacion.services import FacturacionService
            FacturacionService.preparar_para_venta(venta, usuario)
        except Exception as exc:
            logger.warning("No se pudo preparar comprobante electronico para venta %s: %s", venta.id, exc)

        return venta

    @staticmethod
    @transaction.atomic
    def anular_venta_cobrada(venta_id: int, usuario, motivo: str) -> dict:
        """
        Anula una venta ya cobrada (PAGADA / AL_CREDITO) del POS revirtiendo su
        impacto: devuelve el stock a las mismas ubicaciones de donde salió,
        registra el egreso de caja por lo cobrado (en la sesión abierta del
        usuario que anula) y elimina la cuenta por cobrar si era a crédito.

        No se anula (ValueError con el motivo) cuando:
          - el comprobante electrónico ya fue enviado a SUNAT y no tiene baja
            aceptada (eso va por Baja / Nota de Crédito en Facturación);
          - la venta viene de una Orden de Trabajo o de una Guía de Remisión
            (su stock/estado se maneja en su propio módulo);
          - el crédito ya tiene cuotas cobradas.
        """
        from apps.facturacion.models import ComprobanteElectronico

        motivo = (motivo or '').strip()
        if not motivo:
            raise ValueError("Debe indicar el motivo de la anulación.")

        venta = Venta.objects.select_for_update().get(pk=venta_id)
        if venta.estado not in (Venta.Estado.PAGADA, Venta.Estado.AL_CREDITO):
            raise ValueError("Solo se pueden anular ventas cobradas (pagadas o al crédito).")

        if (venta.ticket_kiosko or '').startswith('OT-'):
            raise ValueError(
                "Esta venta proviene de una Orden de Trabajo. Corrige el cobro desde el módulo de Taller."
            )
        if getattr(venta, 'guia_remision_origen', None):
            raise ValueError("Esta venta proviene de una Guía de Remisión y no se puede anular desde el POS.")

        # 1. Comprobante electrónico: solo se puede anular si SUNAT nunca lo recibió
        #    (pendiente sin intentos) o si su baja ya fue aceptada.
        comprobantes_a_eliminar = []
        for comp in ComprobanteElectronico.objects.select_for_update().filter(venta=venta):
            nunca_enviado = (
                comp.estado == ComprobanteElectronico.Estado.PENDIENTE_ENVIO
                and comp.intentos == 0
                and not comp.logs.exists()
            )
            if nunca_enviado:
                comprobantes_a_eliminar.append(comp)
            elif comp.estado != ComprobanteElectronico.Estado.BAJA_ACEPTADA:
                raise ValueError(
                    f"El comprobante electrónico {comp.serie}-{comp.numero} está en estado "
                    f"'{comp.get_estado_display()}'. Primero debe darse de baja (dentro de 7 días) o "
                    "corregirse con una Nota de Crédito desde Facturación."
                )

        # 2. Crédito: no se anula si ya hay cuotas cobradas.
        cuenta = getattr(venta, 'cuenta_por_cobrar', None)
        if cuenta and PagoCuota.objects.filter(cuota__cuenta_cobrar=cuenta).exists():
            raise ValueError(
                "Esta venta al crédito ya tiene cobros de cuotas registrados. Reviértelos antes de anularla."
            )

        # 3. Caja: egreso por cada pago cobrado, en la sesión abierta de quien anula.
        pagos = list(venta.pagos.select_related('movimiento_caja__metodo_pago'))
        devuelto_caja = Decimal('0.00')
        if pagos:
            sesion = SesionCaja.objects.select_related('caja').filter(
                usuario=usuario, estado=SesionCaja.Estado.ABIERTA
            ).first()
            if not sesion:
                raise ValueError("Necesitas una sesión de caja abierta para registrar la devolución del dinero.")
            if sesion.caja.sucursal_id != venta.sucursal_id:
                raise ValueError("Tu caja abierta pertenece a otra sucursal distinta a la de la venta.")
            for pago in pagos:
                original = pago.movimiento_caja
                MovimientoCaja.objects.create(
                    sesion=sesion,
                    tipo=MovimientoCaja.Tipo.EGRESO,
                    concepto=MovimientoCaja.Concepto.DEVOLUCION,
                    metodo_pago=original.metodo_pago,
                    monto=original.monto,
                    referencia=f"Anulación {venta.serie_correlativo}",
                    origen_movimiento=MovimientoCaja.OrigenMovimiento.AJUSTE_MANUAL,
                    referencia_origen=f"ANUL-V{venta.id}",
                    estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO,
                    observacion=f"Anulación de la venta {venta.serie_correlativo}: {motivo}",
                    venta_origen=venta,
                    creado_por=usuario,
                )
                devuelto_caja += original.monto

        # 4. Stock: entrada compensatoria a la misma ubicación de cada salida.
        stock_reingresado = 0
        salidas = MovimientoInventario.objects.select_related('repuesto', 'ubicacion').filter(
            referencia_tipo='VENTA', referencia_id=venta.id,
            tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA,
        )
        for salida in salidas:
            cantidad = -salida.cantidad
            stock, _ = InventarioStock.objects.select_for_update().get_or_create(
                repuesto=salida.repuesto, ubicacion=salida.ubicacion,
                defaults={'stock_disponible': Decimal('0.00')},
            )
            stock.stock_disponible += cantidad
            stock.save(update_fields=['stock_disponible'])
            MovimientoInventario.objects.create(
                repuesto=salida.repuesto,
                ubicacion=salida.ubicacion,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.ENTRADA,
                cantidad=cantidad,
                stock_resultante=stock.stock_disponible,
                motivo=f"Anulación de venta {venta.serie_correlativo}",
                usuario=usuario,
                referencia_id=venta.id,
                referencia_tipo='ANULACION_VENTA',
            )
            stock_reingresado += 1

        # 5. Crédito pendiente y comprobante interno nunca enviado.
        if cuenta:
            cuenta.delete()
        for comp in comprobantes_a_eliminar:
            comp.delete()

        venta.estado = Venta.Estado.ANULADA
        venta.anulado_en = timezone.now()
        venta.anulado_por = usuario
        venta.motivo_anulacion = motivo
        venta.save(update_fields=['estado', 'anulado_en', 'anulado_por', 'motivo_anulacion'])

        logger.info("Venta %s (%s) anulada por %s. Motivo: %s", venta.id, venta.serie_correlativo, usuario, motivo)
        return {
            'stock_reingresado': stock_reingresado,
            'devuelto_caja': str(devuelto_caja),
            'credito_eliminado': bool(cuenta),
        }

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
