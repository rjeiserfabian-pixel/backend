import logging
from django.db import transaction
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import action

from .models import SesionCaja, MetodoPago, CuentaPorCobrar, CuotaCredito
from .serializers import CuentaPorCobrarSerializer
from apps.seguridad.permissions import TienePermiso

from .paginacion import VentaPagination

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# MANTENIMIENTO (CONFIGURACIONES)
# ──────────────────────────────────────────────


class CuentaPorCobrarViewSet(viewsets.ModelViewSet):
    queryset = CuentaPorCobrar.objects.select_related('venta__cliente').prefetch_related('cuotas').all()
    serializer_class = CuentaPorCobrarSerializer
    pagination_class = VentaPagination

    def get_permissions(self):
        if self.action == 'pagar_cuota':
            return [TienePermiso("CUENTAS.POR_COBRAR.REGISTRAR_PAGO")]
        if self.request.method == 'GET':
            return [TienePermiso("CUENTAS.POR_COBRAR.VER")]
        # No hay código CREAR/EDITAR/ELIMINAR dedicado para cuentas por cobrar
        # fuera del registro de pagos; se reutiliza REGISTRAR_PAGO.
        return [TienePermiso("CUENTAS.POR_COBRAR.REGISTRAR_PAGO")]

    def get_queryset(self):
        qs = super().get_queryset()
        estado = self.request.query_params.get('estado')
        cliente_id = self.request.query_params.get('cliente_id')
        if estado:
            qs = qs.filter(estado=estado)
        if cliente_id:
            qs = qs.filter(venta__cliente_id=cliente_id)
        return qs.order_by('-creado_en')

    @action(detail=False, methods=['get'], url_path='resumen-clientes')
    def resumen_clientes(self, request):
        from django.db.models import Sum, Count, Q
        from django.utils import timezone

        hoy = timezone.localdate()

        # El estado ATRASADO de CuentaPorCobrar nunca se actualiza en ningún
        # lado del sistema (queda fijo en PENDIENTE aunque venza), así que
        # contar por ese campo siempre daría 0. Se calcula en su lugar
        # dinámicamente: una cuenta "tiene atraso" si alguna de sus cuotas
        # está vencida y con saldo pendiente — igual que ya hace Cuentas por
        # Pagar en resumen_proveedores.
        qs = CuentaPorCobrar.objects.values(
            'venta__cliente__id',
            'venta__cliente__dni',
            'venta__cliente__nombres',
            'venta__cliente__apellidos'
        ).annotate(
            total_deuda=Sum('monto_financiado'),
            saldo_pendiente_total=Sum('saldo_pendiente'),
            tiene_atrasos=Count(
                'id',
                filter=Q(cuotas__fecha_vencimiento__lt=hoy, cuotas__saldo_pendiente__gt=0),
                distinct=True
            )
        ).order_by('-saldo_pendiente_total')

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(page)

        return Response(qs)

    @action(detail=False, methods=['get'], url_path='cuotas-vencidas')
    def cuotas_vencidas(self, request):
        """
        Para la campanita de alertas del header: cuotas de crédito vencidas
        (fecha_vencimiento pasada y con saldo pendiente) ordenadas por las más
        urgentes primero (más antiguas vencidas). Se limita a 30 resultados
        para no sobrecargar el desplegable; `total` lleva la cuenta real.
        Filtra por sucursal si se manda `sucursal_id` (vía Venta.sucursal).
        """
        from django.utils import timezone

        hoy = timezone.localdate()
        sucursal_id = request.query_params.get('sucursal_id')
        LIMITE = 30

        qs = CuotaCredito.objects.select_related(
            'cuenta_cobrar__venta__cliente'
        ).filter(
            fecha_vencimiento__lt=hoy,
            saldo_pendiente__gt=0
        )
        if sucursal_id:
            qs = qs.filter(cuenta_cobrar__venta__sucursal_id=sucursal_id)
        qs = qs.order_by('fecha_vencimiento')

        total = qs.count()
        data = [{
            'id': c.id,
            'cuenta_cobrar_id': c.cuenta_cobrar_id,
            'cliente_nombre': f"{c.cuenta_cobrar.venta.cliente.nombres} {c.cuenta_cobrar.venta.cliente.apellidos}".strip(),
            'codigo_credito': c.cuenta_cobrar.codigo_credito,
            'numero_cuota': c.numero_cuota,
            'saldo_pendiente': c.saldo_pendiente,
            'fecha_vencimiento': c.fecha_vencimiento,
            'dias_vencido': (hoy - c.fecha_vencimiento).days,
        } for c in qs[:LIMITE]]

        return Response({'total': total, 'results': data})

    @action(detail=False, methods=['post'], url_path='pagar-cuota/(?P<cuota_id>[^/.]+)')
    def pagar_cuota(self, request, cuota_id=None):
        from django.utils import timezone
        from decimal import Decimal
        from apps.ventas.models import PagoCuota, MovimientoCaja
        try:
            with transaction.atomic():
                cuota = CuotaCredito.objects.select_for_update().get(id=cuota_id)
                if cuota.estado == CuotaCredito.Estado.PAGADA:
                    return Response({"error": "Esta cuota ya está pagada."}, status=400)
                
                sesion = SesionCaja.objects.filter(usuario=request.user, fecha_cierre__isnull=True).first()
                if not sesion:
                    return Response({"error": "Debes abrir una caja antes de registrar un cobro."}, status=400)
                
                pagos = request.data.get('pagos', [])
                if not pagos:
                    return Response({"error": "Debe enviar al menos un método de pago y monto."}, status=400)
                
                total_pagado = sum(Decimal(str(p.get('monto', 0))) for p in pagos)
                if total_pagado > cuota.saldo_pendiente:
                    return Response({"error": "El monto pagado supera el saldo pendiente de la cuota."}, status=400)

                import uuid
                operacion_uuid = uuid.uuid4()
                nuevos_pagos = []
                for pago_data in pagos:
                    monto_decimal = Decimal(str(pago_data.get('monto', 0)))
                    metodo_pago_id = pago_data.get('metodo_pago_id')
                    metodo_pago = MetodoPago.objects.get(id=metodo_pago_id)
                    
                    movimiento = MovimientoCaja.objects.create(
                        sesion=sesion,
                        tipo=MovimientoCaja.Tipo.INGRESO,
                        concepto=MovimientoCaja.Concepto.COBRO_CUOTA,
                        metodo_pago=metodo_pago,
                        monto=monto_decimal,
                        referencia=pago_data.get('referencia', ''),
                        origen_movimiento=MovimientoCaja.OrigenMovimiento.COBRO,
                        creado_por=request.user
                    )
                    pago_obj = PagoCuota.objects.create(
                        cuota=cuota,
                        movimiento_caja=movimiento,
                        operacion_id=operacion_uuid,
                        monto=monto_decimal
                    )
                    nuevos_pagos.append(pago_obj)
                
                cuota.saldo_pendiente -= total_pagado
                if cuota.saldo_pendiente <= 0:
                    cuota.saldo_pendiente = 0
                    cuota.estado = CuotaCredito.Estado.PAGADA
                    cuota.fecha_pago = timezone.now().date()
                else:
                    cuota.estado = CuotaCredito.Estado.PARCIAL
                cuota.save()

                cuenta = cuota.cuenta_cobrar
                cuenta.saldo_pendiente -= total_pagado
                if cuenta.saldo_pendiente <= 0:
                    cuenta.saldo_pendiente = 0
                    cuenta.estado = CuentaPorCobrar.Estado.PAGADO
                cuenta.save()
                
                from apps.ventas.serializers import PagoCuotaSerializer
                pagos_data = PagoCuotaSerializer(nuevos_pagos, many=True).data

                return Response({
                    "message": "Pago registrado exitosamente.",
                    "pagos": pagos_data
                })
        except Exception as e:
            logger.error(f"Error al pagar cuota: {str(e)}")
            return Response({"error": str(e)}, status=500)
