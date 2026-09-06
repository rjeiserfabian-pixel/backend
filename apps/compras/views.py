from decimal import Decimal
from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import Compra, DetalleCompra, CuentaPorPagar, PagoCuenta
from .serializers import CompraSerializer, CuentaPorPagarSerializer, PagoCuentaSerializer
from apps.inventario.models import Repuesto, InventarioStock, MovimientoInventario, UbicacionFisica

class CompraViewSet(viewsets.ModelViewSet):
    queryset = Compra.objects.all().select_related('proveedor', 'usuario')
    serializer_class = CompraSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        data = request.data
        detalles_data = data.pop('detalles', [])
        ubicacion_id = data.pop('ubicacion_id', None) # Ubicación donde ingresa la mercadería
        
        # Validar ubicación
        if not ubicacion_id:
            return Response({"error": "Debe especificar la ubicación de destino (ubicacion_id)."}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            ubicacion = UbicacionFisica.objects.get(id=ubicacion_id)
        except UbicacionFisica.DoesNotExist:
            return Response({"error": "La ubicación especificada no existe."}, status=status.HTTP_400_BAD_REQUEST)

        # Crear Compra
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        compra = serializer.save(usuario=request.user)

        for detalle in detalles_data:
            repuesto_id = detalle['repuesto']
            cantidad = Decimal(str(detalle['cantidad']))
            precio_unitario = Decimal(str(detalle['precio_unitario']))
            
            repuesto = Repuesto.objects.select_for_update().get(id=repuesto_id)
            
            # 1. Guardar detalle de compra
            DetalleCompra.objects.create(
                compra=compra,
                repuesto=repuesto,
                cantidad=cantidad,
                precio_unitario=precio_unitario,
                subtotal=cantidad * precio_unitario
            )

            # 2. Actualizar Stock
            inventario, created = InventarioStock.objects.select_for_update().get_or_create(
                repuesto=repuesto,
                ubicacion=ubicacion,
                defaults={'stock_disponible': 0, 'stock_reservado': 0, 'stock_merma': 0}
            )
            inventario.stock_disponible += cantidad
            inventario.save()

            # 3. Registrar en Kardex
            MovimientoInventario.objects.create(
                repuesto=repuesto,
                ubicacion=ubicacion,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.ENTRADA,
                cantidad=cantidad,
                stock_resultante=inventario.stock_disponible,
                motivo=f"Compra {compra.tipo_comprobante} {compra.serie}-{compra.numero_comprobante}",
                usuario=request.user,
                referencia_id=compra.id,
                referencia_tipo='COMPRA'
            )

            # 4. Actualizar Costo Promedio Ponderado
            stock_total_actual = repuesto.stock_total_fisico
            costo_actual = repuesto.precio_compra
            
            # Nuevo costo = ((Stock anterior * Costo anterior) + (Nueva cantidad * Nuevo costo)) / (Stock anterior + Nueva cantidad)
            # Como stock_total_fisico ya incluye la cantidad recien sumada, el stock anterior es stock_total_actual - cantidad
            stock_anterior = stock_total_actual - cantidad
            
            if stock_anterior < 0:
                stock_anterior = 0 # Protección

            nuevo_costo = ((stock_anterior * costo_actual) + (cantidad * precio_unitario)) / stock_total_actual
            repuesto.precio_compra = nuevo_costo.quantize(Decimal('0.00'))

            # 5. Evaluar Margen y Alerta
            # Margen = ((Precio Lista - Nuevo Costo) / Precio Lista) * 100
            if repuesto.precio_lista > 0:
                margen_actual = ((repuesto.precio_lista - repuesto.precio_compra) / repuesto.precio_lista) * 100
                margen_esperado = repuesto.categoria.margen_minimo_esperado
                if margen_actual < margen_esperado:
                    repuesto.alerta_precio = True
                else:
                    repuesto.alerta_precio = False
            else:
                repuesto.alerta_precio = True # Si no hay precio de lista, alertar.

            repuesto.save()

        # Si es al crédito, crear Cuenta por Pagar
        if compra.tipo_pago == 'Credito':
            dias_credito = int(data.get('dias_credito', 30))
            from datetime import timedelta
            vencimiento = compra.fecha_emision + timedelta(days=dias_credito)
            
            CuentaPorPagar.objects.create(
                compra=compra,
                proveedor=compra.proveedor,
                monto_total=compra.total,
                monto_pagado=0,
                saldo_pendiente=compra.total,
                fecha_vencimiento=vencimiento,
                estado='Pendiente'
            )

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


from rest_framework.decorators import action

class CuentaPorPagarViewSet(viewsets.ModelViewSet):
    queryset = CuentaPorPagar.objects.all().select_related('proveedor', 'compra')
    serializer_class = CuentaPorPagarSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        proveedor_id = self.request.query_params.get('proveedor_id')
        if proveedor_id:
            qs = qs.filter(proveedor_id=proveedor_id)
        return qs.order_by('-fecha_creacion')

    @action(detail=False, methods=['get'], url_path='resumen-proveedores')
    def resumen_proveedores(self, request):
        from django.db.models import Sum, Count, Q
        from django.utils import timezone
        
        qs = CuentaPorPagar.objects.values(
            'proveedor__id',
            'proveedor__numero_documento',
            'proveedor__nombre_o_razon_social'
        ).annotate(
            total_deuda=Sum('monto_total'),
            saldo_pendiente_total=Sum('saldo_pendiente'),
            tiene_atrasos=Count('id', filter=Q(fecha_vencimiento__lt=timezone.now().date(), saldo_pendiente__gt=0))
        ).order_by('-saldo_pendiente_total')

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(page)
            
        return Response(qs)



class PagoCuentaViewSet(viewsets.ModelViewSet):
    queryset = PagoCuenta.objects.all()
    serializer_class = PagoCuentaSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        pago = serializer.save(usuario=request.user)
        cuenta = pago.cuenta_por_pagar
        
        # Actualizar la cuenta por pagar
        cuenta.monto_pagado += pago.monto_abonado
        cuenta.saldo_pendiente = cuenta.monto_total - cuenta.monto_pagado
        
        if cuenta.saldo_pendiente <= 0:
            cuenta.estado = 'Pagada'
            cuenta.saldo_pendiente = 0 # Sanity check
        elif cuenta.monto_pagado > 0:
            cuenta.estado = 'Parcial'
            
        cuenta.save()
        
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
