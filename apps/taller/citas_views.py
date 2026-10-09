import logging
from datetime import date, datetime, time, timedelta
from rest_framework import viewsets, status, pagination
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from django.db import transaction
from django.utils import timezone
from .models import (
    BloqueoAgendaSucursal,
    Cita,
    CitaHistorial,
    ConfiguracionAgendaSucursal,
    HorarioAgendaSucursal,
    ListaEsperaCita,
    OrdenTrabajo,
    TipoServicio,
    OrdenHistorialEstado,
)
from apps.vehiculos.models import Vehiculo
from .serializers import (
    BloqueoAgendaSucursalSerializer,
    CitaHistorialSerializer,
    CitaSerializer,
    ConfiguracionAgendaSucursalSerializer,
    ListaEsperaCitaSerializer,
    ReservaPublicaCitaSerializer,
    TipoServicioSerializer,
)
from apps.inventario.models import Sucursal
from apps.clientes.models import Cliente
from apps.seguridad.permissions import TienePermiso

logger = logging.getLogger(__name__)


def asegurar_configuracion_agenda(sucursal):
    config, _ = ConfiguracionAgendaSucursal.objects.get_or_create(
        sucursal=sucursal,
        defaults={'intervalo_minutos': 30, 'capacidad_simultanea': 3, 'activo': True},
    )
    existentes = set(config.horarios.values_list('dia_semana', flat=True))
    faltantes = []
    for dia in range(7):
        if dia in existentes:
            continue
        faltantes.append(HorarioAgendaSucursal(
            configuracion=config,
            dia_semana=dia,
            cerrado=(dia == 6),
        ))
    if faltantes:
        HorarioAgendaSucursal.objects.bulk_create(faltantes)
    return config


def registrar_historial_cita(
    cita,
    accion,
    usuario=None,
    estado_anterior=None,
    estado_nuevo=None,
    fecha_inicio_anterior=None,
    fecha_inicio_nueva=None,
    observacion=None,
):
    return CitaHistorial.objects.create(
        cita=cita,
        accion=accion,
        usuario=usuario if getattr(usuario, 'is_authenticated', False) else None,
        estado_anterior=estado_anterior,
        estado_nuevo=estado_nuevo,
        fecha_inicio_anterior=fecha_inicio_anterior,
        fecha_inicio_nueva=fecha_inicio_nueva,
        observacion=observacion,
    )


def calcular_disponibilidad_sucursal(sucursal, fecha_desde, fecha_hasta, duracion_minutos=60):
    config = asegurar_configuracion_agenda(sucursal)
    tz = timezone.get_current_timezone()
    capacidad = config.capacidad_simultanea
    intervalo_minutos = config.intervalo_minutos
    horarios = {h.dia_semana: h for h in config.horarios.all()}
    rango_inicio = timezone.make_aware(datetime.combine(fecha_desde, time.min), tz)
    rango_fin = timezone.make_aware(datetime.combine(fecha_hasta, time.max), tz)

    citas = list(
        Cita.objects
        .select_related('cliente', 'vehiculo', 'tipo_servicio')
        .filter(sucursal=sucursal, fecha_inicio__lt=rango_fin, fecha_fin__gt=rango_inicio)
        .exclude(estado__in=[Cita.Estado.CANCELADA, Cita.Estado.NO_ASISTIO])
        .order_by('fecha_inicio')
    )
    bloqueos = list(
        BloqueoAgendaSucursal.objects
        .filter(sucursal=sucursal, activo=True, fecha_inicio__lt=rango_fin, fecha_fin__gt=rango_inicio)
        .order_by('fecha_inicio')
    )

    ahora = timezone.now()
    dias = []
    dia_actual = fecha_desde
    while dia_actual <= fecha_hasta:
        horario = horarios.get(dia_actual.weekday())
        if not config.activo or not horario or horario.cerrado:
            dias.append({
                'fecha': dia_actual.isoformat(),
                'cerrado': True,
                'hora_inicio': None,
                'hora_fin': None,
                'slots': [],
            })
            dia_actual += timedelta(days=1)
            continue

        slots = []
        cursor = timezone.make_aware(datetime.combine(dia_actual, horario.hora_inicio), tz)
        cierre = timezone.make_aware(datetime.combine(dia_actual, horario.hora_fin), tz)
        while cursor + timedelta(minutes=duracion_minutos) <= cierre:
            slot_fin = cursor + timedelta(minutes=duracion_minutos)
            citas_cruzadas = [c for c in citas if c.fecha_inicio < slot_fin and c.fecha_fin > cursor]
            bloqueos_cruzados = [b for b in bloqueos if b.fecha_inicio < slot_fin and b.fecha_fin > cursor]
            horario_pasado = slot_fin <= ahora
            disponible = len(citas_cruzadas) < capacidad and not bloqueos_cruzados and not horario_pasado
            slots.append({
                'inicio': cursor.isoformat(),
                'fin': slot_fin.isoformat(),
                'hora': timezone.localtime(cursor).strftime('%H:%M'),
                'disponible': disponible,
                'ocupadas': len(citas_cruzadas),
                'capacidad': capacidad,
                'bloqueado': bool(bloqueos_cruzados),
                'motivo': 'Horario pasado' if horario_pasado else (bloqueos_cruzados[0].motivo if bloqueos_cruzados else ('Reservado' if len(citas_cruzadas) >= capacidad else '')),
            })
            cursor += timedelta(minutes=intervalo_minutos)

        dias.append({
            'fecha': dia_actual.isoformat(),
            'cerrado': False,
            'hora_inicio': horario.hora_inicio.strftime('%H:%M'),
            'hora_fin': horario.hora_fin.strftime('%H:%M'),
            'slots': slots,
        })
        dia_actual += timedelta(days=1)

    return {
        'sucursal': sucursal.id,
        'fecha_desde': fecha_desde.isoformat(),
        'fecha_hasta': fecha_hasta.isoformat(),
        'duracion_minutos': duracion_minutos,
        'intervalo_minutos': intervalo_minutos,
        'capacidad': capacidad,
        'dias': dias,
    }


def obtener_o_crear_cliente_vehiculo(data):
    # El portal solo crea registros que no existen. Una reserva pública no debe
    # sobrescribir datos ya validados por el personal del taller.
    cliente, _ = Cliente.objects.get_or_create(
        dni=data['documento'],
        defaults={
            'tipo_documento': 'RUC' if len(data['documento']) == 11 else 'DNI',
            'nombres': data['nombres'].strip(),
            'apellidos': (data.get('apellidos') or '').strip(),
            'telefono': data['telefono'].strip(),
            'email': data.get('email') or None,
            'estado': True,
        },
    )
    vehiculo, _ = Vehiculo.objects.get_or_create(
        placa=data['placa'],
        defaults={
            'marca': (data.get('marca') or 'POR DEFINIR').strip() or 'POR DEFINIR',
            'modelo': (data.get('modelo') or 'POR DEFINIR').strip() or 'POR DEFINIR',
            'estado': True,
        },
    )
    vehiculo.clientes.add(cliente)
    return cliente, vehiculo


class CitaPagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class TipoServicioViewSet(viewsets.ModelViewSet):
    queryset = TipoServicio.objects.all()
    serializer_class = TipoServicioSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("TIPOS_SERVICIO.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("TIPOS_SERVICIO.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("TIPOS_SERVICIO.ELIMINAR")]
        return [TienePermiso("TIPOS_SERVICIO.EDITAR")]


class ConfiguracionAgendaSucursalViewSet(viewsets.ModelViewSet):
    serializer_class = ConfiguracionAgendaSucursalSerializer
    pagination_class = None

    def get_permissions(self):
        return [TienePermiso("CITAS.CONFIGURAR_AGENDA")]

    def get_queryset(self):
        for sucursal in Sucursal.objects.filter(estado=True):
            asegurar_configuracion_agenda(sucursal)
        return (
            ConfiguracionAgendaSucursal.objects
            .select_related('sucursal')
            .prefetch_related('horarios')
            .order_by('sucursal__nombre')
        )

    @action(detail=False, methods=['get'], url_path='por-sucursal/(?P<sucursal_id>[^/.]+)')
    def por_sucursal(self, request, sucursal_id=None):
        sucursal = Sucursal.objects.get(pk=sucursal_id)
        config = asegurar_configuracion_agenda(sucursal)
        serializer = self.get_serializer(config)
        return Response(serializer.data)


class BloqueoAgendaSucursalViewSet(viewsets.ModelViewSet):
    serializer_class = BloqueoAgendaSucursalSerializer

    def get_permissions(self):
        return [TienePermiso("CITAS.CONFIGURAR_AGENDA")]

    def get_queryset(self):
        qs = BloqueoAgendaSucursal.objects.select_related('sucursal', 'creado_por').order_by('fecha_inicio')
        sucursal = self.request.query_params.get('sucursal')
        fecha_desde = self.request.query_params.get('fecha_desde')
        fecha_hasta = self.request.query_params.get('fecha_hasta')
        activo = self.request.query_params.get('activo')
        if sucursal:
            qs = qs.filter(sucursal_id=sucursal)
        if fecha_desde:
            qs = qs.filter(fecha_fin__date__gte=fecha_desde)
        if fecha_hasta:
            qs = qs.filter(fecha_inicio__date__lte=fecha_hasta)
        if activo in ('true', 'false', '1', '0'):
            qs = qs.filter(activo=activo in ('true', '1'))
        return qs

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class ListaEsperaCitaViewSet(viewsets.ModelViewSet):
    serializer_class = ListaEsperaCitaSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("CITAS.VER")]
        return [TienePermiso("CITAS.EDITAR")]

    def get_queryset(self):
        qs = (
            ListaEsperaCita.objects
            .select_related('sucursal', 'tipo_servicio', 'cliente', 'vehiculo', 'cita_convertida', 'creado_por')
            .order_by('-fecha_creacion')
        )
        params = self.request.query_params
        if params.get('sucursal'):
            qs = qs.filter(sucursal_id=params['sucursal'])
        if params.get('estado'):
            qs = qs.filter(estado=params['estado'])
        if params.get('fecha_desde'):
            qs = qs.filter(fecha_preferida__gte=params['fecha_desde'])
        if params.get('fecha_hasta'):
            qs = qs.filter(fecha_preferida__lte=params['fecha_hasta'])
        if params.get('placa'):
            qs = qs.filter(placa__icontains=params['placa'])
        return qs

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user, creado_desde_portal=False)


class CitasCatalogoPublicoView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        sucursales = [
            {'id': s.id, 'nombre': s.nombre, 'direccion': s.direccion}
            for s in Sucursal.objects.filter(estado=True).order_by('nombre')
        ]
        tipos_servicio = [
            {'id': t.id, 'nombre': t.nombre}
            for t in TipoServicio.objects.filter(estado=True).order_by('nombre')
        ]
        return Response({'sucursales': sucursales, 'tipos_servicio': tipos_servicio})


class DisponibilidadCitasPublicaView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            sucursal = Sucursal.objects.get(pk=request.query_params.get('sucursal'), estado=True)
            fecha_desde = date.fromisoformat(request.query_params.get('fecha_desde'))
            fecha_hasta = date.fromisoformat(request.query_params.get('fecha_hasta'))
            duracion = int(request.query_params.get('duracion_minutos', 60))
        except (Sucursal.DoesNotExist, TypeError, ValueError):
            return Response({'error': 'Parametros de disponibilidad invalidos.'}, status=status.HTTP_400_BAD_REQUEST)

        if fecha_hasta < fecha_desde:
            return Response({'error': 'La fecha hasta debe ser mayor o igual a la fecha desde.'}, status=status.HTTP_400_BAD_REQUEST)
        if fecha_desde < timezone.localdate():
            return Response({'error': 'La disponibilidad solo puede consultarse desde la fecha actual.'}, status=status.HTTP_400_BAD_REQUEST)
        if (fecha_hasta - fecha_desde).days > 14:
            return Response({'error': 'El rango publico maximo es de 14 dias.'}, status=status.HTTP_400_BAD_REQUEST)
        if duracion <= 0:
            return Response({'error': 'La duracion debe ser mayor a cero.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response(calcular_disponibilidad_sucursal(sucursal, fecha_desde, fecha_hasta, duracion))


class ReservaCitaPublicaView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @transaction.atomic
    def post(self, request):
        serializer = ReservaPublicaCitaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        cliente, vehiculo = obtener_o_crear_cliente_vehiculo(data)

        if data.get('lista_espera'):
            espera = ListaEsperaCita.objects.create(
                sucursal=data['sucursal'],
                tipo_servicio=data.get('tipo_servicio'),
                cliente=cliente,
                vehiculo=vehiculo,
                documento=data['documento'],
                nombres=data['nombres'],
                apellidos=data.get('apellidos') or '',
                telefono=data['telefono'],
                email=data.get('email') or None,
                placa=data['placa'],
                marca=data.get('marca') or '',
                modelo=data.get('modelo') or '',
                fecha_preferida=timezone.localtime(data['fecha_inicio']).date(),
                hora_preferida=timezone.localtime(data['fecha_inicio']).time(),
                duracion_minutos=data['duracion_minutos'],
                motivo=data.get('motivo') or None,
            )
            return Response({
                'tipo': 'lista_espera',
                'id': espera.id,
                'mensaje': 'Solicitud registrada en lista de espera. El taller se comunicara contigo.',
            }, status=status.HTTP_201_CREATED)

        fecha_fin = data['fecha_inicio'] + timedelta(minutes=data['duracion_minutos'])
        cita_payload = {
            'cliente': cliente.id,
            'vehiculo': vehiculo.id,
            'sucursal': data['sucursal'].id,
            'tipo_servicio': data.get('tipo_servicio').id if data.get('tipo_servicio') else None,
            'fecha_inicio': data['fecha_inicio'],
            'fecha_fin': fecha_fin,
            'duracion_minutos': data['duracion_minutos'],
            'origen': Cita.Origen.PORTAL,
            'estado': Cita.Estado.SOLICITADA,
            'motivo': data.get('motivo') or None,
            'observaciones_cliente': 'Reserva creada desde portal publico.',
        }
        cita_serializer = CitaSerializer(data=cita_payload)
        cita_serializer.is_valid(raise_exception=True)

        last_cita = Cita.objects.order_by('-id').first()
        numero_cita = f"{(1 if not last_cita else last_cita.id + 1):06d}"
        cita = cita_serializer.save(numero=numero_cita, asesor=None, creado_por=None)
        cita.vehiculo.clientes.add(cita.cliente)
        registrar_historial_cita(
            cita=cita,
            accion=CitaHistorial.Accion.CREACION,
            estado_nuevo=cita.estado,
            fecha_inicio_nueva=cita.fecha_inicio,
            observacion='Cita solicitada desde portal publico.',
        )
        return Response({
            'tipo': 'cita',
            'id': cita.id,
            'numero': cita.numero,
            'estado': cita.estado,
            'mensaje': 'Cita solicitada correctamente. El taller confirmara tu reserva.',
        }, status=status.HTTP_201_CREATED)


class CitaViewSet(viewsets.ModelViewSet):
    serializer_class = CitaSerializer
    pagination_class = CitaPagination

    def get_permissions(self):
        if self.action in ('recepcionar',):
            return [TienePermiso("CITAS.RECEPCIONAR")]
        if self.action in ('confirmar', 'cancelar', 'no_asistio'):
            return [TienePermiso("CITAS.CAMBIAR_ESTADO")]
        if self.request.method == 'GET':
            return [TienePermiso("CITAS.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("CITAS.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("CITAS.ELIMINAR")]
        return [TienePermiso("CITAS.EDITAR")]

    def get_queryset(self):
        queryset = Cita.objects.select_related(
            'cliente', 'vehiculo', 'sucursal', 'tipo_servicio',
            'mecanico_preferido', 'asesor', 'orden_trabajo'
        ).order_by('fecha_inicio')

        params = self.request.query_params
        estado = params.get('estado')
        sucursal = params.get('sucursal')
        mecanico = params.get('mecanico_preferido')
        cliente = params.get('cliente')
        placa = params.get('placa')
        fecha_desde = params.get('fecha_desde')
        fecha_hasta = params.get('fecha_hasta')

        if estado:
            queryset = queryset.filter(estado=estado)
        if sucursal:
            queryset = queryset.filter(sucursal_id=sucursal)
        if mecanico:
            queryset = queryset.filter(mecanico_preferido_id=mecanico)
        if cliente:
            queryset = queryset.filter(cliente_id=cliente)
        if placa:
            queryset = queryset.filter(vehiculo__placa__icontains=placa)
        if fecha_desde:
            queryset = queryset.filter(fecha_inicio__date__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(fecha_inicio__date__lte=fecha_hasta)

        return queryset

    def perform_create(self, serializer):
        last_cita = Cita.objects.order_by('-id').first()
        next_num = 1 if not last_cita else last_cita.id + 1
        numero_cita = f"{next_num:06d}"
        cita = serializer.save(
            numero=numero_cita,
            asesor=self.request.user,
            creado_por=self.request.user,
        )
        cita.vehiculo.clientes.add(cita.cliente)
        registrar_historial_cita(
            cita=cita,
            accion=CitaHistorial.Accion.CREACION,
            usuario=self.request.user,
            estado_nuevo=cita.estado,
            fecha_inicio_nueva=cita.fecha_inicio,
            observacion="Cita registrada.",
        )

    def perform_update(self, serializer):
        cita_anterior = serializer.instance
        estado_anterior = cita_anterior.estado
        fecha_inicio_anterior = cita_anterior.fecha_inicio
        fecha_fin_anterior = cita_anterior.fecha_fin
        cita = serializer.save()

        estado_cambio = estado_anterior != cita.estado
        fecha_cambio = fecha_inicio_anterior != cita.fecha_inicio or fecha_fin_anterior != cita.fecha_fin
        if not estado_cambio and not fecha_cambio:
            observacion = "Datos de cita actualizados."
            accion = CitaHistorial.Accion.EDICION
        elif fecha_cambio:
            observacion = "Cita reprogramada."
            accion = CitaHistorial.Accion.REPROGRAMACION
        else:
            observacion = "Estado de cita actualizado."
            accion = CitaHistorial.Accion.CAMBIO_ESTADO

        registrar_historial_cita(
            cita=cita,
            accion=accion,
            usuario=self.request.user,
            estado_anterior=estado_anterior if estado_cambio else None,
            estado_nuevo=cita.estado if estado_cambio else None,
            fecha_inicio_anterior=fecha_inicio_anterior if fecha_cambio else None,
            fecha_inicio_nueva=cita.fecha_inicio if fecha_cambio else None,
            observacion=observacion,
        )

    @action(detail=False, methods=['get'])
    def disponibilidad(self, request):
        params = request.query_params
        sucursal_id = params.get('sucursal')
        if not sucursal_id:
            return Response({'error': 'Debe indicar la sucursal.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            fecha_desde = date.fromisoformat(params.get('fecha_desde'))
            fecha_hasta = date.fromisoformat(params.get('fecha_hasta'))
        except (TypeError, ValueError):
            return Response({'error': 'Debe indicar fecha_desde y fecha_hasta en formato YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        if fecha_hasta < fecha_desde:
            return Response({'error': 'fecha_hasta debe ser mayor o igual que fecha_desde.'}, status=status.HTTP_400_BAD_REQUEST)
        if (fecha_hasta - fecha_desde).days > 31:
            return Response({'error': 'El rango maximo de consulta es de 31 dias.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            duracion_minutos = int(params.get('duracion_minutos', 60))
            intervalo_minutos = int(params.get('intervalo_minutos', 30))
            capacidad = int(params.get('capacidad', 3))
        except ValueError:
            return Response({'error': 'duracion_minutos, intervalo_minutos y capacidad deben ser numericos.'}, status=status.HTTP_400_BAD_REQUEST)

        if duracion_minutos <= 0 or intervalo_minutos <= 0 or capacidad <= 0:
            return Response({'error': 'Duracion, intervalo y capacidad deben ser mayores a cero.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            sucursal = Sucursal.objects.get(pk=sucursal_id)
        except Sucursal.DoesNotExist:
            return Response({'error': 'Sucursal no encontrada.'}, status=status.HTTP_404_NOT_FOUND)

        config = asegurar_configuracion_agenda(sucursal)
        horarios = {h.dia_semana: h for h in config.horarios.all()}
        tz = timezone.get_current_timezone()
        rango_inicio = timezone.make_aware(datetime.combine(fecha_desde, time.min), tz)
        rango_fin = timezone.make_aware(datetime.combine(fecha_hasta, time.max), tz)
        capacidad = config.capacidad_simultanea
        intervalo_minutos = config.intervalo_minutos

        if not config.activo:
            dias = []
            dia_actual = fecha_desde
            while dia_actual <= fecha_hasta:
                dias.append({
                    'fecha': dia_actual.isoformat(),
                    'label': dia_actual.strftime('%d/%m'),
                    'cerrado': True,
                    'hora_inicio': None,
                    'hora_fin': None,
                    'motivo': 'Agenda inactiva',
                    'slots': [],
                })
                dia_actual += timedelta(days=1)
            return Response({
                'sucursal': int(sucursal_id),
                'fecha_desde': fecha_desde.isoformat(),
                'fecha_hasta': fecha_hasta.isoformat(),
                'duracion_minutos': duracion_minutos,
                'intervalo_minutos': intervalo_minutos,
                'capacidad': capacidad,
                'dias': dias,
            })

        citas = list(
            Cita.objects
            .select_related('cliente', 'vehiculo', 'tipo_servicio')
            .filter(
                sucursal_id=sucursal_id,
                fecha_inicio__lt=rango_fin,
                fecha_fin__gt=rango_inicio,
            )
            .exclude(estado__in=[Cita.Estado.CANCELADA, Cita.Estado.NO_ASISTIO])
            .order_by('fecha_inicio')
        )
        bloqueos = list(
            BloqueoAgendaSucursal.objects
            .filter(
                sucursal_id=sucursal_id,
                activo=True,
                fecha_inicio__lt=rango_fin,
                fecha_fin__gt=rango_inicio,
            )
            .order_by('fecha_inicio')
        )

        ahora = timezone.now()
        dias = []
        dia_actual = fecha_desde
        while dia_actual <= fecha_hasta:
            slots = []
            horario = horarios.get(dia_actual.weekday())
            if not horario or horario.cerrado:
                dias.append({
                    'fecha': dia_actual.isoformat(),
                    'label': dia_actual.strftime('%d/%m'),
                    'cerrado': True,
                    'hora_inicio': None,
                    'hora_fin': None,
                    'slots': [],
                })
                dia_actual += timedelta(days=1)
                continue

            cursor = timezone.make_aware(datetime.combine(dia_actual, horario.hora_inicio), tz)
            cierre = timezone.make_aware(datetime.combine(dia_actual, horario.hora_fin), tz)

            while cursor + timedelta(minutes=duracion_minutos) <= cierre:
                slot_fin = cursor + timedelta(minutes=duracion_minutos)
                citas_cruzadas = [
                    cita for cita in citas
                    if cita.fecha_inicio < slot_fin and cita.fecha_fin > cursor
                ]
                bloqueos_cruzados = [
                    bloqueo for bloqueo in bloqueos
                    if bloqueo.fecha_inicio < slot_fin and bloqueo.fecha_fin > cursor
                ]
                horario_pasado = slot_fin <= ahora
                disponible = len(citas_cruzadas) < capacidad and not horario_pasado and not bloqueos_cruzados
                if horario_pasado:
                    motivo = 'Horario pasado'
                elif bloqueos_cruzados:
                    motivo = bloqueos_cruzados[0].motivo
                elif len(citas_cruzadas) >= capacidad:
                    motivo = 'Reservado'
                else:
                    motivo = ''
                slots.append({
                    'inicio': cursor.isoformat(),
                    'fin': slot_fin.isoformat(),
                    'hora': timezone.localtime(cursor).strftime('%H:%M'),
                    'disponible': disponible,
                    'ocupadas': len(citas_cruzadas),
                    'capacidad': capacidad,
                    'bloqueado': bool(bloqueos_cruzados),
                    'motivo': motivo,
                    'bloqueos': [
                        {'id': bloqueo.id, 'motivo': bloqueo.motivo}
                        for bloqueo in bloqueos_cruzados
                    ],
                    'citas': [
                        {
                            'id': cita.id,
                            'numero': cita.numero,
                            'estado': cita.estado,
                            'cliente': f"{cita.cliente.nombres} {cita.cliente.apellidos}".strip(),
                            'placa': cita.vehiculo.placa,
                            'tipo_servicio': cita.tipo_servicio.nombre if cita.tipo_servicio else '',
                        }
                        for cita in citas_cruzadas
                    ],
                })
                cursor += timedelta(minutes=intervalo_minutos)

            dias.append({
                'fecha': dia_actual.isoformat(),
                'label': dia_actual.strftime('%d/%m'),
                'cerrado': False,
                'hora_inicio': horario.hora_inicio.strftime('%H:%M'),
                'hora_fin': horario.hora_fin.strftime('%H:%M'),
                'slots': slots,
            })
            dia_actual += timedelta(days=1)

        return Response({
            'sucursal': int(sucursal_id),
            'fecha_desde': fecha_desde.isoformat(),
            'fecha_hasta': fecha_hasta.isoformat(),
            'duracion_minutos': duracion_minutos,
            'intervalo_minutos': config.intervalo_minutos,
            'capacidad': capacidad,
            'dias': dias,
        })

    @action(detail=True, methods=['get'])
    def historial(self, request, pk=None):
        cita = self.get_object()
        queryset = cita.historial.select_related('usuario').order_by('-fecha')
        serializer = CitaHistorialSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def confirmar(self, request, pk=None):
        cita = self.get_object()
        if cita.estado in (Cita.Estado.CANCELADA, Cita.Estado.RECEPCIONADA):
            return Response({'error': 'No se puede confirmar una cita cancelada o recepcionada.'}, status=status.HTTP_400_BAD_REQUEST)
        estado_anterior = cita.estado
        cita.estado = Cita.Estado.CONFIRMADA
        cita.save(update_fields=['estado', 'fecha_actualizacion'])
        registrar_historial_cita(
            cita=cita,
            accion=CitaHistorial.Accion.CAMBIO_ESTADO,
            usuario=request.user,
            estado_anterior=estado_anterior,
            estado_nuevo=cita.estado,
            observacion="Cita confirmada.",
        )
        return Response(self.get_serializer(cita).data)

    @action(detail=True, methods=['post'])
    def cancelar(self, request, pk=None):
        cita = self.get_object()
        if cita.estado == Cita.Estado.RECEPCIONADA:
            return Response({'error': 'No se puede cancelar una cita ya recepcionada.'}, status=status.HTTP_400_BAD_REQUEST)
        motivo = (request.data.get('motivo') or '').strip()
        estado_anterior = cita.estado
        if motivo:
            cita.observaciones_internas = f"{cita.observaciones_internas or ''}\nCancelacion: {motivo}".strip()
        cita.estado = Cita.Estado.CANCELADA
        cita.save(update_fields=['estado', 'observaciones_internas', 'fecha_actualizacion'])
        registrar_historial_cita(
            cita=cita,
            accion=CitaHistorial.Accion.CANCELACION,
            usuario=request.user,
            estado_anterior=estado_anterior,
            estado_nuevo=cita.estado,
            observacion=motivo or "Cita cancelada.",
        )
        return Response(self.get_serializer(cita).data)

    @action(detail=True, methods=['post'], url_path='no-asistio')
    def no_asistio(self, request, pk=None):
        cita = self.get_object()
        if cita.estado == Cita.Estado.RECEPCIONADA:
            return Response({'error': 'No se puede marcar como no asistio una cita recepcionada.'}, status=status.HTTP_400_BAD_REQUEST)
        estado_anterior = cita.estado
        cita.estado = Cita.Estado.NO_ASISTIO
        cita.save(update_fields=['estado', 'fecha_actualizacion'])
        registrar_historial_cita(
            cita=cita,
            accion=CitaHistorial.Accion.NO_ASISTIO,
            usuario=request.user,
            estado_anterior=estado_anterior,
            estado_nuevo=cita.estado,
            observacion="Cliente no asistio a la cita.",
        )
        return Response(self.get_serializer(cita).data)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def recepcionar(self, request, pk=None):
        cita = self.get_object()
        if cita.estado == Cita.Estado.RECEPCIONADA and cita.orden_trabajo_id:
            return Response(self.get_serializer(cita).data)
        if cita.estado in (Cita.Estado.CANCELADA, Cita.Estado.NO_ASISTIO):
            return Response({'error': 'No se puede recepcionar una cita cancelada o marcada como no asistio.'}, status=status.HTTP_400_BAD_REQUEST)

        last_ot = OrdenTrabajo.objects.order_by('-id').first()
        next_num = 1 if not last_ot else last_ot.id + 1
        numero_ot = f"{next_num:06d}"

        kilometraje = request.data.get('kilometraje_ingreso', cita.kilometraje_estimado)
        motivo_ingreso = request.data.get('motivo_ingreso') or cita.motivo or cita.observaciones_cliente

        orden = OrdenTrabajo.objects.create(
            numero=numero_ot,
            cliente=cita.cliente,
            vehiculo=cita.vehiculo,
            recepcionista=request.user,
            mecanico_asignado=cita.mecanico_preferido,
            tipo_servicio=cita.tipo_servicio,
            kilometraje_ingreso=kilometraje or None,
            motivo_ingreso=motivo_ingreso or None,
        )
        cita.vehiculo.clientes.add(cita.cliente)
        if orden.kilometraje_ingreso is not None:
            cita.vehiculo.kilometraje_actual = orden.kilometraje_ingreso
            cita.vehiculo.save(update_fields=['kilometraje_actual'])

        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=orden.estado,
            usuario=request.user,
            observaciones=f"Orden creada desde cita {cita.numero}",
        )

        estado_anterior = cita.estado
        cita.estado = Cita.Estado.RECEPCIONADA
        cita.orden_trabajo = orden
        cita.save(update_fields=['estado', 'orden_trabajo', 'fecha_actualizacion'])
        registrar_historial_cita(
            cita=cita,
            accion=CitaHistorial.Accion.RECEPCION,
            usuario=request.user,
            estado_anterior=estado_anterior,
            estado_nuevo=cita.estado,
            observacion=f"Cita recepcionada y vinculada a OT-{orden.numero}.",
        )
        return Response(self.get_serializer(cita).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def notificar(self, request, pk=None):
        cita = self.get_object()
        if cita.estado in (Cita.Estado.CANCELADA, Cita.Estado.NO_ASISTIO, Cita.Estado.RECEPCIONADA):
            return Response(
                {'error': 'No se puede enviar recordatorio a cita cancelada, no asistida o ya recepcionada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cliente = cita.cliente
        telefono_raw = (cliente.telefono or '').strip().replace(' ', '').replace('-', '')
        if telefono_raw and not telefono_raw.startswith('+'):
            telefono_wa = '+51' + telefono_raw if not telefono_raw.startswith('51') else '+' + telefono_raw
        else:
            telefono_wa = telefono_raw

        import urllib.parse as urlp
        fecha_local = timezone.localtime(cita.fecha_inicio)
        nombre_cliente = (cliente.nombres + ' ' + (cliente.apellidos or '')).strip()
        tipo_str = cita.tipo_servicio.nombre if cita.tipo_servicio else 'Servicio'
        sucursal_str = cita.sucursal.nombre if cita.sucursal else ''

        mensaje = (
            'Hola ' + nombre_cliente + '!\n\n'
            'Le recordamos su cita en *' + sucursal_str + '*:\n\n'
            'Fecha: *' + fecha_local.strftime('%d/%m/%Y') + '* a las *' + fecha_local.strftime('%H:%M') + '*\n'
            'Vehiculo: *' + cita.vehiculo.placa + '*\n'
            'Servicio: *' + tipo_str + '*\n\n'
            'Le esperamos. Si necesita reprogramar, comuniquese con nosotros.'
        )

        whatsapp_link = (
            'https://wa.me/' + telefono_wa.lstrip('+') + '?text=' + urlp.quote(mensaje)
        ) if telefono_wa else None

        registrar_historial_cita(
            cita=cita,
            accion=CitaHistorial.Accion.EDICION,
            usuario=request.user,
            observacion='Recordatorio enviado a ' + nombre_cliente + ' (' + (telefono_raw or 'sin telefono') + ').',
        )

        return Response({
            'telefono': telefono_raw,
            'telefono_wa': telefono_wa,
            'mensaje': mensaje,
            'whatsapp_link': whatsapp_link,
            'tiene_telefono': bool(telefono_wa),
        })
