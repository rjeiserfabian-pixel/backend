"""
Construcción de los payloads JSON que espera la API SUNAT del taller
(ver 'API_SUNAT_Mejorado.pdf') a partir de los modelos internos, e
interpretación (best-effort) de sus respuestas.

Nota importante sobre interpretar_respuesta(): la documentación de la API
solo especifica los payloads de ENTRADA, no la forma exacta de la respuesta.
La heurística de abajo cubre las claves más comunes en wrappers de este
tipo; ajústala en cuanto tengas un ejemplo real de respuesta de post.php.
Mientras tanto, el JSON crudo siempre se guarda completo en
ComprobanteElectronico.respuesta_api, así que nada se pierde aunque la
heurística no reconozca la forma exacta.
"""
import logging
from decimal import Decimal

from django.utils import timezone

from apps.seguridad.models import Empresa
from apps.ventas.models import Venta
from apps.ventas.services import VentasService

from .exceptions import FacturacionError
from .models import ComprobanteElectronico

logger = logging.getLogger(__name__)

MONEDA_A_ID = {'PEN': 1, 'USD': 2, 'EUR': 3}
# Código UNSPSC genérico de "servicios de reparación y mantenimiento", usado
# solo cuando la línea de venta es mano de obra (sin repuesto asociado).
SERVICIO_CODIGO_SUNAT_DEFAULT = '81112500'


def _empresa_block(empresa: Empresa, incluir_gre: bool = False) -> dict:
    block = {
        'ruc': empresa.ruc,
        'razon_social': empresa.razon_social,
        'nombre_comercial': empresa.nombre_comercial or empresa.razon_social,
        'domicilio_fiscal': empresa.direccion,
        'ubigeo': empresa.ubigeo or '',
        'departamento': empresa.departamento or '',
        'provincia': empresa.provincia or '',
        'distrito': empresa.distrito or '',
        'modo': 1 if empresa.sunat_modo == Empresa.ModoSunat.PRODUCCION else 0,
        'usu_secundario_produccion_user': empresa.sunat_usuario_secundario or '',
        'usu_secundario_produccion_password': empresa.sunat_clave_secundaria or '',
        'cuenta_detraccion': '',
    }
    if empresa.urbanizacion:
        block['urbanizacion'] = empresa.urbanizacion
    if incluir_gre:
        block['gre_client_id'] = empresa.sunat_gre_client_id or ''
        block['gre_client_secret'] = empresa.sunat_gre_client_secret or ''
    return block


def _validar_empresa(empresa: Empresa) -> None:
    faltantes = []
    if not empresa.ubigeo:
        faltantes.append('ubigeo')
    if not empresa.sunat_usuario_secundario or not empresa.sunat_clave_secundaria:
        faltantes.append('credenciales SOL (usuario/clave secundaria)')
    if faltantes:
        raise FacturacionError(
            f"Complete la configuración de la Empresa antes de emitir: {', '.join(faltantes)}."
        )


def _cliente_block(cliente) -> dict:
    return {
        'codigo_tipo_entidad': '6' if cliente.tipo_documento == 'RUC' else '1',
        'numero_documento': cliente.dni,
        'razon_social_nombres': f"{cliente.nombres} {cliente.apellidos}".strip(),
        'cliente_direccion': cliente.direccion or '',
    }


def _precio_base_neto(precio_unitario: Decimal, tasa_igv: Decimal, tipo_igv_codigo: str) -> Decimal:
    """
    DetalleVenta.precio_unitario es el precio CON IGV incluido (precio de
    venta al público, como se maneja en todo el resto del sistema). La API
    SUNAT espera "precio_base" SIN IGV (según el ejemplo del PDF: item
    precio_base=50.00 + total_igv=9.00 sobre una base de 50, no de 59).
    Por eso se descuenta el IGV acá antes de armar el ítem.
    """
    if tipo_igv_codigo != '10':
        return precio_unitario
    factor = Decimal('1') + (Decimal(tasa_igv) / Decimal('100'))
    return (precio_unitario / factor).quantize(Decimal('0.01'))


def _construir_items_venta(venta: Venta) -> list:
    items = []
    tasa_igv_default = VentasService.obtener_tasa_impuesto()
    # select_related evita N+1 al leer repuesto/unidad_medida/impuesto por cada línea.
    detalles = venta.detalles.select_related('repuesto__unidad_medida', 'impuesto_aplicado').all()

    for det in detalles:
        tipo_igv_codigo = (
            det.impuesto_aplicado.codigo_sunat
            if det.impuesto_aplicado and det.impuesto_aplicado.codigo_sunat
            else '10'
        )
        tasa_linea = det.impuesto_aplicado.tasa if det.impuesto_aplicado else tasa_igv_default
        precio_base = _precio_base_neto(det.precio_unitario, tasa_linea, tipo_igv_codigo)

        if det.repuesto:
            codigo_producto = det.repuesto.codigo
            producto = det.repuesto.nombre
            codigo_sunat_item = det.repuesto.codigo_sunat or ''
            codigo_unidad = (
                det.repuesto.unidad_medida.codigo_sunat if det.repuesto.unidad_medida else None
            ) or 'NIU'
        else:
            codigo_producto = 'SERV001'
            producto = det.descripcion_servicio or 'Servicio'
            codigo_sunat_item = SERVICIO_CODIGO_SUNAT_DEFAULT
            codigo_unidad = 'ZZ'

        if not codigo_sunat_item:
            raise FacturacionError(
                f"El repuesto '{codigo_producto}' no tiene código SUNAT (clasificación UNSPSC) configurado. "
                "Complételo en Inventario > Repuestos antes de emitir."
            )

        items.append({
            'codigo_producto': codigo_producto,
            'producto': producto,
            'codigo_sunat': codigo_sunat_item,
            'cantidad': float(det.cantidad),
            'codigo_unidad': codigo_unidad,
            'precio_base': float(precio_base),
            'tipo_igv_codigo': tipo_igv_codigo,
            'descuento_precio_base': float(det.descuento or 0),
            'bolsa': False,
        })

    if not items:
        raise FacturacionError("La venta no tiene ítems para emitir.")
    return items


def _construir_payload_venta(comprobante: ComprobanteElectronico, empresa: Empresa) -> dict:
    _validar_empresa(empresa)
    venta = comprobante.venta
    if not venta:
        raise FacturacionError("Este comprobante no tiene una venta asociada.")

    items = _construir_items_venta(venta)
    fecha_emision = timezone.localtime(venta.fecha_emision or venta.creado_en)
    forma_pago_id = 2 if venta.estado == Venta.Estado.AL_CREDITO else 1

    return {
        'empresa': _empresa_block(empresa),
        'cliente': _cliente_block(venta.cliente),
        'venta': {
            'tipo_documento_codigo': comprobante.tipo_documento,
            'serie': comprobante.serie,
            'numero': comprobante.numero,
            'fecha_emision': fecha_emision.date().isoformat(),
            'hora_emision': fecha_emision.strftime('%H:%M:%S'),
            'fecha_vencimiento': None,
            'moneda_id': MONEDA_A_ID.get(venta.moneda, 1),
            'forma_pago_id': forma_pago_id,
            # Simplificación: el sistema hoy solo trackea un subtotal/igv
            # agregado por venta (no desglosado por línea gravada/exonerada/
            # inafecta), así que total_gravada/total_igv se toman tal cual.
            # Si en el futuro hay ventas con líneas mixtas (exoneradas +
            # gravadas), esto habría que desglosarlo por tipo_igv_codigo.
            'total_gravada': float(venta.subtotal),
            'total_igv': float(venta.igv),
            'total_exonerada': None,
            'total_inafecta': None,
            'total_gratuita': None,
            'total_gratuita_igv': None,
            'total_bolsa': None,
            'orden_compra': '',
            'nota': '',
            'detraccion_codigo': '',
            'detraccion_porcentaje': None,
            'percepcion_codigo': '',
            'percepcion_porcentaje': None,
            'retencion_porcentaje': None,
        },
        'items': items,
        'cuotas': [],
        'guias_adjuntas': [],
        'anticipos': [],
    }


def _construir_payload_nota(comprobante: ComprobanteElectronico, empresa: Empresa) -> dict:
    _validar_empresa(empresa)
    original = comprobante.comprobante_relacionado
    if not original:
        raise FacturacionError("Esta nota no tiene un comprobante relacionado.")

    detalles = list(comprobante.detalles.all())
    if not detalles:
        raise FacturacionError("La nota no tiene ítems.")

    items = []
    for det in detalles:
        if not det.codigo_sunat:
            raise FacturacionError(f"El ítem '{det.descripcion}' no tiene código SUNAT configurado.")
        items.append({
            'codigo_producto': det.codigo_producto or 'ITEM001',
            'producto': det.descripcion,
            'codigo_sunat': det.codigo_sunat,
            'cantidad': float(det.cantidad),
            'codigo_unidad': det.codigo_unidad or 'NIU',
            'precio_base': float(det.precio_base),
            'tipo_igv_codigo': det.tipo_igv_codigo or '10',
        })

    total_gravada = sum(
        (Decimal(str(i['cantidad'])) * Decimal(str(i['precio_base'])) for i in items if i['tipo_igv_codigo'] == '10'),
        Decimal('0.00'),
    )
    tasa_igv = VentasService.obtener_tasa_impuesto()
    total_igv = (total_gravada * tasa_igv / Decimal('100')).quantize(Decimal('0.01'))

    ahora = timezone.localtime()
    return {
        'empresa': _empresa_block(empresa),
        'cliente': {
            'codigo_tipo_entidad': comprobante.cliente_tipo_documento_codigo(),
            'numero_documento': comprobante.cliente_documento,
            'razon_social_nombres': comprobante.cliente_nombre,
            'cliente_direccion': '',
        },
        'venta': {
            'tipo_documento_codigo': comprobante.tipo_documento,
            'serie': comprobante.serie,
            'numero': comprobante.numero,
            'fecha_emision': ahora.date().isoformat(),
            'hora_emision': ahora.strftime('%H:%M:%S'),
            'moneda_id': MONEDA_A_ID.get(comprobante.moneda, 1),
            'forma_pago_id': 1,
            'total_gravada': float(total_gravada),
            'total_igv': float(total_igv),
            'total_exonerada': None,
            'total_inafecta': None,
            'relacionado_serie': original.serie,
            'relacionado_numero': original.numero,
            'relacionado_tipo_documento': original.tipo_documento,
            'relacionado_motivo_codigo': comprobante.motivo_codigo,
        },
        'items': items,
        'cuotas': [],
        'guias_adjuntas': [],
        'anticipos': [],
    }


def _construir_payload_guia(comprobante: ComprobanteElectronico, empresa: Empresa) -> dict:
    _validar_empresa(empresa)
    guia = comprobante.guia_remision
    if not guia:
        raise FacturacionError("Este comprobante no tiene una guía de remisión asociada.")
    if not empresa.sunat_gre_client_id or not empresa.sunat_gre_client_secret:
        raise FacturacionError("Configure gre_client_id/gre_client_secret de la Empresa antes de emitir guías.")

    extra = comprobante.datos_adicionales or {}
    ubigeo_partida = extra.get('ubigeo_partida', '')
    ubigeo_llegada = extra.get('ubigeo_llegada', '')
    peso_total = extra.get('peso_total')
    if not ubigeo_partida or not ubigeo_llegada:
        raise FacturacionError(
            "Debe indicar ubigeo_partida y ubigeo_llegada para emitir la guía "
            "(el catálogo de distritos aún no guarda el código ubigeo INEI)."
        )
    if not peso_total:
        raise FacturacionError("Debe indicar el peso_total del traslado para emitir la guía.")

    # Caso soportado en esta primera versión: transporte privado, donde quien
    # traslada es la misma persona/vehículo del taller (transportista con DNI
    # propio). El caso de transportista tercero (RUC) con un conductor
    # distinto necesitaría un campo "conductor" propio en GuiaRemision, que
    # hoy no existe — queda como mejora futura si se necesita.
    conductor_es_transportista = bool(guia.transportista and guia.transportista.tipo_documento == 'DNI')

    items = []
    for det in guia.detalles.select_related('repuesto__unidad_medida').all():
        if not det.repuesto.codigo_sunat:
            raise FacturacionError(f"El repuesto '{det.repuesto.codigo}' no tiene código SUNAT configurado.")
        items.append({
            'codigo_producto': det.repuesto.codigo,
            'producto': det.repuesto.nombre,
            'codigo_sunat': det.repuesto.codigo_sunat,
            'cantidad': float(det.cantidad),
            'codigo_unidad': (det.repuesto.unidad_medida.codigo_sunat if det.repuesto.unidad_medida else None) or 'NIU',
        })
    if not items:
        raise FacturacionError("La guía no tiene ítems.")

    documentos_relacionados = []
    if guia.venta_generada_id and guia.venta_generada.tipo_comprobante_id and guia.venta_generada.tipo_comprobante.codigo_sunat:
        serie_venta, numero_venta = guia.venta_generada.serie_correlativo.split('-', 1)
        documentos_relacionados.append({
            'tipo_documento': guia.venta_generada.tipo_comprobante.codigo_sunat,
            'serie': serie_venta,
            'numero': numero_venta,
            'ruc_emisor': empresa.ruc,
        })

    fecha_traslado = extra.get('fecha_traslado') or guia.fecha_traslado.isoformat()

    return {
        'empresa': _empresa_block(empresa, incluir_gre=True),
        'remitente': {
            'codigo_tipo_entidad': '6',
            'numero_documento': empresa.ruc,
            'razon_social_nombres': empresa.razon_social,
        },
        'destinatario': {
            'codigo_tipo_entidad': (
                '6' if guia.cliente and guia.cliente.tipo_documento == 'RUC' else '1'
            ),
            'numero_documento': guia.cliente.dni if guia.cliente else '',
            'razon_social_nombres': (
                f"{guia.cliente.nombres} {guia.cliente.apellidos}".strip() if guia.cliente else ''
            ),
        },
        'guia': {
            'tipo_documento_codigo': '09',
            'serie': comprobante.serie,
            'numero': comprobante.numero,
            'fecha_emision': timezone.localdate().isoformat(),
            'hora_emision': timezone.localtime().strftime('%H:%M:%S'),
            'fecha_entrega_bienes_transportista': fecha_traslado,
            'fecha_inicio_traslado': fecha_traslado,
            'motivo_traslado_codigo': extra.get('motivo_traslado_codigo') or comprobante.motivo_codigo or '01',
            'peso_total': float(peso_total),
            'unidad_peso': extra.get('unidad_peso', 'KGM'),
            'cantidad_bultos': int(extra.get('cantidad_bultos') or 1),
            'direccion_partida': guia.punto_partida,
            'ubigeo_partida': ubigeo_partida,
            'direccion_llegada': guia.punto_llegada,
            'ubigeo_llegada': ubigeo_llegada,
            'transporte_publico': not conductor_es_transportista,
            'observacion': guia.observaciones or '',
        },
        'transportista': (
            {}
            if conductor_es_transportista or not guia.transportista
            else {
                'numero_documento': guia.transportista.numero_documento,
                'razon_social_nombres': guia.transportista.nombre_o_razon_social,
            }
        ),
        'vehiculos': [{'placa': guia.vehiculo.placa, 'principal': True}] if guia.vehiculo else [],
        'conductores': (
            [{
                'tipo_documento': '1',
                'numero_documento': guia.transportista.numero_documento,
                'nombres': guia.transportista.nombre_o_razon_social,
                'apellidos': '',
                'licencia': guia.transportista.licencia_conducir or '',
                'principal': True,
            }]
            if conductor_es_transportista else []
        ),
        'items': items,
        'documentos_relacionados': documentos_relacionados,
        'evento': {},
    }


def construir_payload(comprobante: ComprobanteElectronico, empresa: Empresa) -> dict:
    if comprobante.tipo_documento in (
        ComprobanteElectronico.TipoDocumento.FACTURA, ComprobanteElectronico.TipoDocumento.BOLETA,
    ):
        return _construir_payload_venta(comprobante, empresa)
    if comprobante.tipo_documento in (
        ComprobanteElectronico.TipoDocumento.NOTA_CREDITO, ComprobanteElectronico.TipoDocumento.NOTA_DEBITO,
    ):
        return _construir_payload_nota(comprobante, empresa)
    if comprobante.tipo_documento == ComprobanteElectronico.TipoDocumento.GUIA_REMISION_REMITENTE:
        return _construir_payload_guia(comprobante, empresa)
    raise FacturacionError(f"No hay un constructor de payload para el tipo de documento '{comprobante.tipo_documento}'.")


def construir_payload_baja(comprobante: ComprobanteElectronico, empresa: Empresa, motivo: str) -> dict:
    hoy = timezone.localdate()
    correlativo_diario = ComprobanteElectronico.objects.filter(
        estado__in=[ComprobanteElectronico.Estado.BAJA_SOLICITADA, ComprobanteElectronico.Estado.BAJA_ACEPTADA],
        fecha_envio__date=hoy,
    ).count() + 1

    fecha_comprobante = (
        timezone.localtime(comprobante.fecha_respuesta).date()
        if comprobante.fecha_respuesta else hoy
    )

    return {
        'empresa': {
            'ruc': empresa.ruc,
            'razon_social': empresa.razon_social,
            'modo': 1 if empresa.sunat_modo == Empresa.ModoSunat.PRODUCCION else 0,
            'usu_secundario_user': empresa.sunat_usuario_secundario or '',
            'usu_secundario_password': empresa.sunat_clave_secundaria or '',
        },
        'anulacion': {
            'correlativo_diario': str(correlativo_diario),
            'fecha_comprobante': fecha_comprobante.isoformat(),
            'tipo_documento_codigo': comprobante.tipo_documento,
            'serie': comprobante.serie,
            'numero': comprobante.numero,
            'motivo': motivo,
        },
    }


def construir_payload_ticket(comprobante: ComprobanteElectronico, empresa: Empresa) -> dict:
    # La documentación no detalla el payload exacto de resumen_ticket.php;
    # se sigue el mismo patrón de credenciales que baja.php. Ajustar si la
    # API espera un formato distinto.
    return {
        'empresa': {
            'ruc': empresa.ruc,
            'modo': 1 if empresa.sunat_modo == Empresa.ModoSunat.PRODUCCION else 0,
            'usu_secundario_user': empresa.sunat_usuario_secundario or '',
            'usu_secundario_password': empresa.sunat_clave_secundaria or '',
        },
        'ticket': comprobante.ticket_sunat,
    }


def aplicar_correcciones(payload: dict, correcciones: dict) -> None:
    """Mezcla recursiva de `correcciones` sobre `payload`, para reenviar un
    documento RECHAZADO corrigiendo solo los campos que fallaron."""
    for key, value in correcciones.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            aplicar_correcciones(payload[key], value)
        else:
            payload[key] = value


def interpretar_respuesta(data) -> tuple:
    """Heurística best-effort — ver docstring del módulo."""
    if not isinstance(data, dict):
        return ComprobanteElectronico.Estado.ENVIADO, 'Respuesta no reconocida de la API.', {}

    exito = data.get('success')
    if exito is None:
        exito = data.get('exito')
    if exito is None:
        exito = data.get('aceptado')

    codigo = str(data.get('codigo', data.get('code', ''))).strip()
    mensaje = str(data.get('mensaje') or data.get('message') or data.get('error') or '')

    extra = {
        'pdf_url': data.get('pdf') or data.get('pdf_url') or data.get('enlace_pdf') or '',
        'xml_url': data.get('xml') or data.get('xml_url') or data.get('enlace_xml') or '',
        'cdr_url': data.get('cdr') or data.get('cdr_url') or data.get('enlace_cdr') or '',
        'hash_cdr': data.get('hash') or data.get('codigoHash') or '',
    }

    if exito is True or codigo in ('0', '200') or 'aceptad' in mensaje.lower():
        return ComprobanteElectronico.Estado.ACEPTADO, mensaje or 'Aceptado por SUNAT.', extra
    if 'observ' in mensaje.lower():
        return ComprobanteElectronico.Estado.OBSERVADO, mensaje, extra
    if exito is False or (codigo and codigo not in ('0', '200')) or 'rechaz' in mensaje.lower():
        return ComprobanteElectronico.Estado.RECHAZADO, mensaje or 'Rechazado por SUNAT.', extra

    logger.warning("Respuesta de API SUNAT con forma no reconocida; queda en ENVIADO para revisión manual: %s", data)
    return ComprobanteElectronico.Estado.ENVIADO, 'Respuesta recibida; revisar manualmente (formato no reconocido).', extra


def interpretar_respuesta_ticket(data) -> tuple:
    estado, mensaje, extra = interpretar_respuesta(data)
    if estado == ComprobanteElectronico.Estado.ACEPTADO:
        return ComprobanteElectronico.Estado.BAJA_ACEPTADA, mensaje, extra
    return estado, mensaje, extra
