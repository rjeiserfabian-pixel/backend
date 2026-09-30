import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.inventario.models import GuiaRemision, InventarioStock, MovimientoInventario, UbicacionFisica
from apps.seguridad.models import Empresa
from apps.ventas.models import (
    CuentaPorCobrar, CuotaCredito, DetalleVenta, MetodoPago, MovimientoCaja,
    SerieComprobante, SesionCaja, TipoComprobante, Venta,
)
from apps.ventas.services import VentasService

from . import payloads
from .client import ApiSunatClient
from .exceptions import ApiSunatError, FacturacionError
from .models import ComprobanteElectronico, ComprobanteElectronicoDetalle, ComprobanteEnvioLog

logger = logging.getLogger(__name__)


class FacturacionService:

    @staticmethod
    def _empresa() -> Empresa:
        empresa = Empresa.objects.first()
        if not empresa:
            raise FacturacionError(
                "No hay una Empresa configurada. Configure los datos de la empresa antes de emitir comprobantes."
            )
        return empresa

    # ------------------------------------------------------------------
    # Preparación: crea el registro PENDIENTE_ENVIO. No envía nada todavía.
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def preparar_para_venta(venta: Venta, usuario) -> ComprobanteElectronico:
        existente = ComprobanteElectronico.objects.filter(venta=venta).first()
        if existente:
            return existente

        if venta.estado not in (Venta.Estado.PAGADA, Venta.Estado.AL_CREDITO):
            raise FacturacionError("Solo se puede preparar el comprobante electrónico de una venta pagada o al crédito.")
        if not venta.tipo_comprobante or not venta.serie_correlativo:
            raise FacturacionError("La venta no tiene tipo de comprobante o correlativo asignado.")
        if not venta.tipo_comprobante.codigo_sunat:
            raise FacturacionError(
                f"El tipo de comprobante '{venta.tipo_comprobante.nombre}' no tiene código SUNAT configurado "
                "(01=Factura, 03=Boleta) en Ventas > Configuración."
            )

        try:
            serie, numero = venta.serie_correlativo.split('-', 1)
        except ValueError:
            raise FacturacionError(f"El correlativo '{venta.serie_correlativo}' no tiene el formato SERIE-NUMERO esperado.")

        return ComprobanteElectronico.objects.create(
            tipo_documento=venta.tipo_comprobante.codigo_sunat,
            serie=serie,
            numero=numero,
            sucursal=venta.sucursal,
            venta=venta,
            cliente_tipo_documento=venta.cliente.tipo_documento,
            cliente_documento=venta.cliente.dni,
            cliente_nombre=f"{venta.cliente.nombres} {venta.cliente.apellidos}".strip(),
            moneda=venta.moneda,
            total=venta.total,
            creado_por=usuario,
        )

    @staticmethod
    @transaction.atomic
    def sincronizar_ventas_pendientes(usuario) -> dict:
        comprobante_existente = ComprobanteElectronico.objects.filter(venta_id=OuterRef('pk'))
        ventas = (
            Venta.objects
            .annotate(tiene_comprobante=Exists(comprobante_existente))
            .select_for_update()
            .select_related('cliente', 'sucursal', 'tipo_comprobante')
            .filter(
                estado__in=[Venta.Estado.PAGADA, Venta.Estado.AL_CREDITO],
                tipo_comprobante__codigo_sunat__in=[
                    ComprobanteElectronico.TipoDocumento.FACTURA,
                    ComprobanteElectronico.TipoDocumento.BOLETA,
                ],
                serie_correlativo__isnull=False,
            )
            .exclude(serie_correlativo='')
            .filter(tiene_comprobante=False)
            .order_by('creado_en')
        )

        creados = []
        errores = []
        for venta in ventas:
            try:
                comprobante = FacturacionService.preparar_para_venta(venta, usuario)
                creados.append(comprobante.id)
            except FacturacionError as exc:
                errores.append({'venta_id': venta.id, 'error': str(exc)})

        return {
            'creados': len(creados),
            'comprobante_ids': creados,
            'errores': errores,
        }

    @staticmethod
    @transaction.atomic
    def preparar_para_guia(guia: GuiaRemision, usuario, tipo_comprobante_id) -> ComprobanteElectronico:
        existente = ComprobanteElectronico.objects.filter(guia_remision=guia).first()
        if existente:
            return existente

        if not guia.transportista_id or not guia.vehiculo_id:
            raise FacturacionError("La guía debe tener transportista y vehículo asignado antes de emitirla ante SUNAT.")

        tipo_comprobante = TipoComprobante.objects.filter(id=tipo_comprobante_id, estado=True).first()
        if not tipo_comprobante or tipo_comprobante.codigo_sunat != ComprobanteElectronico.TipoDocumento.GUIA_REMISION_REMITENTE:
            raise FacturacionError(
                "Debe indicar un Tipo de Comprobante configurado con código SUNAT '09' (Guía de Remisión) "
                "en Ventas > Configuración."
            )

        # select_for_update evita que dos usuarios emitan la misma guía a la
        # vez y terminen con el mismo correlativo (misma protección que ya
        # usa VentasService.procesar_pago_venta para facturas/boletas).
        serie_obj = SerieComprobante.objects.select_for_update().filter(
            sucursal=guia.sucursal, tipo_comprobante=tipo_comprobante, estado=True
        ).first()
        if not serie_obj:
            raise FacturacionError("No hay una serie de Guía de Remisión Electrónica configurada para esta sucursal.")

        correlativo = serie_obj.generar_siguiente_correlativo()
        serie_obj.correlativo_actual += 1
        serie_obj.save(update_fields=['correlativo_actual'])
        serie, numero = correlativo.split('-', 1)

        return ComprobanteElectronico.objects.create(
            tipo_documento=ComprobanteElectronico.TipoDocumento.GUIA_REMISION_REMITENTE,
            serie=serie,
            numero=numero,
            sucursal=guia.sucursal,
            guia_remision=guia,
            cliente_tipo_documento=guia.cliente.tipo_documento if guia.cliente else 'DNI',
            cliente_documento=guia.cliente.dni if guia.cliente else '',
            cliente_nombre=(f"{guia.cliente.nombres} {guia.cliente.apellidos}".strip() if guia.cliente else ''),
            creado_por=usuario,
        )

    @staticmethod
    @transaction.atomic
    def generar_nota(
        comprobante_original: ComprobanteElectronico, tipo_documento: str, motivo_codigo: str,
        items: list, tipo_comprobante_id, usuario, impacto_interno: dict = None,
    ) -> ComprobanteElectronico:
        if comprobante_original.estado != ComprobanteElectronico.Estado.ACEPTADO:
            raise FacturacionError("Solo se puede generar una nota de crédito/débito sobre un comprobante ACEPTADO por SUNAT.")
        if tipo_documento not in (
            ComprobanteElectronico.TipoDocumento.NOTA_CREDITO, ComprobanteElectronico.TipoDocumento.NOTA_DEBITO,
        ):
            raise FacturacionError("Tipo de documento inválido para nota de crédito/débito.")
        if not motivo_codigo:
            raise FacturacionError("Debe indicar el motivo de la nota.")
        if not items:
            raise FacturacionError("Debe indicar al menos un ítem para la nota.")

        tipo_comprobante = TipoComprobante.objects.filter(id=tipo_comprobante_id, estado=True).first()
        if not tipo_comprobante or tipo_comprobante.codigo_sunat != tipo_documento:
            raise FacturacionError(f"Debe indicar un Tipo de Comprobante configurado con código SUNAT '{tipo_documento}'.")

        serie_obj = SerieComprobante.objects.select_for_update().filter(
            sucursal=comprobante_original.sucursal, tipo_comprobante=tipo_comprobante, estado=True
        ).first()
        if not serie_obj:
            raise FacturacionError("No hay una serie configurada para este tipo de nota en la sucursal del comprobante original.")
        if tipo_documento == ComprobanteElectronico.TipoDocumento.NOTA_CREDITO:
            prefijo_requerido = 'F' if comprobante_original.tipo_documento == ComprobanteElectronico.TipoDocumento.FACTURA else 'B'
            if comprobante_original.tipo_documento not in (
                ComprobanteElectronico.TipoDocumento.FACTURA,
                ComprobanteElectronico.TipoDocumento.BOLETA,
            ):
                raise FacturacionError("La nota de crédito solo puede afectar facturas o boletas aceptadas.")
            if not serie_obj.serie.upper().startswith(prefijo_requerido):
                raise FacturacionError(
                    f"La serie de nota de crédito debe iniciar con '{prefijo_requerido}' "
                    f"porque afecta el comprobante {comprobante_original.serie}-{comprobante_original.numero}."
                )

        correlativo = serie_obj.generar_siguiente_correlativo()
        serie_obj.correlativo_actual += 1
        serie_obj.save(update_fields=['correlativo_actual'])
        serie, numero = correlativo.split('-', 1)

        try:
            total = sum(
                (Decimal(str(item['cantidad'])) * Decimal(str(item['precio_base'])) for item in items),
                Decimal('0.00'),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FacturacionError("Cada ítem debe traer 'cantidad' y 'precio_base' numéricos.") from exc

        nota = ComprobanteElectronico.objects.create(
            tipo_documento=tipo_documento,
            serie=serie,
            numero=numero,
            sucursal=comprobante_original.sucursal,
            comprobante_relacionado=comprobante_original,
            motivo_codigo=motivo_codigo,
            cliente_tipo_documento=comprobante_original.cliente_tipo_documento,
            cliente_documento=comprobante_original.cliente_documento,
            cliente_nombre=comprobante_original.cliente_nombre,
            moneda=comprobante_original.moneda,
            total=total,
            datos_adicionales={'impacto_interno': impacto_interno or {}},
            creado_por=usuario,
        )
        detalles = [
            ComprobanteElectronicoDetalle(
                comprobante=nota,
                codigo_producto=item.get('codigo_producto', ''),
                descripcion=item['descripcion'],
                codigo_sunat=item.get('codigo_sunat', ''),
                codigo_unidad=item.get('codigo_unidad', 'NIU'),
                cantidad=item['cantidad'],
                precio_base=item['precio_base'],
                tipo_igv_codigo=item.get('tipo_igv_codigo', '10'),
            )
            for item in items
        ]
        ComprobanteElectronicoDetalle.objects.bulk_create(detalles)
        return nota

    @staticmethod
    def items_sugeridos_nota_credito(comprobante_original: ComprobanteElectronico) -> list:
        if comprobante_original.tipo_documento not in (
            ComprobanteElectronico.TipoDocumento.FACTURA,
            ComprobanteElectronico.TipoDocumento.BOLETA,
        ):
            raise FacturacionError("Solo se pueden sugerir ítems para facturas o boletas.")
        if not comprobante_original.venta_id:
            raise FacturacionError("El comprobante original no tiene una venta asociada.")

        venta = comprobante_original.venta
        tasa_igv_default = VentasService.obtener_tasa_impuesto()
        items = []
        for det in venta.detalles.select_related('repuesto__unidad_medida', 'impuesto_aplicado', 'almacen_origen').all():
            tipo_igv_codigo = (
                det.impuesto_aplicado.codigo_sunat
                if det.impuesto_aplicado and det.impuesto_aplicado.codigo_sunat
                else '10'
            )
            tasa_linea = det.impuesto_aplicado.tasa if det.impuesto_aplicado else tasa_igv_default
            precio_base = payloads._precio_base_neto(det.precio_unitario, tasa_linea, tipo_igv_codigo)

            if det.repuesto:
                codigo_producto = det.repuesto.codigo
                descripcion = det.repuesto.nombre
                codigo_sunat = det.repuesto.codigo_sunat or ''
                codigo_unidad = (
                    det.repuesto.unidad_medida.codigo_sunat if det.repuesto.unidad_medida else None
                ) or 'NIU'
                es_repuesto = True
            else:
                codigo_producto = 'SERV001'
                descripcion = det.descripcion_servicio or 'Servicio'
                codigo_sunat = payloads.SERVICIO_CODIGO_SUNAT_DEFAULT
                codigo_unidad = 'ZZ'
                es_repuesto = False

            items.append({
                'detalle_venta_id': det.id,
                'codigo_producto': codigo_producto,
                'descripcion': descripcion,
                'codigo_sunat': codigo_sunat,
                'codigo_unidad': codigo_unidad,
                'cantidad_vendida': det.cantidad,
                'cantidad': det.cantidad,
                'precio_unitario_con_igv': det.precio_unitario,
                'precio_base': precio_base,
                'tipo_igv_codigo': tipo_igv_codigo,
                'es_repuesto': es_repuesto,
                'almacen_origen_id': det.almacen_origen_id,
                'almacen_origen_nombre': det.almacen_origen.nombre if det.almacen_origen else '',
            })
        return items

    @staticmethod
    def _total_nota_con_impuesto(comprobante: ComprobanteElectronico) -> Decimal:
        tasa_igv = VentasService.obtener_tasa_impuesto()
        total = Decimal('0.00')
        for detalle in comprobante.detalles.all():
            base = Decimal(str(detalle.cantidad)) * Decimal(str(detalle.precio_base))
            if (detalle.tipo_igv_codigo or '10') == '10':
                base = base * (Decimal('1') + (tasa_igv / Decimal('100')))
            total += base
        return total.quantize(Decimal('0.01'))

    @staticmethod
    def _reducir_cuenta_por_cobrar(venta: Venta, monto: Decimal) -> dict:
        cuenta = getattr(venta, 'cuenta_por_cobrar', None)
        if not cuenta or monto <= 0:
            return {'aplicado': False, 'motivo': 'La venta no tiene cuenta por cobrar pendiente.'}

        monto_aplicado = min(monto, cuenta.saldo_pendiente).quantize(Decimal('0.01'))
        restante = monto_aplicado
        cuotas_afectadas = []

        cuotas = (
            cuenta.cuotas.select_for_update()
            .filter(saldo_pendiente__gt=0)
            .order_by('-numero_cuota')
        )
        for cuota in cuotas:
            if restante <= 0:
                break
            descuento = min(cuota.saldo_pendiente, restante).quantize(Decimal('0.01'))
            cuota.saldo_pendiente -= descuento
            if cuota.saldo_pendiente <= 0:
                cuota.saldo_pendiente = Decimal('0.00')
                cuota.estado = CuotaCredito.Estado.PAGADA
                cuota.fecha_pago = timezone.localdate()
            elif cuota.estado == CuotaCredito.Estado.PAGADA:
                cuota.estado = CuotaCredito.Estado.PARCIAL
                cuota.fecha_pago = None
            cuota.save(update_fields=['saldo_pendiente', 'estado', 'fecha_pago'])
            cuotas_afectadas.append({'cuota_id': cuota.id, 'monto': str(descuento)})
            restante -= descuento

        cuenta.saldo_pendiente -= monto_aplicado
        if cuenta.saldo_pendiente <= 0:
            cuenta.saldo_pendiente = Decimal('0.00')
            cuenta.estado = CuentaPorCobrar.Estado.PAGADO
        else:
            cuenta.estado = CuentaPorCobrar.Estado.PENDIENTE
        cuenta.save(update_fields=['saldo_pendiente', 'estado'])

        return {
            'aplicado': True,
            'cuenta_id': cuenta.id,
            'monto': str(monto_aplicado),
            'cuotas': cuotas_afectadas,
        }

    @staticmethod
    def _aplicar_reingreso_stock(comprobante: ComprobanteElectronico, impacto: dict, usuario) -> list:
        if not impacto.get('reingresar_stock'):
            return []

        movimientos = []
        lineas = impacto.get('lineas_stock') or []
        lineas_por_detalle = {
            int(linea.get('detalle_venta_id')): linea
            for linea in lineas
            if linea.get('detalle_venta_id') and linea.get('ubicacion_destino_id')
        }

        for linea in lineas:
            if not linea.get('detalle_venta_id') or not linea.get('ubicacion_destino_id'):
                raise FacturacionError("Para reingresar stock, cada repuesto devuelto debe tener ubicación destino.")

        for linea in lineas_por_detalle.values():
            detalle_venta = DetalleVenta.objects.select_related('repuesto').filter(
                id=linea['detalle_venta_id'],
                venta_id=comprobante.comprobante_relacionado.venta_id,
            ).first()
            if not detalle_venta or not detalle_venta.repuesto_id:
                continue

            cantidad = Decimal(str(linea.get('cantidad') or 0)).quantize(Decimal('0.01'))
            if cantidad <= 0:
                continue

            ubicacion = UbicacionFisica.objects.select_related('almacen').filter(
                id=linea['ubicacion_destino_id']
            ).first()
            if not ubicacion:
                raise FacturacionError("La ubicación destino de reingreso no existe.")

            stock, _ = InventarioStock.objects.select_for_update().get_or_create(
                repuesto=detalle_venta.repuesto,
                ubicacion=ubicacion,
                defaults={'stock_disponible': Decimal('0.00')},
            )
            stock.stock_disponible += cantidad
            stock.save(update_fields=['stock_disponible'])

            movimiento = MovimientoInventario.objects.create(
                repuesto=detalle_venta.repuesto,
                ubicacion=ubicacion,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.ENTRADA,
                cantidad=cantidad,
                stock_resultante=stock.stock_disponible,
                motivo=f"Reingreso por Nota de Crédito {comprobante.serie}-{comprobante.numero}",
                usuario=usuario,
                referencia_id=comprobante.id,
                referencia_tipo='NOTA_CREDITO',
            )
            movimientos.append(movimiento.id)

        return movimientos

    @staticmethod
    def _registrar_devolucion_caja(comprobante: ComprobanteElectronico, impacto: dict, monto: Decimal, usuario) -> dict:
        if not impacto.get('registrar_devolucion_caja'):
            return {'aplicado': False}

        sesion = SesionCaja.objects.filter(
            id=impacto.get('sesion_caja_id'),
            estado=SesionCaja.Estado.ABIERTA,
        ).first()
        if not sesion:
            raise FacturacionError("La sesión de caja para la devolución no existe o no está abierta.")

        metodo = MetodoPago.objects.filter(id=impacto.get('metodo_pago_id'), estado=True).first()
        if not metodo:
            raise FacturacionError("El método de pago de la devolución no existe o está inactivo.")

        movimiento = MovimientoCaja.objects.create(
            sesion=sesion,
            tipo=MovimientoCaja.Tipo.EGRESO,
            concepto=MovimientoCaja.Concepto.DEVOLUCION,
            metodo_pago=metodo,
            monto=monto,
            referencia=impacto.get('referencia_caja') or f"NC {comprobante.serie}-{comprobante.numero}",
            origen_movimiento=MovimientoCaja.OrigenMovimiento.AJUSTE_MANUAL,
            referencia_origen=f"NC-{comprobante.id}",
            estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO,
            observacion=f"Devolución por Nota de Crédito {comprobante.serie}-{comprobante.numero}",
            venta_origen=comprobante.comprobante_relacionado.venta if comprobante.comprobante_relacionado else None,
            creado_por=usuario,
        )
        return {'aplicado': True, 'movimiento_caja_id': movimiento.id, 'monto': str(monto)}

    @staticmethod
    def _impacto_interno_tiene_acciones(comprobante: ComprobanteElectronico, impacto: dict) -> bool:
        if not impacto:
            return False
        if impacto.get('reingresar_stock') or impacto.get('registrar_devolucion_caja'):
            return True
        venta = comprobante.comprobante_relacionado.venta if comprobante.comprobante_relacionado_id else None
        return bool(impacto.get('reducir_cuenta_por_cobrar') or (venta and venta.estado == Venta.Estado.AL_CREDITO))

    @staticmethod
    @transaction.atomic
    def aplicar_impacto_nota_credito(
        comprobante: ComprobanteElectronico, usuario, validar_pendiente: bool = False,
    ) -> ComprobanteElectronico:
        if comprobante.tipo_documento != ComprobanteElectronico.TipoDocumento.NOTA_CREDITO:
            if validar_pendiente:
                raise FacturacionError("Solo se puede aplicar impacto interno a notas de credito.")
            return comprobante
        if comprobante.estado != ComprobanteElectronico.Estado.ACEPTADO:
            if validar_pendiente:
                raise FacturacionError("La nota de credito debe estar aceptada por SUNAT antes de aplicar el impacto interno.")
            return comprobante
        if not comprobante.comprobante_relacionado_id or not comprobante.comprobante_relacionado.venta_id:
            if validar_pendiente:
                raise FacturacionError("La nota de credito no tiene una venta relacionada para aplicar impacto interno.")
            return comprobante

        datos = comprobante.datos_adicionales or {}
        impacto = datos.get('impacto_interno') or {}
        if impacto.get('aplicado_en'):
            if validar_pendiente:
                raise FacturacionError("El impacto interno de esta nota de credito ya fue aplicado.")
            return comprobante
        if not FacturacionService._impacto_interno_tiene_acciones(comprobante, impacto):
            if validar_pendiente:
                raise FacturacionError("Esta nota de credito no tiene impacto interno pendiente para aplicar.")
            return comprobante

        monto_con_impuesto = FacturacionService._total_nota_con_impuesto(comprobante)
        resultado = {}
        resultado['stock_movimiento_ids'] = FacturacionService._aplicar_reingreso_stock(comprobante, impacto, usuario)
        resultado['caja'] = FacturacionService._registrar_devolucion_caja(comprobante, impacto, monto_con_impuesto, usuario)

        venta = comprobante.comprobante_relacionado.venta
        if impacto.get('reducir_cuenta_por_cobrar') or venta.estado == Venta.Estado.AL_CREDITO:
            resultado['cuenta_por_cobrar'] = FacturacionService._reducir_cuenta_por_cobrar(venta, monto_con_impuesto)

        impacto['aplicado_en'] = timezone.now().isoformat()
        impacto['monto_con_impuesto'] = str(monto_con_impuesto)
        impacto['resultado'] = resultado
        impacto.pop('error_aplicacion', None)
        impacto.pop('error_aplicacion_en', None)
        datos['impacto_interno'] = impacto
        comprobante.datos_adicionales = datos
        update_fields = ['datos_adicionales']
        if comprobante.mensaje_error and 'Impacto interno pendiente:' in comprobante.mensaje_error:
            comprobante.mensaje_error = '\n'.join(
                linea for linea in comprobante.mensaje_error.splitlines()
                if not linea.startswith('Impacto interno pendiente:')
            )
            update_fields.append('mensaje_error')
        comprobante.save(update_fields=update_fields)
        return comprobante

    # ------------------------------------------------------------------
    # Envío manual — nunca disparado por una señal/hook automático.
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def emitir(comprobante: ComprobanteElectronico, usuario, correcciones: dict = None) -> ComprobanteElectronico:
        """
        Envía (o reenvía) un comprobante a la API SUNAT. `correcciones`
        permite sobreescribir campos puntuales del payload antes de reenviar
        uno RECHAZADO/ERROR_CONEXION (ej. un documento de cliente mal
        tipeado) sin tener que editar los datos maestros de la venta/cliente.
        """
        if comprobante.estado in (ComprobanteElectronico.Estado.ACEPTADO, ComprobanteElectronico.Estado.BAJA_ACEPTADA):
            raise FacturacionError("Este comprobante ya fue aceptado/anulado por SUNAT y no se puede reenviar.")

        empresa = FacturacionService._empresa()
        payload = payloads.construir_payload(comprobante, empresa)
        if correcciones:
            payloads.aplicar_correcciones(payload, correcciones)

        comprobante.payload_enviado = payload
        comprobante.estado = ComprobanteElectronico.Estado.ENVIADO
        comprobante.intentos += 1
        comprobante.enviado_por = usuario
        comprobante.fecha_envio = timezone.now()
        comprobante.save(update_fields=['payload_enviado', 'estado', 'intentos', 'enviado_por', 'fecha_envio'])

        endpoint = 'post.php'
        client = ApiSunatClient()
        try:
            if comprobante.tipo_documento == ComprobanteElectronico.TipoDocumento.GUIA_REMISION_REMITENTE:
                respuesta = client.enviar_guia_remision(payload)
            else:
                respuesta = client.enviar_comprobante(payload)
        except ApiSunatError as exc:
            comprobante.estado = ComprobanteElectronico.Estado.ERROR_CONEXION
            comprobante.mensaje_error = str(exc)
            comprobante.fecha_respuesta = timezone.now()
            comprobante.save(update_fields=['estado', 'mensaje_error', 'fecha_respuesta'])
            ComprobanteEnvioLog.objects.create(
                comprobante=comprobante, endpoint=endpoint, payload_enviado=payload,
                respuesta_api=None, exitoso=False, mensaje=str(exc), usuario=usuario,
            )
            logger.warning("Error de conexión emitiendo comprobante %s: %s", comprobante.id, exc)
            return comprobante

        estado, mensaje, extra = payloads.interpretar_respuesta(respuesta)
        comprobante.estado = estado
        comprobante.mensaje_error = mensaje
        comprobante.respuesta_api = respuesta
        comprobante.fecha_respuesta = timezone.now()
        for campo in ('pdf_url', 'xml_url', 'cdr_url', 'hash_cdr'):
            if extra.get(campo):
                setattr(comprobante, campo, extra[campo])
        comprobante.save()

        ComprobanteEnvioLog.objects.create(
            comprobante=comprobante, endpoint=endpoint, payload_enviado=payload,
            respuesta_api=respuesta, exitoso=(estado == ComprobanteElectronico.Estado.ACEPTADO),
            mensaje=mensaje, usuario=usuario,
        )
        if estado == ComprobanteElectronico.Estado.ACEPTADO:
            try:
                comprobante = FacturacionService.aplicar_impacto_nota_credito(comprobante, usuario)
            except FacturacionError as exc:
                datos = comprobante.datos_adicionales or {}
                impacto = datos.get('impacto_interno') or {}
                impacto['error_aplicacion'] = str(exc)
                impacto['error_aplicacion_en'] = timezone.now().isoformat()
                datos['impacto_interno'] = impacto
                comprobante.datos_adicionales = datos
                comprobante.mensaje_error = (
                    f"{comprobante.mensaje_error}\nImpacto interno pendiente: {exc}"
                    if comprobante.mensaje_error else f"Impacto interno pendiente: {exc}"
                )
                comprobante.save(update_fields=['datos_adicionales', 'mensaje_error'])
        logger.info("Comprobante %s emitido, estado resultante: %s", comprobante.id, estado)
        return comprobante

    @staticmethod
    @transaction.atomic
    def solicitar_baja(comprobante: ComprobanteElectronico, motivo: str, usuario) -> ComprobanteElectronico:
        if comprobante.estado != ComprobanteElectronico.Estado.ACEPTADO:
            raise FacturacionError("Solo se puede solicitar la baja de un comprobante ACEPTADO por SUNAT.")
        if not motivo:
            raise FacturacionError("Debe indicar el motivo de la anulación.")
        dias_transcurridos = (timezone.now() - comprobante.fecha_respuesta).days if comprobante.fecha_respuesta else 999
        if dias_transcurridos >= 7:
            raise FacturacionError("La Comunicación de Baja solo aplica dentro de los 7 días de emitido el comprobante.")

        empresa = FacturacionService._empresa()
        payload = payloads.construir_payload_baja(comprobante, empresa, motivo)

        client = ApiSunatClient()
        try:
            respuesta = client.solicitar_baja(payload)
        except ApiSunatError as exc:
            ComprobanteEnvioLog.objects.create(
                comprobante=comprobante, endpoint='baja.php', payload_enviado=payload,
                respuesta_api=None, exitoso=False, mensaje=str(exc), usuario=usuario,
            )
            raise FacturacionError(str(exc)) from exc

        comprobante.estado = ComprobanteElectronico.Estado.BAJA_SOLICITADA
        comprobante.ticket_sunat = str(respuesta.get('ticket') or respuesta.get('numTicket') or '') if isinstance(respuesta, dict) else ''
        comprobante.respuesta_api = respuesta
        comprobante.save(update_fields=['estado', 'ticket_sunat', 'respuesta_api'])

        ComprobanteEnvioLog.objects.create(
            comprobante=comprobante, endpoint='baja.php', payload_enviado=payload,
            respuesta_api=respuesta, exitoso=True, mensaje='Baja solicitada, pendiente de ticket.', usuario=usuario,
        )
        return comprobante

    @staticmethod
    @transaction.atomic
    def consultar_ticket_baja(comprobante: ComprobanteElectronico, usuario) -> ComprobanteElectronico:
        if comprobante.estado != ComprobanteElectronico.Estado.BAJA_SOLICITADA:
            raise FacturacionError("Este comprobante no tiene una baja pendiente de consultar.")
        if not comprobante.ticket_sunat:
            raise FacturacionError("El comprobante no tiene un ticket de baja registrado.")

        empresa = FacturacionService._empresa()
        payload = payloads.construir_payload_ticket(comprobante, empresa)

        client = ApiSunatClient()
        try:
            respuesta = client.consultar_ticket(payload)
        except ApiSunatError as exc:
            raise FacturacionError(str(exc)) from exc

        estado, mensaje, _ = payloads.interpretar_respuesta_ticket(respuesta)
        comprobante.estado = estado
        comprobante.mensaje_error = mensaje
        comprobante.respuesta_api = respuesta
        comprobante.save(update_fields=['estado', 'mensaje_error', 'respuesta_api'])

        ComprobanteEnvioLog.objects.create(
            comprobante=comprobante, endpoint='resumen_ticket.php', payload_enviado=payload,
            respuesta_api=respuesta, exitoso=(estado == ComprobanteElectronico.Estado.BAJA_ACEPTADA),
            mensaje=mensaje, usuario=usuario,
        )
        return comprobante
