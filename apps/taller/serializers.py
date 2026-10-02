from rest_framework import serializers
from django.utils import timezone
from .models import (
    BloqueoAgendaSucursal, Cita, CitaHistorial, ConfiguracionAgendaSucursal, HorarioAgendaSucursal,
    ListaEsperaCita,
    OrdenTrabajo, Hallazgo, OrdenServicio, OrdenRepuesto, PlantillaPreventiva,
    TipoServicio, OrdenHistorialEstado, PlantillaCorrectiva
)
from apps.vehiculos.serializers import VehiculoSerializer
from apps.inventario.models import InventarioStock
from apps.inventario.models import Sucursal
from apps.inventario.serializers import SucursalSerializer
from apps.inventario.serializers import RepuestoSerializer
from apps.clientes.serializers import ClienteSerializer

class TipoServicioSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoServicio
        fields = '__all__'


class HorarioAgendaSucursalSerializer(serializers.ModelSerializer):
    dia_semana_display = serializers.CharField(source='get_dia_semana_display', read_only=True)

    class Meta:
        model = HorarioAgendaSucursal
        fields = ['id', 'dia_semana', 'dia_semana_display', 'hora_inicio', 'hora_fin', 'cerrado']


class ConfiguracionAgendaSucursalSerializer(serializers.ModelSerializer):
    sucursal_detalle = SucursalSerializer(source='sucursal', read_only=True)
    horarios = HorarioAgendaSucursalSerializer(many=True)

    class Meta:
        model = ConfiguracionAgendaSucursal
        fields = ['id', 'sucursal', 'sucursal_detalle', 'intervalo_minutos', 'capacidad_simultanea', 'activo', 'actualizado_en', 'horarios']

    def validate(self, attrs):
        intervalo = attrs.get('intervalo_minutos', getattr(self.instance, 'intervalo_minutos', 30))
        capacidad = attrs.get('capacidad_simultanea', getattr(self.instance, 'capacidad_simultanea', 3))
        if intervalo <= 0:
            raise serializers.ValidationError("El intervalo de agenda debe ser mayor a cero.")
        if capacidad <= 0:
            raise serializers.ValidationError("La capacidad simultanea debe ser mayor a cero.")
        return attrs

    def update(self, instance, validated_data):
        horarios_data = validated_data.pop('horarios', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if horarios_data is not None:
            for horario_data in horarios_data:
                dia_semana = horario_data.get('dia_semana')
                if dia_semana is None:
                    continue
                HorarioAgendaSucursal.objects.update_or_create(
                    configuracion=instance,
                    dia_semana=dia_semana,
                    defaults=horario_data,
                )
        return instance


class BloqueoAgendaSucursalSerializer(serializers.ModelSerializer):
    sucursal_detalle = SucursalSerializer(source='sucursal', read_only=True)
    creado_por_nombre = serializers.CharField(source='creado_por.nombre_completo', read_only=True)

    class Meta:
        model = BloqueoAgendaSucursal
        fields = ['id', 'sucursal', 'sucursal_detalle', 'fecha_inicio', 'fecha_fin', 'motivo', 'activo', 'creado_por', 'creado_por_nombre', 'fecha_creacion']
        read_only_fields = ['creado_por', 'fecha_creacion']

    def validate(self, attrs):
        fecha_inicio = attrs.get('fecha_inicio') or getattr(self.instance, 'fecha_inicio', None)
        fecha_fin = attrs.get('fecha_fin') or getattr(self.instance, 'fecha_fin', None)
        if fecha_inicio and fecha_fin and fecha_fin <= fecha_inicio:
            raise serializers.ValidationError("La fecha de fin del bloqueo debe ser posterior al inicio.")
        return attrs


class CitaHistorialSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.CharField(source='usuario.nombre_completo', read_only=True)
    accion_display = serializers.CharField(source='get_accion_display', read_only=True)
    estado_anterior_display = serializers.CharField(source='get_estado_anterior_display', read_only=True)
    estado_nuevo_display = serializers.CharField(source='get_estado_nuevo_display', read_only=True)

    class Meta:
        model = CitaHistorial
        fields = [
            'id', 'cita', 'accion', 'accion_display', 'estado_anterior',
            'estado_anterior_display', 'estado_nuevo', 'estado_nuevo_display',
            'fecha_inicio_anterior', 'fecha_inicio_nueva', 'observacion',
            'usuario', 'usuario_nombre', 'fecha'
        ]
        read_only_fields = fields


class ListaEsperaCitaSerializer(serializers.ModelSerializer):
    sucursal_detalle = SucursalSerializer(source='sucursal', read_only=True)
    tipo_servicio_detalle = TipoServicioSerializer(source='tipo_servicio', read_only=True)
    cliente_detalle = ClienteSerializer(source='cliente', read_only=True)
    vehiculo_detalle = VehiculoSerializer(source='vehiculo', read_only=True)
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)

    class Meta:
        model = ListaEsperaCita
        fields = [
            'id', 'sucursal', 'sucursal_detalle', 'tipo_servicio', 'tipo_servicio_detalle',
            'cliente', 'cliente_detalle', 'vehiculo', 'vehiculo_detalle', 'documento',
            'nombres', 'apellidos', 'telefono', 'email', 'placa', 'marca', 'modelo',
            'fecha_preferida', 'hora_preferida', 'duracion_minutos', 'motivo',
            'estado', 'estado_display', 'cita_convertida', 'observaciones_internas',
            'creado_desde_portal', 'creado_por', 'fecha_creacion', 'fecha_actualizacion'
        ]
        read_only_fields = ['cliente', 'vehiculo', 'cita_convertida', 'creado_por', 'fecha_creacion', 'fecha_actualizacion']

    def validate(self, attrs):
        if attrs.get('duracion_minutos', getattr(self.instance, 'duracion_minutos', 60)) <= 0:
            raise serializers.ValidationError("La duracion debe ser mayor a cero.")
        if attrs.get('fecha_preferida') and attrs['fecha_preferida'] < timezone.localdate():
            raise serializers.ValidationError("La fecha preferida no puede estar en el pasado.")
        return attrs


class ReservaPublicaCitaSerializer(serializers.Serializer):
    sucursal = serializers.PrimaryKeyRelatedField(queryset=Sucursal.objects.filter(estado=True))
    tipo_servicio = serializers.PrimaryKeyRelatedField(queryset=TipoServicio.objects.filter(estado=True), required=False, allow_null=True)
    fecha_inicio = serializers.DateTimeField()
    duracion_minutos = serializers.IntegerField(min_value=1, default=60)
    documento = serializers.CharField(max_length=15)
    nombres = serializers.CharField(max_length=150)
    apellidos = serializers.CharField(max_length=150, required=False, allow_blank=True)
    telefono = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    placa = serializers.CharField(max_length=15)
    marca = serializers.CharField(max_length=100, required=False, allow_blank=True)
    modelo = serializers.CharField(max_length=100, required=False, allow_blank=True)
    motivo = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    lista_espera = serializers.BooleanField(default=False)

    def validate_placa(self, value):
        return value.strip().replace('-', '').replace(' ', '').upper()

    def validate_documento(self, value):
        return value.strip()

    def validate(self, attrs):
        fecha_local = timezone.localtime(attrs['fecha_inicio']).date()
        if fecha_local < timezone.localdate():
            raise serializers.ValidationError("La fecha preferida no puede estar en el pasado.")
        if not attrs.get('lista_espera') and attrs['fecha_inicio'] <= timezone.now():
            raise serializers.ValidationError("La fecha de la cita debe ser futura.")
        return attrs


class CitaSerializer(serializers.ModelSerializer):
    cliente_detalle = ClienteSerializer(source='cliente', read_only=True)
    vehiculo_detalle = VehiculoSerializer(source='vehiculo', read_only=True)
    sucursal_detalle = SucursalSerializer(source='sucursal', read_only=True)
    tipo_servicio_detalle = TipoServicioSerializer(source='tipo_servicio', read_only=True)
    mecanico_nombre = serializers.CharField(source='mecanico_preferido.nombre_completo', read_only=True)
    asesor_nombre = serializers.CharField(source='asesor.nombre_completo', read_only=True)
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    origen_display = serializers.CharField(source='get_origen_display', read_only=True)
    orden_numero = serializers.CharField(source='orden_trabajo.numero', read_only=True)

    class Meta:
        model = Cita
        fields = [
            'id', 'numero', 'cliente', 'cliente_detalle', 'vehiculo', 'vehiculo_detalle',
            'sucursal', 'sucursal_detalle', 'tipo_servicio', 'tipo_servicio_detalle',
            'fecha_inicio', 'fecha_fin', 'duracion_minutos', 'origen', 'origen_display',
            'estado', 'estado_display', 'mecanico_preferido', 'mecanico_nombre',
            'asesor', 'asesor_nombre', 'motivo', 'observaciones_cliente',
            'observaciones_internas', 'kilometraje_estimado', 'orden_trabajo',
            'orden_numero', 'creado_por', 'fecha_creacion', 'fecha_actualizacion'
        ]
        read_only_fields = ['numero', 'asesor', 'creado_por', 'orden_trabajo']

    def validate(self, attrs):
        fecha_inicio = attrs.get('fecha_inicio') or getattr(self.instance, 'fecha_inicio', None)
        fecha_fin = attrs.get('fecha_fin') or getattr(self.instance, 'fecha_fin', None)
        duracion = attrs.get('duracion_minutos') or getattr(self.instance, 'duracion_minutos', 60)
        vehiculo = attrs.get('vehiculo') or getattr(self.instance, 'vehiculo', None)
        sucursal = attrs.get('sucursal') or getattr(self.instance, 'sucursal', None)

        if fecha_inicio and not fecha_fin:
            fecha_fin = fecha_inicio + timezone.timedelta(minutes=duracion or 60)
            attrs['fecha_fin'] = fecha_fin

        if fecha_inicio and fecha_fin and fecha_fin <= fecha_inicio:
            raise serializers.ValidationError("La fecha de fin debe ser posterior a la fecha de inicio.")

        if fecha_inicio and fecha_fin and vehiculo:
            qs = Cita.objects.filter(
                vehiculo=vehiculo,
                fecha_inicio__lt=fecha_fin,
                fecha_fin__gt=fecha_inicio,
            ).exclude(estado__in=[Cita.Estado.CANCELADA, Cita.Estado.NO_ASISTIO])
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError("El vehiculo ya tiene una cita activa en ese horario.")

        if fecha_inicio and fecha_fin and sucursal:
            config = ConfiguracionAgendaSucursal.objects.filter(sucursal=sucursal).first()
            capacidad = config.capacidad_simultanea if config else 3
            if config:
                if not config.activo:
                    raise serializers.ValidationError("La agenda de la sucursal esta inactiva.")

                inicio_local = timezone.localtime(fecha_inicio)
                fin_local = timezone.localtime(fecha_fin)
                horario = config.horarios.filter(dia_semana=inicio_local.weekday()).first()
                if not horario or horario.cerrado:
                    raise serializers.ValidationError("La sucursal no atiende citas en el dia seleccionado.")
                if inicio_local.date() != fin_local.date():
                    raise serializers.ValidationError("La cita debe iniciar y terminar dentro del mismo dia de atencion.")
                if inicio_local.time() < horario.hora_inicio or fin_local.time() > horario.hora_fin:
                    raise serializers.ValidationError("La cita esta fuera del horario configurado para la sucursal.")

                bloqueo = BloqueoAgendaSucursal.objects.filter(
                    sucursal=sucursal,
                    activo=True,
                    fecha_inicio__lt=fecha_fin,
                    fecha_fin__gt=fecha_inicio,
                ).first()
                if bloqueo:
                    raise serializers.ValidationError(f"El horario esta bloqueado: {bloqueo.motivo}")

            qs = Cita.objects.filter(
                sucursal=sucursal,
                fecha_inicio__lt=fecha_fin,
                fecha_fin__gt=fecha_inicio,
            ).exclude(estado__in=[Cita.Estado.CANCELADA, Cita.Estado.NO_ASISTIO, Cita.Estado.RECEPCIONADA])
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.count() >= capacidad:
                raise serializers.ValidationError("La sucursal ya alcanzo la capacidad sugerida de citas en ese horario.")

        return attrs

class OrdenHistorialEstadoSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.SerializerMethodField()
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)
    motivo_categoria_display = serializers.CharField(source='get_motivo_categoria_display', read_only=True)

    class Meta:
        model = OrdenHistorialEstado
        fields = ['id', 'estado', 'estado_display', 'fecha_registro', 'usuario', 'usuario_nombre', 'observaciones', 'motivo_categoria', 'motivo_categoria_display']

    def get_usuario_nombre(self, obj):
        if obj.usuario:
            return obj.usuario.nombre_completo
        return 'Portal del cliente'

class HallazgoSerializer(serializers.ModelSerializer):
    registrado_por_nombre = serializers.CharField(source='registrado_por.nombre_completo', read_only=True)

    class Meta:
        model = Hallazgo
        fields = ['id', 'orden', 'descripcion', 'severidad', 'fecha_registro', 'registrado_por', 'registrado_por_nombre']
        read_only_fields = ['registrado_por']

class OrdenServicioSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrdenServicio
        fields = ['id', 'orden', 'descripcion', 'precio_estimado', 'aprobado_cliente', 'completado', 'hallazgo_origen']

    def validate(self, attrs):
        hallazgo = attrs.get('hallazgo_origen')
        orden = attrs.get('orden') or getattr(self.instance, 'orden', None)
        if hallazgo and orden and hallazgo.orden_id != orden.id:
            raise serializers.ValidationError("El hallazgo seleccionado no pertenece a esta orden de trabajo.")
        return attrs

class OrdenRepuestoSerializer(serializers.ModelSerializer):
    repuesto_detalle = RepuestoSerializer(source='repuesto', read_only=True)

    class Meta:
        model = OrdenRepuesto
        fields = ['id', 'orden', 'repuesto', 'repuesto_detalle', 'cantidad', 'precio_unitario', 'aprobado_cliente', 'entregado_por_almacen', 'instalado']
        # aprobado_cliente/instalado solo deben cambiar vía las acciones dedicadas
        # (aprobar_servicios / marcar_instalado), que mantienen sincronizado el
        # stock reservado. Un PATCH directo a estos campos desincronizaba stock
        # y estado (bug real ya detectado y corregido).
        read_only_fields = ['aprobado_cliente', 'instalado']

    def validate(self, attrs):
        orden = attrs.get('orden') or getattr(self.instance, 'orden', None)
        repuesto = attrs.get('repuesto') or getattr(self.instance, 'repuesto', None)
        cantidad = attrs.get('cantidad') or getattr(self.instance, 'cantidad', None)

        if not orden or not repuesto or cantidad is None:
            return attrs

        if cantidad <= 0:
            raise serializers.ValidationError("La cantidad del repuesto debe ser mayor a cero.")

        stock_disponible = sum(
            item.stock_disponible
            for item in InventarioStock.objects.filter(repuesto=repuesto)
        )
        cantidad_pendiente_misma_orden = sum(
            item.cantidad
            for item in OrdenRepuesto.objects.filter(
                orden=orden,
                repuesto=repuesto,
                aprobado_cliente=False,
            ).exclude(pk=getattr(self.instance, 'pk', None))
        )
        cantidad_necesaria = cantidad + cantidad_pendiente_misma_orden

        if stock_disponible < cantidad_necesaria:
            raise serializers.ValidationError(
                f"No hay stock disponible suficiente para el repuesto '{repuesto.nombre}'. "
                f"Disponible: {stock_disponible}. Cantidad solicitada: {cantidad_necesaria}."
            )

        return attrs

class OrdenTrabajoListSerializer(serializers.ModelSerializer):
    vehiculo_placa = serializers.CharField(source='vehiculo.placa', read_only=True)
    cliente_nombre = serializers.SerializerMethodField()
    mecanico_nombre = serializers.CharField(source='mecanico_asignado.nombre_completo', read_only=True)
    tipo_servicio_detalle = TipoServicioSerializer(source='tipo_servicio', read_only=True)
    
    class Meta:
        model = OrdenTrabajo
        fields = ['id', 'numero', 'vehiculo', 'vehiculo_placa', 'cliente', 'cliente_nombre', 'estado', 'tipo_servicio', 'tipo_servicio_detalle', 'fecha_ingreso', 'mecanico_asignado', 'mecanico_nombre', 'motivo_ingreso', 'fecha_vencimiento_cotizacion', 'fecha_estimada_entrega']
        
    def get_cliente_nombre(self, obj):
        if obj.cliente:
            return obj.cliente.nombres + " " + (obj.cliente.apellidos or "")
        # Fallback to vehicle's first client for older records
        cliente = obj.vehiculo.clientes.first()
        return cliente.nombres + " " + (cliente.apellidos or "") if cliente else "Sin Cliente"

class OrdenTrabajoDetailSerializer(serializers.ModelSerializer):
    vehiculo_detalle = VehiculoSerializer(source='vehiculo', read_only=True)
    cliente_detalle = ClienteSerializer(source='cliente', read_only=True)
    tipo_servicio_detalle = TipoServicioSerializer(source='tipo_servicio', read_only=True)
    hallazgos = HallazgoSerializer(many=True, read_only=True)
    servicios = OrdenServicioSerializer(many=True, read_only=True)
    repuestos = OrdenRepuestoSerializer(many=True, read_only=True)
    recepcionista_nombre = serializers.CharField(source='recepcionista.nombre_completo', read_only=True)
    mecanico_nombre = serializers.CharField(source='mecanico_asignado.nombre_completo', read_only=True)
    historial_estados = OrdenHistorialEstadoSerializer(many=True, read_only=True)

    class Meta:
        model = OrdenTrabajo
        fields = [
            'id', 'numero', 'vehiculo', 'vehiculo_detalle', 'cliente', 'cliente_detalle', 'recepcionista', 'recepcionista_nombre',
            'mecanico_asignado', 'mecanico_nombre', 'estado', 'tipo_servicio', 'tipo_servicio_detalle', 'kilometraje_ingreso',
            'motivo_ingreso', 'url_cotizacion_pdf', 'fecha_vencimiento_cotizacion', 'fecha_estimada_entrega', 'fecha_ingreso',
            'fecha_finalizacion', 'hallazgos', 'servicios', 'repuestos', 'historial_estados'
        ]
        read_only_fields = ['recepcionista', 'numero']

class PlantillaPreventivaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlantillaPreventiva
        fields = '__all__'


class PlantillaCorrectivaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlantillaCorrectiva
        fields = '__all__'
