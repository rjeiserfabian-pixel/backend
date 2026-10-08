from calendar import monthrange

from django.utils import timezone

from .models import MantenimientoVehiculo


def calcular_proximo_mantenimiento(registro, kilometraje_actual, hoy=None):
    hoy = hoy or timezone.localdate()
    proximo_km = (
        registro.kilometraje_realizado + registro.intervalo_km
        if registro.intervalo_km else None
    )
    proxima_fecha = None
    if registro.intervalo_meses:
        # Sumar meses de calendario conserva el dia o usa el ultimo dia del mes.
        meses = registro.fecha_realizado.year * 12 + registro.fecha_realizado.month - 1 + registro.intervalo_meses
        anio, mes = divmod(meses, 12)
        mes += 1
        dia = min(registro.fecha_realizado.day, monthrange(anio, mes)[1])
        proxima_fecha = registro.fecha_realizado.replace(year=anio, month=mes, day=dia)

    lectura_valida = kilometraje_actual is not None and kilometraje_actual >= registro.kilometraje_realizado
    km_restantes = proximo_km - kilometraje_actual if proximo_km is not None and lectura_valida else None
    dias_restantes = (proxima_fecha - hoy).days if proxima_fecha else None
    vencido_km = km_restantes is not None and km_restantes <= 0
    vencido_fecha = dias_restantes is not None and dias_restantes <= 0
    estado = 'VENCIDO' if vencido_km or vencido_fecha else (
        'VERIFICAR_KILOMETRAJE' if proximo_km is not None and not lectura_valida else 'PENDIENTE'
    )
    return {
        'tipo': registro.tipo,
        'tipo_display': registro.get_tipo_display(),
        'fecha_realizado': registro.fecha_realizado,
        'kilometraje_realizado': registro.kilometraje_realizado,
        'proximo_km': proximo_km,
        'proxima_fecha': proxima_fecha,
        'km_restantes': km_restantes,
        'dias_restantes': dias_restantes,
        'estado': estado,
        'vencido_km': vencido_km,
        'vencido_fecha': vencido_fecha,
    }


def proximos_mantenimientos(vehiculo):
    resultados = []
    for tipo in MantenimientoVehiculo.Tipo.values:
        registro = vehiculo.mantenimientos.filter(tipo=tipo, activo=True).first()
        if registro:
            resultados.append(calcular_proximo_mantenimiento(registro, vehiculo.kilometraje_actual))
    return resultados
