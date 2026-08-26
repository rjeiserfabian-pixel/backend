import logging
from django.db import transaction
from django.db.models import Sum
from rest_framework import viewsets, status, views
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from .models import (
    Caja, SesionCaja, MovimientoCaja, TipoComprobante, SerieComprobante, MetodoPago, 
    Impuesto, Venta, DetalleVenta, CuentaPorCobrar, CuotaCredito, PagoVenta
)
from .serializers import (
    CajaSerializer, SesionCajaSerializer, MovimientoCajaSerializer,
    TipoComprobanteSerializer, SerieComprobanteSerializer, MetodoPagoSerializer, ImpuestoSerializer,
    VentaSerializer, TicketKioskoCreateSerializer, ProcesarVentaSerializer,
    CuentaPorCobrarSerializer
)
from .services import VentasService, CreditoService
from apps.inventario.models import Sucursal, Almacen, Repuesto
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

    @action(detail=True, methods=['get'], url_path='detalle-activa')
    def detalle_activa(self, request, pk=None):
        sesion = self.get_object()
        
        ingresos = sesion.movimientos.filter(tipo=MovimientoCaja.Tipo.INGRESO).aggregate(t=Sum('monto'))['t'] or 0
        egresos = sesion.movimientos.filter(tipo=MovimientoCaja.Tipo.EGRESO).aggregate(t=Sum('monto'))['t'] or 0
        saldo_actual = float(sesion.saldo_inicial) + float(ingresos) - float(egresos)
        
        movimientos = sesion.movimientos.all().order_by('-fecha')
        
        return Response({
            "sesion_id": sesion.id,
            "estado": sesion.estado,
            "saldo_inicial": float(sesion.saldo_inicial),
            "ingresos": float(ingresos),
            "egresos": float(egresos),
            "saldo_actual": float(saldo_actual),
            "movimientos": MovimientoCajaSerializer(movimientos, many=True).data
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

    @action(detail=False, methods=['post'], url_path='directa')
    @transaction.atomic
    def procesar_venta_directa(self, request):
        try:
            # Para POS y Registro Manual
            data = request.data
            es_registro_manual = data.get('es_registro_manual', False)
            fecha_manual = data.get('fecha_manual')
            
            cliente = Cliente.objects.get(id=data['cliente_id'])
            sucursal = Sucursal.objects.get(id=data['sucursal_id'])
            
            # 1. Crear Venta
            from django.utils import timezone
            import uuid
            
            fecha_venta = timezone.now()
            if es_registro_manual and fecha_manual:
                from django.utils.dateparse import parse_datetime
                parsed_date = parse_datetime(fecha_manual)
                if parsed_date:
                    fecha_venta = parsed_date

            venta = Venta.objects.create(
                cliente=cliente,
                sucursal=sucursal,
                estado=Venta.Estado.PRE_VENTA,
                ticket_kiosko=f"POS-{str(uuid.uuid4())[:6].upper()}",
                creado_en=fecha_venta
            )
            
            # Detalles
            subtotal_acumulado = 0
            for item in data.get('detalles', []):
                repuesto = Repuesto.objects.get(id=item['repuesto_id'])
                cantidad = item['cantidad']
                precio = float(item['precio_venta'])
                sub = cantidad * precio
                subtotal_acumulado += sub
                DetalleVenta.objects.create(
                    venta=venta, repuesto=repuesto, cantidad=cantidad, 
                    precio_unitario=precio, subtotal_linea=sub
                )
                
            venta.total = subtotal_acumulado
            venta.subtotal = float(venta.total) / 1.18
            venta.igv = float(venta.total) - float(venta.subtotal)
            venta.save()
            
            # 2. Procesar (Caja, Stock, etc)
            tipo_comprobante = TipoComprobante.objects.get(id=data['tipo_comprobante_id'])
            
            # Correlativo
            serie_obj = SerieComprobante.objects.filter(id=data['serie_id']).first()
            correlativo = serie_obj.generar_siguiente_correlativo()
            serie_obj.correlativo_actual += 1
            serie_obj.save()
            
            venta.estado = Venta.Estado.AL_CREDITO if data.get('condicion_pago') == 'CREDITO' else Venta.Estado.PAGADA
            venta.tipo_comprobante = tipo_comprobante
            venta.serie_correlativo = correlativo
            venta.fecha_emision = fecha_venta
            
            # Movimientos de Caja (saltar si es registro manual)
            sesion = None
            if not es_registro_manual:
                sesion_caja_id = data.get('sesion_caja_id')
                sesion = SesionCaja.objects.filter(id=sesion_caja_id, estado=SesionCaja.Estado.ABIERTA).first()
                if not sesion:
                    return Response({"error": "Sesión de caja abierta requerida para venta normal."}, status=status.HTTP_400_BAD_REQUEST)
                venta.sesion_caja = sesion

            venta.save()
            
            # Registrar pagos
            if not es_registro_manual and sesion and data.get('pagos'):
                for p in data['pagos']:
                    monto_pago = p.get('monto', 0)
                    if float(monto_pago) > 0:
                        movimiento = MovimientoCaja.objects.create(
                            sesion=sesion,
                            tipo=MovimientoCaja.Tipo.INGRESO,
                            concepto=MovimientoCaja.Concepto.VENTA,
                            metodo_pago_id=p.get('metodo_id'),
                            monto=monto_pago,
                            referencia=p.get('referencia', '') or f"Ticket {venta.serie_correlativo}",
                            venta_origen=venta,
                            creado_por=request.user
                        )
                        PagoVenta.objects.create(
                            venta=venta,
                            movimiento_caja=movimiento,
                            monto=monto_pago
                        )
            
            # 2. Procesar (Stock, etc)
            almacen_origen = None
            almacen_origen_id = data.get('almacen_origen_id')
            
            if almacen_origen_id:
                almacen_origen = Almacen.objects.filter(id=almacen_origen_id, sucursal=sucursal).first()
            
            if not almacen_origen:
                if sesion and sesion.caja.almacen_defecto and sesion.caja.almacen_defecto.sucursal_id == sucursal.id:
                    almacen_origen = sesion.caja.almacen_defecto
                else:
                    almacen_origen = sucursal.almacenes.first()
                    
            if not almacen_origen:
                return Response({"error": "La sucursal no tiene almacenes configurados."}, status=status.HTTP_400_BAD_REQUEST)

            # 3. Descontar stock
            for det in venta.detalles.all():
                VentasService._descontar_stock(
                    repuesto=det.repuesto, 
                    almacen=almacen_origen, 
                    cantidad=det.cantidad, 
                    motivo=f"Venta {venta.serie_correlativo}",
                    usuario=request.user,
                    referencia_id=venta.id
                )
                
            return Response(VentaSerializer(venta).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Error procesando venta directa: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class CuentaPorCobrarViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CuentaPorCobrar.objects.all().order_by('-creado_en')
    serializer_class = CuentaPorCobrarSerializer
