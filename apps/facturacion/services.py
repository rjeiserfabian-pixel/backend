import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.inventario.models import GuiaRemision
from apps.seguridad.models import Empresa
from apps.ventas.models import SerieComprobante, TipoComprobante, Venta

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
        items: list, tipo_comprobante_id, usuario,
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
