from rest_framework import serializers
from .models import OrdenTrabajo, Hallazgo, OrdenServicio, OrdenRepuesto, PlantillaPreventiva, TipoServicio
from apps.vehiculos.serializers import VehiculoSerializer
from apps.inventario.serializers import RepuestoSerializer
from apps.clientes.serializers import ClienteSerializer

class TipoServicioSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoServicio
        fields = '__all__'

class HallazgoSerializer(serializers.ModelSerializer):
    registrado_por_nombre = serializers.CharField(source='registrado_por.nombre_completo', read_only=True)

    class Meta:
        model = Hallazgo
        fields = ['id', 'orden', 'descripcion', 'severidad', 'fecha_registro', 'registrado_por', 'registrado_por_nombre']
        read_only_fields = ['registrado_por']

class OrdenServicioSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrdenServicio
        fields = ['id', 'orden', 'descripcion', 'precio_estimado', 'aprobado_cliente', 'completado']

class OrdenRepuestoSerializer(serializers.ModelSerializer):
    repuesto_detalle = RepuestoSerializer(source='repuesto', read_only=True)

    class Meta:
        model = OrdenRepuesto
        fields = ['id', 'orden', 'repuesto', 'repuesto_detalle', 'cantidad', 'precio_unitario', 'aprobado_cliente', 'entregado_por_almacen', 'instalado']

class OrdenTrabajoListSerializer(serializers.ModelSerializer):
    vehiculo_placa = serializers.CharField(source='vehiculo.placa', read_only=True)
    cliente_nombre = serializers.SerializerMethodField()
    mecanico_nombre = serializers.CharField(source='mecanico_asignado.nombre_completo', read_only=True)
    tipo_servicio_detalle = TipoServicioSerializer(source='tipo_servicio', read_only=True)
    
    class Meta:
        model = OrdenTrabajo
        fields = ['id', 'numero', 'vehiculo', 'vehiculo_placa', 'cliente', 'cliente_nombre', 'estado', 'tipo_servicio', 'tipo_servicio_detalle', 'fecha_ingreso', 'mecanico_nombre', 'motivo_ingreso', 'fecha_vencimiento_cotizacion']
        
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

    class Meta:
        model = OrdenTrabajo
        fields = [
            'id', 'numero', 'vehiculo', 'vehiculo_detalle', 'cliente', 'cliente_detalle', 'recepcionista', 'recepcionista_nombre',
            'mecanico_asignado', 'mecanico_nombre', 'estado', 'tipo_servicio', 'tipo_servicio_detalle', 'kilometraje_ingreso',
            'motivo_ingreso', 'url_cotizacion_pdf', 'fecha_vencimiento_cotizacion', 'hallazgos', 'servicios', 'repuestos'
        ]
        read_only_fields = ['recepcionista', 'numero']

class PlantillaPreventivaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlantillaPreventiva
        fields = '__all__'
