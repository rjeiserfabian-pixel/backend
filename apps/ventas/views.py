import logging
from django.db.models import Sum
from rest_framework import viewsets, status, views
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from .models import (
    Caja, SesionCaja, MovimientoCaja, TipoComprobante, SerieComprobante, MetodoPago, 
    Impuesto, Venta, DetalleVenta, CuentaPorCobrar, CuotaCredito
)
from .serializers import (
    CajaSerializer, SesionCajaSerializer, MovimientoCajaSerializer,
    TipoComprobanteSerializer, SerieComprobanteSerializer, MetodoPagoSerializer, ImpuestoSerializer,
    VentaSerializer, TicketKioskoCreateSerializer, ProcesarVentaSerializer,
    CuentaPorCobrarSerializer
)
from .services import VentasService, CreditoService
from apps.inventario.models import Sucursal, Almacen
from apps.clientes.models import Cliente
from apps.vehiculos.models import Vehiculo

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# MANTENIMIENTO (CONFIGURACIONES)
# ──────────────────────────────────────────────

class MetodoPagoViewSet(viewsets.ModelViewSet):
    queryset = MetodoPago.objects.all()
    serializer_class = MetodoPagoSerializer


class ImpuestoViewSet(viewsets.ModelViewSet):
    queryset = Impuesto.objects.all()
    serializer_class = ImpuestoSerializer


class TipoComprobanteViewSet(viewsets.ModelViewSet):
    queryset = TipoComprobante.objects.all()
    serializer_class = TipoComprobanteSerializer


class SerieComprobanteViewSet(viewsets.ModelViewSet):
    queryset = SerieComprobante.objects.all()
    serializer_class = SerieComprobanteSerializer


class CajaViewSet(viewsets.ModelViewSet):
    queryset = Caja.objects.all()
    serializer_class = CajaSerializer


# ──────────────────────────────────────────────
# CAJA Y SESIONES
# ──────────────────────────────────────────────

class SesionCajaViewSet(viewsets.ModelViewSet):
    queryset = SesionCaja.objects.all()
    serializer_class = SesionCajaSerializer

    @action(detail=False, methods=['post'])
    def aperturar(self, request):
        caja_id = request.data.get('caja_id')
        saldo_inicial = request.data.get('saldo_inicial', 0.00)
        
        caja = Caja.objects.filter(id=caja_id).first()
        if not caja:
            return Response({"error": "Caja no encontrada."}, status=status.HTTP_404_NOT_FOUND)
            
        sesion_abierta = SesionCaja.objects.filter(caja=caja, estado=SesionCaja.Estado.ABIERTA).exists()
        if sesion_abierta:
            return Response({"error": "Ya existe una sesión abierta para esta caja."}, status=status.HTTP_400_BAD_REQUEST)
            
        sesion = SesionCaja.objects.create(
            caja=caja,
            usuario=request.user,
            saldo_inicial=saldo_inicial
        )
        return Response(SesionCajaSerializer(sesion).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def cerrar(self, request, pk=None):
        sesion = self.get_object()
        saldo_fisico_declarado = request.data.get('saldo_cierre_real', 0.00)
        
        # Calcular saldo esperado sumando movimientos
        ingresos = sesion.movimientos.filter(tipo=MovimientoCaja.Tipo.INGRESO).aggregate(t=Sum('monto'))['t'] or 0
        egresos = sesion.movimientos.filter(tipo=MovimientoCaja.Tipo.EGRESO).aggregate(t=Sum('monto'))['t'] or 0
        saldo_esperado = float(sesion.saldo_inicial) + float(ingresos) - float(egresos)
        
        sesion.saldo_cierre_esperado = saldo_esperado
        sesion.saldo_cierre_real = saldo_fisico_declarado
        sesion.estado = SesionCaja.Estado.CERRADA
        from django.utils import timezone
        sesion.fecha_cierre = timezone.now()
        sesion.save()
        
        return Response(SesionCajaSerializer(sesion).data)

    @action(detail=True, methods=['get'], url_path='reporte-cierre')
    def reporte_cierre(self, request, pk=None):
        sesion = self.get_object()
        
        # Agrupar ingresos por método de pago
        movimientos = sesion.movimientos.all()
        por_metodo = {}
        por_concepto = {}
        
        for mov in movimientos:
            # Agrupar por método
            if mov.metodo_pago.nombre not in por_metodo:
                por_metodo[mov.metodo_pago.nombre] = 0
            # Agrupar por concepto
            if mov.concepto not in por_concepto:
                por_concepto[mov.concepto] = 0
                
            if mov.tipo == MovimientoCaja.Tipo.INGRESO:
                por_metodo[mov.metodo_pago.nombre] += float(mov.monto)
                por_concepto[mov.concepto] += float(mov.monto)
            else:
                por_metodo[mov.metodo_pago.nombre] -= float(mov.monto)
                por_concepto[mov.concepto] -= float(mov.monto)
                
        return Response({
            "sesion_id": sesion.id,
            "caja": sesion.caja.nombre,
            "cajero": sesion.usuario.get_full_name(),
            "saldo_inicial": sesion.saldo_inicial,
            "desglose_por_metodo": por_metodo,
            "desglose_por_concepto": por_concepto,
            "saldo_final_esperado": sesion.saldo_cierre_esperado,
            "estado": sesion.estado
        })


# ──────────────────────────────────────────────
# VENTAS Y KIOSKO
# ──────────────────────────────────────────────

class VentaViewSet(viewsets.ModelViewSet):
    queryset = Venta.objects.all().order_by('-creado_en')
    serializer_class = VentaSerializer

    @action(detail=False, methods=['post'], url_path='kiosko/generar-ticket')
    def kiosko_generar_ticket(self, request):
        serializer = TicketKioskoCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        cliente = Cliente.objects.get(id=data['cliente_id'])
        vehiculo = Vehiculo.objects.get(id=data['vehiculo_id'])
        sucursal = Sucursal.objects.get(id=data['sucursal_id'])
        
        try:
            venta = VentasService.generar_ticket_kiosko(
                cliente=cliente,
                vehiculo=vehiculo,
                sucursal=sucursal,
                detalles_data=data['detalles']
            )
            return Response(VentaSerializer(venta).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Error generando ticket: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def procesar(self, request, pk=None):
        venta = self.get_object()
        serializer = ProcesarVentaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        
        sesion_caja_id = request.data.get('sesion_caja_id')
        sesion = SesionCaja.objects.filter(id=sesion_caja_id, estado=SesionCaja.Estado.ABIERTA).first()
        if not sesion:
            return Response({"error": "Debe proporcionar una sesión de caja abierta."}, status=status.HTTP_400_BAD_REQUEST)
            
        almacen_origen = Almacen.objects.filter(id=data.get('almacen_origen_id')).first()
        if not almacen_origen:
            almacen_origen = venta.sucursal.almacenes.first()
            
        try:
            venta = VentasService.procesar_pago_venta(
                venta=venta,
                sesion_caja=sesion,
                tipo_comprobante=data['tipo_comprobante'],
                pagos_data=data['pagos'],
                almacen_origen=almacen_origen,
                usuario=request.user
            )
            
            # Si es crédito, generamos las cuotas
            if 'credito' in data:
                CreditoService.generar_credito(
                    venta=venta,
                    frecuencia=data['credito']['frecuencia'],
                    num_cuotas=data['credito']['cuotas'],
                    dia_pago=data['credito'].get('dia_pago')
                )
                
            return Response(VentaSerializer(venta).data)
        except Exception as e:
            logger.error(f"Error procesando venta {pk}: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CuentaPorCobrarViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CuentaPorCobrar.objects.all().order_by('-creado_en')
    serializer_class = CuentaPorCobrarSerializer
