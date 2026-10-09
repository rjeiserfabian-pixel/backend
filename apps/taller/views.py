import logging
from datetime import date, datetime, time, timedelta
from rest_framework import viewsets, status, pagination
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from .models import (
    BloqueoAgendaSucursal, Cita, CitaHistorial, ConfiguracionAgendaSucursal, HorarioAgendaSucursal,
    ListaEsperaCita, OrdenTrabajo, Hallazgo, OrdenServicio, OrdenRepuesto, PlantillaPreventiva,
    PlantillaCorrectiva, TipoServicio, OrdenHistorialEstado
)
from apps.vehiculos.models import Vehiculo
from .serializers import (
    BloqueoAgendaSucursalSerializer, CitaHistorialSerializer, CitaSerializer, ConfiguracionAgendaSucursalSerializer,
    ListaEsperaCitaSerializer, ReservaPublicaCitaSerializer,
    OrdenTrabajoListSerializer, OrdenTrabajoDetailSerializer,
    HallazgoSerializer, OrdenServicioSerializer, OrdenRepuestoSerializer,
    PlantillaPreventivaSerializer, PlantillaCorrectivaSerializer, TipoServicioSerializer
)
from .services import aprobar_cotizacion_orden
from apps.inventario.models import MovimientoInventario, InventarioStock, Sucursal
from apps.clientes.models import Cliente
from apps.ventas.models import Venta, DetalleVenta
from apps.ventas.services import VentasService
from apps.seguridad.permissions import TienePermiso, permisos_efectivos
import uuid

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


class OrdenTrabajoViewSet(viewsets.ModelViewSet):
    def get_permissions(self):
        if self.action == 'aprobar_servicios':
            return [TienePermiso("ORDENES_TRABAJO.APROBAR")]
        if self.action == 'finalizar_orden':
            return [TienePermiso("ORDENES_TRABAJO.FINALIZAR")]
        if self.action == 'enviar_a_pos':
            return [TienePermiso("VENTAS.POS.CREAR")]
        if self.action == 'anular':
            return [TienePermiso("ORDENES_TRABAJO.CAMBIAR_ESTADO")]
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("ORDENES_TRABAJO.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    def update(self, request, *args, **kwargs):
        # Extender el vencimiento de la cotización es una acción sensible
        # aparte de "editar la orden" en general (motivo, kilometraje, etc.):
        # exige su propio permiso aunque ambas viajen por el mismo PATCH
        # genérico del ViewSet. Ocultar el botón en el frontend no alcanza,
        # ya que este mismo endpoint acepta el campo sin distinción.
        if "fecha_vencimiento_cotizacion" in request.data:
            if "ORDENES_TRABAJO.EXTENDER_VENCIMIENTO" not in permisos_efectivos(request.user):
                raise PermissionDenied(
                    "No tienes permiso para extender el vencimiento de la cotización."
                )
        # Prometer/editar la fecha de entrega al cliente es igual de sensible,
        # pero con su propio permiso (a diferencia del vencimiento, el
        # Mecánico sí lo conserva por defecto).
        if "fecha_estimada_entrega" in request.data:
            if "ORDENES_TRABAJO.PROMETER_ENTREGA" not in permisos_efectivos(request.user):
                raise PermissionDenied(
                    "No tienes permiso para prometer/editar la fecha de entrega."
                )
        return super().update(request, *args, **kwargs)

    def get_queryset(self):
        # Evitar N+1 en las consultas, usando select_related para FK y prefetch para M:N
        queryset = OrdenTrabajo.objects.select_related(
            'vehiculo', 'recepcionista', 'mecanico_asignado', 'cliente'
        ).prefetch_related('vehiculo__clientes')
        
        if self.action == 'retrieve':
            queryset = queryset.prefetch_related(
                'hallazgos', 'servicios', 
                Prefetch('repuestos', queryset=OrdenRepuesto.objects.select_related('repuesto'))
            )
            
        # Filtrado por rol (Mecánico solo ve las suyas)
        user = self.request.user
        if hasattr(user, 'usuario_roles'):
            is_mecanico = user.usuario_roles.filter(id_rol__codigo='TÉCNICO_AUTOMOTRIZ', estado=True).exists()
            is_admin = user.usuario_roles.filter(id_rol__codigo='ADMINISTRADOR', estado=True).exists()
            
            if is_mecanico and not is_admin:
                queryset = queryset.filter(mecanico_asignado=user)

        params = self.request.query_params
        estado = params.get('estado')
        mecanico_asignado = params.get('mecanico_asignado')
        cliente = params.get('cliente')
        placa = params.get('placa')
        fecha_desde = params.get('fecha_desde')
        fecha_hasta = params.get('fecha_hasta')

        if estado:
            queryset = queryset.filter(estado=estado)
        if mecanico_asignado:
            queryset = queryset.filter(mecanico_asignado_id=mecanico_asignado)
        if cliente:
            queryset = queryset.filter(cliente_id=cliente)
        if placa:
            queryset = queryset.filter(vehiculo__placa__icontains=placa)
        if fecha_desde:
            queryset = queryset.filter(fecha_ingreso__date__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(fecha_ingreso__date__lte=fecha_hasta)

        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return OrdenTrabajoListSerializer
        return OrdenTrabajoDetailSerializer

    def perform_create(self, serializer):
        # Generar numero de OT unico secuencial
        last_ot = OrdenTrabajo.objects.order_by('-id').first()
        next_num = 1 if not last_ot else last_ot.id + 1
        numero_ot = f"{next_num:06d}"
        
        orden = serializer.save(
            recepcionista=self.request.user,
            numero=numero_ot
        )
        
        if orden.cliente:
            orden.vehiculo.clientes.add(orden.cliente)
        
        if orden.kilometraje_ingreso is not None:
            vehiculo = orden.vehiculo
            vehiculo.kilometraje_actual = orden.kilometraje_ingreso
            vehiculo.save(update_fields=['kilometraje_actual'])
            
        # Generar primer historial de estado
        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=orden.estado,
            usuario=self.request.user
        )

    # Transiciones de estado permitidas fuera de las acciones dedicadas (aprobar_servicios,
    # finalizar_orden, enviar_a_pos, anular). Cualquier otro cambio de estado vía PATCH/PUT
    # directo se rechaza para evitar saltarse las validaciones de negocio de esas acciones.
    TRANSICIONES_MANUALES_PERMITIDAS = {
        (OrdenTrabajo.Estado.RECEPCIONADO, OrdenTrabajo.Estado.INSPECCION),
        (OrdenTrabajo.Estado.INSPECCION, OrdenTrabajo.Estado.ESPERANDO_APROBACION),
        # Atajo: si en recepción ya se sabe qué se necesita (ver "Generar Cotización
        # Directa" en el frontend), se puede cotizar sin pasar por INSPECCION.
        (OrdenTrabajo.Estado.RECEPCIONADO, OrdenTrabajo.Estado.ESPERANDO_APROBACION),
    }

    def perform_update(self, serializer):
        orden_anterior = self.get_object()
        estado_anterior = orden_anterior.estado
        nuevo_estado = serializer.validated_data.get('estado', estado_anterior)

        if nuevo_estado != estado_anterior and (estado_anterior, nuevo_estado) not in self.TRANSICIONES_MANUALES_PERMITIDAS:
            raise ValidationError(
                f"No se puede cambiar el estado de '{estado_anterior}' a '{nuevo_estado}' directamente. "
                "Usa la acción correspondiente (aprobar, finalizar, enviar a POS o anular)."
            )

        # Enviar la cotización al cliente (→ ESPERANDO_APROBACION) es una responsabilidad
        # distinta de editar la orden (agregar hallazgos/servicios/repuestos); requiere
        # ORDENES_TRABAJO.APROBAR aunque el usuario tenga EDITAR.
        if nuevo_estado == OrdenTrabajo.Estado.ESPERANDO_APROBACION and estado_anterior != nuevo_estado:
            if not self.request.user.is_superuser and 'ORDENES_TRABAJO.APROBAR' not in permisos_efectivos(self.request.user):
                raise PermissionDenied("No tiene permiso para enviar la cotización al cliente.")

        orden = serializer.save()
        
        # Guardar en el historial si el estado cambió
        if estado_anterior != orden.estado:
            OrdenHistorialEstado.objects.create(
                orden=orden,
                estado=orden.estado,
                usuario=self.request.user
            )
        
        # Transición a ESPERANDO_APROBACION: Calcular fecha de vencimiento si no tiene o si recién entra al estado
        if estado_anterior != OrdenTrabajo.Estado.ESPERANDO_APROBACION and orden.estado == OrdenTrabajo.Estado.ESPERANDO_APROBACION:
            from apps.seguridad.models import Empresa
            empresa = Empresa.objects.first()
            dias = empresa.dias_validez_cotizacion if empresa else 15
            orden.fecha_vencimiento_cotizacion = timezone.now() + timezone.timedelta(days=dias)
            orden.save(update_fields=['fecha_vencimiento_cotizacion'])

    @action(detail=True, methods=['post'])
    def aprobar_servicios(self, request, pk=None):
        """Endpoint para aprobar masivamente servicios y repuestos luego que el cliente revisa."""
        orden = self.get_object()
        
        # Validar vencimiento de la cotización
        if orden.fecha_vencimiento_cotizacion and timezone.now() > orden.fecha_vencimiento_cotizacion:
            return Response(
                {'error': 'La cotización ha expirado. Por favor, actualice la fecha de vencimiento en los detalles de la orden para proceder.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validar payload
        servicios_ids = request.data.get('servicios_aprobados', [])
        repuestos_ids = request.data.get('repuestos_aprobados', [])

        try:
            aprobar_cotizacion_orden(
                orden,
                servicios_ids,
                repuestos_ids,
                usuario=request.user,
            )
        except ValidationError as exc:
            detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
            return Response({'error': detail}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'ok', 'message': 'Aprobacion registrada correctamente.'})
        
    @action(detail=True, methods=['post'])
    def finalizar_orden(self, request, pk=None):
        orden = self.get_object()
        
        if orden.estado != OrdenTrabajo.Estado.APROBADO:
            return Response({'error': 'La orden debe estar en estado APROBADO para finalizarse.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Validar servicios aprobados completados
        servicios_aprobados = orden.servicios.filter(aprobado_cliente=True)
        if servicios_aprobados.filter(completado=False).exists():
            return Response({'error': 'Todos los servicios aprobados deben estar marcados como Terminados.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Validar repuestos aprobados instalados
        repuestos_aprobados = orden.repuestos.filter(aprobado_cliente=True)
        if repuestos_aprobados.filter(instalado=False).exists():
            return Response({'error': 'Todos los repuestos aprobados deben estar marcados como Instalados.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Cambiar estado
        orden.estado = OrdenTrabajo.Estado.FINALIZADO
        orden.fecha_finalizacion = timezone.now()
        orden.save(update_fields=['estado', 'fecha_finalizacion'])

        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=orden.estado,
            usuario=request.user,
        )

        return Response({'status': 'ok', 'message': 'Orden finalizada correctamente.', 'estado': orden.estado})

    @action(detail=True, methods=['post'])
    def enviar_a_pos(self, request, pk=None):
        orden = self.get_object()
        
        if orden.estado != OrdenTrabajo.Estado.FINALIZADO:
            return Response({'error': 'La orden debe estar en estado FINALIZADO para enviarse a POS.'}, status=status.HTTP_400_BAD_REQUEST)
            
        if not orden.cliente:
            return Response({'error': 'La orden no tiene un cliente asignado. Asigne un cliente en los detalles de la orden antes de cobrar.'}, status=status.HTTP_400_BAD_REQUEST)
            
        sucursal_id = request.data.get('sucursal_id')
        if not sucursal_id:
            raise ValidationError("Debe especificar la sucursal (sucursal_id) para enviar la orden al POS.")


        venta_existente = Venta.objects.filter(
            ticket_kiosko__startswith=f"OT-{orden.id}-",
            estado=Venta.Estado.PRE_VENTA
        ).first()
        
        if venta_existente:
            return Response({
                'status': 'ok', 
                'message': 'Ya existe un ticket en POS.', 
                'venta_id': venta_existente.id,
                'ticket': venta_existente.ticket_kiosko,
                'estado_orden': orden.estado
            })
            
        with transaction.atomic():
            ticket_code = f"OT-{orden.id}-{str(uuid.uuid4())[:4].upper()}"
            venta = Venta.objects.create(
                cliente=orden.cliente,
                vehiculo=orden.vehiculo,
                sucursal_id=sucursal_id,
                estado=Venta.Estado.PRE_VENTA,
                ticket_kiosko=ticket_code,
                kilometraje=orden.kilometraje_ingreso
            )
            
            subtotal_acumulado = 0
            
            # Repuestos
            # select_related('repuesto') evita una query extra por línea al
            # leer rep.repuesto.precio_compra para el costo histórico.
            for rep in orden.repuestos.filter(aprobado_cliente=True, instalado=True).select_related('repuesto'):
                subtotal_linea = rep.cantidad * rep.precio_unitario
                DetalleVenta.objects.create(
                    venta=venta,
                    repuesto=rep.repuesto,
                    cantidad=rep.cantidad,
                    precio_unitario=rep.precio_unitario,
                    costo_unitario=rep.repuesto.precio_compra,
                    subtotal_linea=subtotal_linea
                )
                subtotal_acumulado += subtotal_linea
                
            # Servicios
            for serv in orden.servicios.filter(aprobado_cliente=True, completado=True):
                subtotal_linea = serv.precio_estimado
                # Cantidad = 1, usando descripcion_servicio
                DetalleVenta.objects.create(
                    venta=venta,
                    descripcion_servicio=serv.descripcion,
                    cantidad=1,
                    precio_unitario=serv.precio_estimado,
                    subtotal_linea=subtotal_linea
                )
                subtotal_acumulado += subtotal_linea
                
            venta.total = subtotal_acumulado
            venta.subtotal, venta.igv = VentasService.descomponer_total_con_impuesto(venta.total)
            venta.save()
            
            # NOTA: Ya no cambiamos a FACTURADO aquí, se hará cuando se pague en POS.
            
        return Response({
            'status': 'ok',
            'message': 'Enviado a POS correctamente.',
            'venta_id': venta.id,
            'ticket': venta.ticket_kiosko,
            'estado_orden': orden.estado
        })

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def anular(self, request, pk=None):
        """
        Anula una orden de trabajo: libera las reservas de stock de sus repuestos
        aprobados aún no instalados y registra el motivo en el historial de estados.
        No revierte repuestos ya instalados (ese stock ya salió físicamente).
        """
        orden = self.get_object()

        if orden.estado in (OrdenTrabajo.Estado.FACTURADO, OrdenTrabajo.Estado.CANCELADO):
            raise ValidationError(f"No se puede anular una orden en estado '{orden.estado}'.")

        venta_existente = Venta.objects.filter(
            ticket_kiosko__startswith=f"OT-{orden.id}-",
            estado=Venta.Estado.PRE_VENTA
        ).exists()
        if venta_existente:
            raise ValidationError(
                "No se puede anular: ya existe un ticket en el Punto de Venta para esta orden. "
                "Cancele ese ticket antes de anular la orden."
            )

        if orden.repuestos.filter(instalado=True).exists():
            raise ValidationError(
                "No se puede anular: esta orden tiene repuestos ya instalados (ese stock ya salió del almacén)."
            )

        motivo = (request.data.get('motivo') or '').strip()
        if not motivo:
            raise ValidationError("Debe indicar el motivo de la anulación.")

        categoria = request.data.get('categoria') or None
        categorias_validas = OrdenHistorialEstado.MotivoCategoria.values
        if categoria and categoria not in categorias_validas:
            raise ValidationError(f"Categoría de motivo inválida: '{categoria}'.")

        for orp in orden.repuestos.filter(aprobado_cliente=True, instalado=False):
            stock_record = InventarioStock.objects.filter(repuesto=orp.repuesto).first()
            if stock_record:
                stock_record.stock_disponible += orp.cantidad
                stock_record.stock_reservado -= orp.cantidad
                stock_record.save()

                MovimientoInventario.objects.create(
                    repuesto=orp.repuesto,
                    ubicacion=stock_record.ubicacion,
                    tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                    cantidad=orp.cantidad,
                    stock_resultante=stock_record.stock_disponible,
                    motivo=f"Liberación de reserva por anulación de OT-{orden.numero}",
                    usuario=request.user,
                    referencia_id=orden.id,
                    referencia_tipo='OT_ANULACION'
                )

        orden.estado = OrdenTrabajo.Estado.CANCELADO
        orden.save(update_fields=['estado'])

        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=orden.estado,
            usuario=request.user,
            observaciones=motivo,
            motivo_categoria=categoria
        )

        serializer = self.get_serializer(orden)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def generar_pdf(self, request, pk=None):
        orden = self.get_object()

        # Calcular totales
        total_servicios = sum(s.precio_estimado for s in orden.servicios.all())
        total_repuestos = sum(r.cantidad * r.precio_unitario for r in orden.repuestos.all())
        total_general = total_servicios + total_repuestos

        from apps.seguridad.models import CuentaBancaria, Empresa
        cuentas = CuentaBancaria.objects.filter(estado=True)

        # Configuración real de la empresa (la misma que ve el usuario en
        # Configuración > Empresa), no datos de ejemplo fijos.
        empresa, _ = Empresa.objects.get_or_create(id=1, defaults={
            "razon_social": "Mi Empresa",
            "ruc": "00000000000",
            "direccion": "Dirección no configurada"
        })
        empresa_logo_data_uri = None
        if empresa.logo:
            import base64
            import mimetypes
            try:
                # xhtml2pdf (a diferencia del navegador) no resuelve de forma
                # confiable una ruta de archivo o una URL del propio servidor
                # para cargar la imagen del logo. La forma robusta es incrustar
                # los bytes de la imagen directamente en el HTML como base64,
                # así el logo queda embebido en el PDF sin depender de ninguna
                # ruta externa.
                with empresa.logo.open('rb') as f:
                    logo_bytes = f.read()
                mime_type = mimetypes.guess_type(empresa.logo.name)[0] or 'image/png'
                empresa_logo_data_uri = f"data:{mime_type};base64,{base64.b64encode(logo_bytes).decode('ascii')}"
            except (OSError, ValueError) as e:
                logger.error(f"No se pudo incrustar el logo de la empresa en el PDF: {e}")
                empresa_logo_data_uri = None

        # Número de cotización: se genera una sola vez con la serie PROFORMA
        # (Configuración > Series Internas) de la sucursal indicada, y se
        # reutiliza en reimpresiones. Si aún no hay serie configurada para esa
        # sucursal, se sigue mostrando el número de la OT (comportamiento previo).
        if not orden.numero_cotizacion:
            sucursal_id = request.query_params.get('sucursal_id')
            if sucursal_id:
                from apps.ventas.models import SerieDocumentoInterno
                with transaction.atomic():
                    serie = SerieDocumentoInterno.objects.select_for_update().filter(
                        sucursal_id=sucursal_id,
                        tipo_documento=SerieDocumentoInterno.TipoDocumento.PROFORMA,
                        estado=True
                    ).first()
                    if serie:
                        numero = serie.generar_siguiente_correlativo()
                        serie.correlativo_actual += 1
                        serie.save(update_fields=['correlativo_actual'])
                        orden.numero_cotizacion = numero
                        orden.save(update_fields=['numero_cotizacion'])

        numero_documento = orden.numero_cotizacion or orden.numero

        # Configurar contexto
        context = {
            'orden': orden,
            'numero_documento': numero_documento,
            'total_servicios': total_servicios,
            'total_repuestos': total_repuestos,
            'total_general': total_general,
            'cuentas_bancarias': cuentas,
            'empresa': empresa,
            'empresa_logo_data_uri': empresa_logo_data_uri,
        }

        from django.template.loader import render_to_string
        from django.http import HttpResponse
        from xhtml2pdf import pisa

        html_string = render_to_string('taller/proforma_pdf.html', context)

        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="cotizacion_{numero_documento}.pdf"'

        pisa_status = pisa.CreatePDF(
            html_string, dest=response
        )

        if pisa_status.err:
            return HttpResponse('Error generando PDF', status=500)

        return response

class HallazgoViewSet(viewsets.ModelViewSet):
    queryset = Hallazgo.objects.all()
    serializer_class = HallazgoSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    def perform_create(self, serializer):
        serializer.save(registrado_por=self.request.user)

class OrdenServicioViewSet(viewsets.ModelViewSet):
    queryset = OrdenServicio.objects.all()
    serializer_class = OrdenServicioSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    @action(detail=True, methods=['patch'])
    def marcar_completado(self, request, pk=None):
        servicio = self.get_object()
        servicio.completado = not servicio.completado
        servicio.save(update_fields=['completado'])
        return Response({'status': 'ok', 'completado': servicio.completado})

class OrdenRepuestoViewSet(viewsets.ModelViewSet):
    queryset = OrdenRepuesto.objects.select_related('repuesto')
    serializer_class = OrdenRepuestoSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    @action(detail=True, methods=['patch'])
    @transaction.atomic
    def marcar_instalado(self, request, pk=None):
        """
        Alterna 'instalado'. Es simétrico: activar convierte la reserva en
        salida definitiva (Kardex SALIDA); desactivar devuelve esa reserva
        (Kardex RESERVA positivo). Antes solo activar tenía efecto, así que
        activar/desactivar repetidamente descontaba stock de más (bug real
        ya detectado en producción, corregido aquí).
        """
        repuesto_orden = self.get_object()
        orden = repuesto_orden.orden

        if orden.estado in (OrdenTrabajo.Estado.CANCELADO, OrdenTrabajo.Estado.FACTURADO):
            raise ValidationError(
                f"No se puede modificar la instalación de un repuesto en una orden '{orden.estado}'."
            )

        nuevo_valor = not repuesto_orden.instalado

        if nuevo_valor and not repuesto_orden.aprobado_cliente:
            raise ValidationError("No se puede instalar un repuesto que no ha sido aprobado.")

        stock_record = InventarioStock.objects.select_for_update().filter(repuesto=repuesto_orden.repuesto).first()

        if stock_record:
            if nuevo_valor:
                # Instalar: convertir la reserva en salida definitiva.
                stock_record.stock_reservado -= repuesto_orden.cantidad
                stock_record.save()
                MovimientoInventario.objects.create(
                    repuesto=repuesto_orden.repuesto,
                    ubicacion=stock_record.ubicacion,
                    tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA,
                    cantidad=-repuesto_orden.cantidad,
                    stock_resultante=stock_record.stock_disponible,
                    motivo=f"Instalación en OT-{orden.numero}",
                    usuario=request.user,
                    referencia_id=orden.id,
                    referencia_tipo='OT'
                )
            else:
                # Revertir instalación: la reserva vuelve a estar activa.
                stock_record.stock_reservado += repuesto_orden.cantidad
                stock_record.save()
                MovimientoInventario.objects.create(
                    repuesto=repuesto_orden.repuesto,
                    ubicacion=stock_record.ubicacion,
                    tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                    cantidad=repuesto_orden.cantidad,
                    stock_resultante=stock_record.stock_disponible,
                    motivo=f"Reversión de instalación en OT-{orden.numero}",
                    usuario=request.user,
                    referencia_id=orden.id,
                    referencia_tipo='OT'
                )

        repuesto_orden.instalado = nuevo_valor
        repuesto_orden.save(update_fields=['instalado'])

        return Response({'status': 'ok', 'instalado': repuesto_orden.instalado})

class PlantillaPreventivaViewSet(viewsets.ModelViewSet):
    queryset = PlantillaPreventiva.objects.all()
    serializer_class = PlantillaPreventivaSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("PLANTILLAS_TALLER.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("PLANTILLAS_TALLER.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("PLANTILLAS_TALLER.ELIMINAR")]
        return [TienePermiso("PLANTILLAS_TALLER.EDITAR")]


class PlantillaCorrectivaViewSet(viewsets.ModelViewSet):
    """
    CRUD de plantillas correctivas. Reutiliza los mismos permisos de
    PLANTILLAS_TALLER para no requerir nuevos perfiles de acceso.
    Filtro ?activo=true disponible para que NuevaOrdenPage solo cargue
    las plantillas visibles en recepción.
    """
    serializer_class = PlantillaCorrectivaSerializer

    def get_queryset(self):
        qs = PlantillaCorrectiva.objects.all()
        activo = self.request.query_params.get('activo')
        if activo is not None:
            qs = qs.filter(activo=(activo.lower() == 'true'))
        return qs

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("PLANTILLAS_TALLER.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("PLANTILLAS_TALLER.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("PLANTILLAS_TALLER.ELIMINAR")]
        return [TienePermiso("PLANTILLAS_TALLER.EDITAR")]

class ConsultaVehiculoPublicaView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        placa = request.data.get('placa')
        dni = request.data.get('dni')
        sucursal_id = request.data.get('sucursal_id')
        
        if not placa or not dni:
            return Response({'error': 'Placa y DNI son requeridos'}, status=status.HTTP_400_BAD_REQUEST)
            
        vehiculo = Vehiculo.objects.filter(placa__iexact=placa).first()
        if not vehiculo:
            return Response({'error': 'Vehículo no encontrado o credenciales incorrectas'}, status=status.HTTP_404_NOT_FOUND)
            
        # Buscar la última orden activa (que no esté FACTURADA ni CANCELADA)
        ordenes_qs = (
            OrdenTrabajo.objects
            .filter(vehiculo=vehiculo)
            .exclude(estado__in=['FACTURADO', 'CANCELADO'])
            .prefetch_related('hallazgos')
        )
        if sucursal_id:
            ordenes_qs = ordenes_qs.filter(
                recepcionista__sucursales_asignadas__sucursal_id=sucursal_id,
                recepcionista__sucursales_asignadas__estado=True,
            ).distinct()
        orden = ordenes_qs.order_by('-fecha_ingreso').first()
        
        # Validar credenciales: El DNI debe ser del dueño (vehiculo.clientes) o del cliente que dejó la orden activa (orden.cliente.dni)
        es_propietario = vehiculo.clientes.filter(dni=dni).exists()
        es_cliente_orden = orden and orden.cliente and orden.cliente.dni == dni
        
        if not es_propietario and not es_cliente_orden:
            return Response({'error': 'Vehículo no encontrado o credenciales incorrectas'}, status=status.HTTP_404_NOT_FOUND)
            
        
        # Helper para obtener el nombre del cliente
        def get_nombre_cliente(c):
            if c:
                return f"{c.nombres} {c.apellidos}".strip()
            return 'Cliente'

        vehiculo_data = {
            'placa': vehiculo.placa,
            'marca': vehiculo.marca,
            'modelo': vehiculo.modelo,
            'cliente': get_nombre_cliente(orden.cliente) if orden and orden.cliente else get_nombre_cliente(vehiculo.clientes.first()) if vehiculo.clientes.exists() else 'Cliente',
        }
        
        if not orden:
            return Response({
                'vehiculo': vehiculo_data,
                'has_active_order': False,
                'message': 'Su vehículo no tiene reparaciones activas'
            })
            
        cotizacion_pendiente = orden.estado == OrdenTrabajo.Estado.ESPERANDO_APROBACION

        # Preparar resumen de la orden
        servicios_qs = orden.servicios.all() if cotizacion_pendiente else orden.servicios.filter(aprobado_cliente=True)
        servicios = [
            {
                'id': s.id,
                'descripcion': s.descripcion,
                'completado': s.completado,
                'precio': s.precio_estimado,
                'aprobado_cliente': s.aprobado_cliente
            }
            for s in servicios_qs
        ]

        repuestos_qs = orden.repuestos.select_related('repuesto')
        if not cotizacion_pendiente:
            repuestos_qs = repuestos_qs.filter(aprobado_cliente=True)
        repuestos = [
            {
                'id': r.id,
                'descripcion': r.repuesto.nombre if r.repuesto else 'Repuesto',
                'instalado': r.instalado,
                'precio': r.precio_unitario,
                'cantidad': r.cantidad,
                'aprobado_cliente': r.aprobado_cliente
            }
            for r in repuestos_qs
        ]

        hallazgos = [
            {
                'id': h.id,
                'descripcion': h.descripcion,
                'severidad': h.severidad,
                'fecha_registro': h.fecha_registro,
            }
            for h in orden.hallazgos.all().order_by('-fecha_registro')
        ]
        
        total_estimado = sum([float(s['precio']) for s in servicios]) + sum([float(r['precio']) * float(r['cantidad']) for r in repuestos])
        cotizacion_vencida = bool(orden.fecha_vencimiento_cotizacion and timezone.now() > orden.fecha_vencimiento_cotizacion)
        
        return Response({
            'vehiculo': vehiculo_data,
            'has_active_order': True,
            'orden': {
                'id': orden.id,
                'numero': orden.numero,
                'estado': orden.estado,
                'fecha_ingreso': orden.fecha_ingreso,
                'fecha_vencimiento_cotizacion': orden.fecha_vencimiento_cotizacion,
                'cotizacion_pendiente': cotizacion_pendiente,
                'cotizacion_vencida': cotizacion_vencida,
                'hallazgos': hallazgos,
                'servicios': servicios,
                'repuestos': repuestos,
                'total_estimado': total_estimado
            }
        })


class AprobarCotizacionPublicaView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        placa = request.data.get('placa')
        dni = request.data.get('dni')
        sucursal_id = request.data.get('sucursal_id')

        if not placa or not dni:
            return Response({'error': 'Placa y DNI son requeridos'}, status=status.HTTP_400_BAD_REQUEST)

        vehiculo = Vehiculo.objects.filter(placa__iexact=placa).first()
        if not vehiculo:
            return Response({'error': 'Vehiculo no encontrado o credenciales incorrectas'}, status=status.HTTP_404_NOT_FOUND)

        ordenes_qs = (
            OrdenTrabajo.objects
            .filter(vehiculo=vehiculo)
            .exclude(estado__in=['FACTURADO', 'CANCELADO'])
        )
        if sucursal_id:
            ordenes_qs = ordenes_qs.filter(
                recepcionista__sucursales_asignadas__sucursal_id=sucursal_id,
                recepcionista__sucursales_asignadas__estado=True,
            ).distinct()
        orden = ordenes_qs.order_by('-fecha_ingreso').first()

        es_propietario = vehiculo.clientes.filter(dni=dni).exists()
        es_cliente_orden = orden and orden.cliente and orden.cliente.dni == dni

        if not es_propietario and not es_cliente_orden:
            return Response({'error': 'Vehiculo no encontrado o credenciales incorrectas'}, status=status.HTTP_404_NOT_FOUND)
        if not orden:
            return Response({'error': 'No hay una orden activa para aprobar.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            aprobar_cotizacion_orden(
                orden,
                request.data.get('servicios_aprobados', []),
                request.data.get('repuestos_aprobados', []),
                usuario=None,
                observaciones='Aprobado por el cliente desde Estado de Vehiculo.',
                exigir_esperando_aprobacion=True,
                exigir_seleccion=True,
            )
        except ValidationError as exc:
            detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
            return Response({'error': detail}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'status': 'ok',
            'message': 'Cotizacion aprobada correctamente.',
            'estado': OrdenTrabajo.Estado.APROBADO,
        })
