"""
Generación de avisos a clientes.

`generar_avisos()` es idempotente: puede ejecutarse las veces que se quiera (botón en la
pantalla o comando programado) sin duplicar avisos. Cada generador devuelve los avisos que
HOY corresponden; los pendientes que ya no corresponden (cita cancelada, cuota pagada,
vehículo ya entregado...) se descartan solos.
"""
import re
from datetime import timedelta
from urllib.parse import quote

from django.db import transaction
from django.utils import timezone

from apps.documentos.models import Documento
from apps.seguridad.models import Empresa
from apps.taller.models import Cita, OrdenTrabajo
from apps.vehiculos.mantenimientos import calcular_proximo_mantenimiento
from apps.vehiculos.models import MantenimientoVehiculo, Vehiculo
from apps.ventas.models import CuotaCredito

from .models import AvisoCliente

# Ventanas de anticipación (en días).
DIAS_RECORDATORIO_CITA = 1
DIAS_VEHICULO_LISTO = 7
DIAS_COTIZACION = 2
DIAS_MANTENIMIENTO = 15
KM_MANTENIMIENTO = 500
DIAS_DOCUMENTO_ANTES = 15
DIAS_DOCUMENTO_DESPUES = 30
DIAS_CUOTA_ANTES = 3


def normalizar_telefono(valor):
    """Devuelve el número solo con dígitos y código de país de Perú, o '' si no parece válido."""
    digitos = re.sub(r'\D', '', valor or '')
    if len(digitos) == 9 and digitos.startswith('9'):
        return '51' + digitos
    if len(digitos) == 11 and digitos.startswith('519'):
        return digitos
    return ''


def whatsapp_url(telefono, mensaje):
    if not telefono:
        return None
    return f'https://wa.me/{telefono}?text={quote(mensaje)}'


def _fecha(d):
    return d.strftime('%d/%m/%Y')


def _nombre_cliente(cliente):
    return f'{cliente.nombres} {cliente.apellidos}'.strip()


def _aviso(tipo, referencia, cliente, mensaje, fecha_evento=None):
    return {
        'tipo': tipo,
        'referencia': referencia,
        'cliente': cliente,
        'telefono': normalizar_telefono(cliente.telefono),
        'mensaje': mensaje,
        'fecha_evento': fecha_evento,
    }


def _empresa_nombre():
    empresa = Empresa.objects.first()
    if not empresa:
        return 'el taller'
    return empresa.nombre_comercial or empresa.razon_social


# ──────────────────────────────────────────────────────────────────────────────
# Generadores: cada uno devuelve una lista de avisos que corresponden HOY.
# ──────────────────────────────────────────────────────────────────────────────

def _citas(hoy, taller):
    limite = hoy + timedelta(days=DIAS_RECORDATORIO_CITA)
    citas = Cita.objects.select_related('cliente', 'vehiculo').filter(
        estado__in=[Cita.Estado.CONFIRMADA, Cita.Estado.REPROGRAMADA],
        fecha_inicio__date__gte=hoy, fecha_inicio__date__lte=limite,
    )
    for cita in citas:
        inicio = timezone.localtime(cita.fecha_inicio)
        cuando = 'hoy' if inicio.date() == hoy else 'mañana'
        yield _aviso(
            AvisoCliente.Tipo.CITA, f'cita:{cita.id}:{inicio:%Y%m%d%H%M}', cita.cliente,
            f'Hola {cita.cliente.nombres}, le recordamos su cita en {taller} {cuando} {_fecha(inicio)} '
            f'a las {inicio:%H:%M} para su vehículo {cita.vehiculo.placa}. '
            'Si no puede asistir, avísenos por favor para reprogramar.',
            inicio.date(),
        )


def _vehiculos_listos(hoy, taller):
    desde = timezone.now() - timedelta(days=DIAS_VEHICULO_LISTO)
    ordenes = OrdenTrabajo.objects.select_related('cliente', 'vehiculo').filter(
        estado=OrdenTrabajo.Estado.FINALIZADO, cliente__isnull=False, fecha_finalizacion__gte=desde,
    )
    for orden in ordenes:
        yield _aviso(
            AvisoCliente.Tipo.VEHICULO_LISTO, f'ot:{orden.id}', orden.cliente,
            f'Hola {orden.cliente.nombres}, su vehículo {orden.vehiculo.placa} ya está listo para recoger '
            f'en {taller}. ¡Lo esperamos!',
            timezone.localtime(orden.fecha_finalizacion).date(),
        )


def _cotizaciones(hoy, taller):
    limite = hoy + timedelta(days=DIAS_COTIZACION)
    ordenes = OrdenTrabajo.objects.select_related('cliente', 'vehiculo').filter(
        estado=OrdenTrabajo.Estado.ESPERANDO_APROBACION, cliente__isnull=False,
        fecha_vencimiento_cotizacion__date__gte=hoy, fecha_vencimiento_cotizacion__date__lte=limite,
    )
    for orden in ordenes:
        vence = timezone.localtime(orden.fecha_vencimiento_cotizacion).date()
        numero = orden.numero_cotizacion or orden.numero
        yield _aviso(
            AvisoCliente.Tipo.COTIZACION, f'cot:{orden.id}:{vence:%Y%m%d}', orden.cliente,
            f'Hola {orden.cliente.nombres}, la cotización {numero} de su vehículo {orden.vehiculo.placa} '
            f'vence el {_fecha(vence)}. Si desea aprobarla, escríbanos o llámenos y coordinamos el trabajo.',
            vence,
        )


def _mantenimientos(hoy, taller):
    vehiculos = Vehiculo.objects.filter(estado=True, mantenimientos__activo=True).distinct().prefetch_related('clientes')
    for vehiculo in vehiculos:
        cliente = next((c for c in vehiculo.clientes.all() if c.estado), None)
        if cliente is None:
            continue
        for tipo in MantenimientoVehiculo.Tipo.values:
            registro = vehiculo.mantenimientos.filter(tipo=tipo, activo=True).first()
            if not registro:
                continue
            info = calcular_proximo_mantenimiento(registro, vehiculo.kilometraje_actual, hoy)
            por_fecha = info['dias_restantes'] is not None and info['dias_restantes'] <= DIAS_MANTENIMIENTO
            por_km = info['km_restantes'] is not None and info['km_restantes'] <= KM_MANTENIMIENTO
            if not (por_fecha or por_km):
                continue
            detalle = []
            if info['proxima_fecha']:
                detalle.append(f"para el {_fecha(info['proxima_fecha'])}")
            if info['proximo_km']:
                detalle.append(f"a los {info['proximo_km']:,} km".replace(',', ' '))
            yield _aviso(
                AvisoCliente.Tipo.MANTENIMIENTO, f'mant:{registro.id}', cliente,
                f"Hola {cliente.nombres}, a su vehículo {vehiculo.placa} le corresponde: "
                f"{info['tipo_display'].lower()} ({' o '.join(detalle)}). "
                f'¿Desea agendar una cita en {taller}?',
                info['proxima_fecha'] or hoy,
            )


def _documentos(hoy, taller):
    documentos = Documento.objects.select_related('vehiculo', 'cliente').prefetch_related('vehiculo__clientes').filter(
        activo=True, fecha_vencimiento__isnull=False,
        fecha_vencimiento__gte=hoy - timedelta(days=DIAS_DOCUMENTO_DESPUES),
        fecha_vencimiento__lte=hoy + timedelta(days=DIAS_DOCUMENTO_ANTES),
    )
    for doc in documentos:
        if doc.cliente_id:
            cliente, sobre = doc.cliente, f'su {doc.get_tipo_display().lower()}'
        else:
            cliente = next((c for c in doc.vehiculo.clientes.all() if c.estado), None)
            sobre = f'el {doc.get_tipo_display().lower()} de su vehículo {doc.vehiculo.placa}'
        if cliente is None:
            continue
        vencido = doc.fecha_vencimiento < hoy
        accion = 'venció' if vencido else 'vence'
        cierre = ('Le recomendamos renovarlo cuanto antes.' if vencido
                  else 'Le avisamos con tiempo para que pueda renovarlo.')
        yield _aviso(
            AvisoCliente.Tipo.DOCUMENTO, f'doc:{doc.id}:{doc.fecha_vencimiento:%Y%m%d}', cliente,
            f'Hola {cliente.nombres}, {sobre} {accion} el {_fecha(doc.fecha_vencimiento)}. {cierre} '
            f'Atentamente, {taller}.',
            doc.fecha_vencimiento,
        )


def _cuotas(hoy, taller):
    cuotas = CuotaCredito.objects.select_related('cuenta_cobrar__venta__cliente').filter(
        estado__in=[CuotaCredito.Estado.PENDIENTE, CuotaCredito.Estado.PARCIAL, CuotaCredito.Estado.ATRASADA],
        saldo_pendiente__gt=0, fecha_vencimiento__lte=hoy + timedelta(days=DIAS_CUOTA_ANTES),
    )
    for cuota in cuotas:
        cliente = cuota.cuenta_cobrar.venta.cliente
        vencida = cuota.fecha_vencimiento < hoy
        etapa = 'v' if vencida else 'pv'
        verbo = 'venció' if vencida else 'vence'
        yield _aviso(
            AvisoCliente.Tipo.CUOTA, f'cuota:{cuota.id}:{etapa}', cliente,
            f'Hola {cliente.nombres}, le recordamos que la cuota {cuota.numero_cuota} de su crédito '
            f'({cuota.cuenta_cobrar.codigo_credito}) por S/ {cuota.saldo_pendiente:.2f} {verbo} el '
            f'{_fecha(cuota.fecha_vencimiento)}. Gracias por su preferencia, {taller}.',
            cuota.fecha_vencimiento,
        )


GENERADORES = (_citas, _vehiculos_listos, _cotizaciones, _mantenimientos, _documentos, _cuotas)


@transaction.atomic
def generar_avisos(hoy=None):
    """Crea los avisos que faltan y descarta los pendientes que ya no corresponden."""
    hoy = hoy or timezone.localdate()
    taller = _empresa_nombre()
    vigentes = set()
    creados = 0

    for generador in GENERADORES:
        for datos in generador(hoy, taller):
            clave = (datos['tipo'], datos['referencia'])
            vigentes.add(clave)
            valores = {
                'cliente': datos['cliente'],
                'cliente_nombre': _nombre_cliente(datos['cliente']),
                'telefono': datos['telefono'],
                'mensaje': datos['mensaje'],
                'fecha_evento': datos['fecha_evento'],
            }
            aviso, nuevo = AvisoCliente.objects.get_or_create(
                tipo=datos['tipo'], referencia=datos['referencia'], defaults=valores,
            )
            creados += int(nuevo)
            # Un aviso aún pendiente se mantiene al día (ej. el cliente ya tiene teléfono,
            # o cambió el saldo de la cuota); los ya enviados no se tocan.
            if not nuevo and aviso.estado == AvisoCliente.Estado.PENDIENTE:
                cambios = [campo for campo, valor in valores.items() if getattr(aviso, campo) != valor]
                if cambios:
                    for campo in cambios:
                        setattr(aviso, campo, valores[campo])
                    aviso.save(update_fields=cambios)

    descartados = 0
    for aviso in AvisoCliente.objects.filter(estado=AvisoCliente.Estado.PENDIENTE):
        if (aviso.tipo, aviso.referencia) not in vigentes:
            aviso.estado = AvisoCliente.Estado.DESCARTADO
            aviso.descartado_automatico = True
            aviso.atendido_en = timezone.now()
            aviso.save(update_fields=['estado', 'descartado_automatico', 'atendido_en'])
            descartados += 1
    return {'creados': creados, 'descartados': descartados}
