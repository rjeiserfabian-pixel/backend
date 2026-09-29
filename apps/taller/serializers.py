from rest_framework import serializers
from .models import OrdenTrabajo, Hallazgo, OrdenServicio, OrdenRepuesto, PlantillaPreventiva, TipoServicio, OrdenHistorialEstado, PlantillaCorrectiva
from apps.vehiculos.serializers import VehiculoSerializer
from apps.inventario.models import InventarioStock
from apps.inventario.serializers import RepuestoSerializer
from apps.clientes.serializers import ClienteSerializer

class TipoServicioSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoServicio
        fields = '__all__'

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
