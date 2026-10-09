"""
avanzado_views.py — Reporte Avanzado (ventas, compras, órdenes de servicio, resumen del día, rentabilidad).
Se separó de views.py por tamaño; el contenido de la vista no cambió.
"""
import logging
from datetime import date, timedelta

from django.db.models import Sum, Count, F, Q, Value, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce, TruncDate
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from apps.ventas.models import Venta, MovimientoCaja, DetalleVenta
from apps.taller.models import OrdenTrabajo
from apps.seguridad.permissions import TienePermiso

from .helpers import (
    CONCEPTOS_TRANSFERENCIA_INTERNA,
    _exportar_excel,
    _exportar_pdf,
    _parse_date_range,
    _totales_ventas_en_soles,
)

logger = logging.getLogger(__name__)


class ReporteAvanzadoView(APIView):
    """
    GET /api/reportes/avanzado/
    Sub-reportes: tipo = ventas_general | ventas_sucursal | ventas_detalladas |
                         compras_detalladas | ordenes_servicio | resumen_dia | rentabilidad
    Parámetros: fecha_inicio, fecha_fin, sucursal_id, tipo, formato
    """
    def get_permissions(self):
        if self.request.query_params.get('formato') in ('excel', 'pdf'):
            return [TienePermiso("REPORTES.AVANZADO.EXPORTAR")]
        return [TienePermiso("REPORTES.AVANZADO.VER")]

    def get(self, request):
        fecha_inicio, fecha_fin = _parse_date_range(request)
        tipo = request.query_params.get("tipo", "ventas_general")
        sucursal_id = request.query_params.get("sucursal_id")
        formato = request.query_params.get("formato", "json")

        if tipo == "ventas_general":
            return self._ventas_general(request, fecha_inicio, fecha_fin, sucursal_id, formato)
        elif tipo == "ventas_sucursal":
            return self._ventas_por_sucursal(request, fecha_inicio, fecha_fin, formato)
        elif tipo == "ventas_detalladas":
            return self._ventas_detalladas(request, fecha_inicio, fecha_fin, sucursal_id, formato)
        elif tipo == "compras_detalladas":
            return self._compras_detalladas(request, fecha_inicio, fecha_fin, formato)
        elif tipo == "ordenes_servicio":
            return self._ordenes_servicio(request, fecha_inicio, fecha_fin, formato)
        elif tipo == "resumen_dia":
            return self._resumen_dia(request, sucursal_id)
        elif tipo == "rentabilidad":
            return self._rentabilidad(request, fecha_inicio, fecha_fin, sucursal_id, formato)
        else:
            return Response({"error": "Tipo de reporte no válido."}, status=400)

    def _ventas_general(self, request, fi, ff, sucursal_id, formato):
        """Reporte General de Ventas: ventas netas vs costo de ventas."""
        qs = (
            DetalleVenta.objects
            .select_related("repuesto", "venta__sucursal")
            .filter(
                venta__fecha_emision__date__gte=fi,
                venta__fecha_emision__date__lte=ff,
            )
            .exclude(venta__estado=Venta.Estado.ANULADA)
        )
        if sucursal_id:
            qs = qs.filter(venta__sucursal_id=sucursal_id)

        totales = qs.aggregate(
            ventas_netas=Coalesce(Sum("subtotal_linea"), Value(0, output_field=DecimalField())),
            costo_ventas=Coalesce(
                Sum(
                    # costo_unitario es el costo histórico al momento de la
                    # venta; se usa el precio_compra actual del repuesto solo
                    # como respaldo para filas creadas antes de este campo.
                    ExpressionWrapper(
                        Coalesce(F("costo_unitario"), F("repuesto__precio_compra")) * F("cantidad"),
                        output_field=DecimalField(max_digits=14, decimal_places=2),
                    ),
                    filter=Q(repuesto__isnull=False),
                ),
                Value(0, output_field=DecimalField()),
            ),
        )
        ventas_netas = float(totales["ventas_netas"])
        costo_ventas = float(totales["costo_ventas"])
        utilidad = ventas_netas - costo_ventas
        margen = (utilidad / ventas_netas * 100) if ventas_netas > 0 else 0

        cero = Value(0, output_field=DecimalField(max_digits=14, decimal_places=2))
        costo_linea_expr = ExpressionWrapper(
            Coalesce(F("costo_unitario"), F("repuesto__precio_compra")) * F("cantidad"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )

        # Repuestos más rentables del periodo (mismo qs/criterio que el
        # cálculo de arriba, para que no haya inconsistencia entre las
        # tarjetas y este desglose).
        top_repuestos = []
        for r in (
            qs.filter(repuesto__isnull=False)
            .values("repuesto__nombre")
            .annotate(
                cantidad_vendida=Sum("cantidad"),
                ventas=Coalesce(Sum("subtotal_linea"), cero),
                costo=Coalesce(Sum(costo_linea_expr), cero),
            )
        ):
            ventas_r = float(r["ventas"])
            costo_r = float(r["costo"])
            utilidad_r = ventas_r - costo_r
            top_repuestos.append({
                "repuesto": r["repuesto__nombre"],
                "cantidad_vendida": float(r["cantidad_vendida"]),
                "ventas": round(ventas_r, 2),
                "costo": round(costo_r, 2),
                "utilidad": round(utilidad_r, 2),
                "margen_porcentaje": round((utilidad_r / ventas_r * 100) if ventas_r > 0 else 0, 2),
            })
        # Se ordena por utilidad (repuestos "más rentables"), no por ventas:
        # el más vendido no siempre es el que más deja de ganancia.
        top_repuestos.sort(key=lambda r: r["utilidad"], reverse=True)
        top_repuestos = top_repuestos[:10]

        # Mano de obra = líneas de servicio (sin repuesto asociado); no
        # tienen costo registrado, así que la venta es 100% utilidad.
        # "ventas_netas"/"costo_ventas"/"utilidad" de arriba ya combinan
        # repuestos + mano de obra (subtotal_linea suma TODAS las líneas del
        # qs); aquí se separa la parte de repuestos por resta, no
        # reutilizando esos totales tal cual, para no duplicar mano de obra.
        ventas_mano_obra = float(
            qs.filter(repuesto__isnull=True).aggregate(t=Coalesce(Sum("subtotal_linea"), cero))["t"]
        )
        ventas_repuestos = ventas_netas - ventas_mano_obra
        costo_repuestos = costo_ventas
        utilidad_repuestos = ventas_repuestos - costo_repuestos

        data = {
            "ventas_netas": ventas_netas,
            "costo_ventas": costo_ventas,
            "utilidad_bruta": utilidad,
            "margen_porcentaje": round(margen, 2),
            "top_repuestos": top_repuestos,
            "repuestos_vs_mano_obra": {
                "ventas_repuestos": round(ventas_repuestos, 2),
                "costo_repuestos": round(costo_repuestos, 2),
                "utilidad_repuestos": round(utilidad_repuestos, 2),
                "ventas_mano_obra": round(ventas_mano_obra, 2),
                "utilidad_mano_obra": round(ventas_mano_obra, 2),
                "utilidad_total": round(utilidad_repuestos + ventas_mano_obra, 2),
            },
        }

        if formato == "excel":
            headers = ["Ventas Netas", "Costo de Ventas", "Utilidad Bruta", "Margen %"]
            rows = [[ventas_netas, costo_ventas, utilidad, round(margen, 2)]]
            return _exportar_excel(headers, rows, "Reporte_General_Ventas")
        if formato == "pdf":
            headers = ["Ventas Netas", "Costo de Ventas", "Utilidad Bruta", "Margen %"]
            rows = [[str(ventas_netas), str(costo_ventas), str(utilidad), f"{round(margen, 2)}%"]]
            return _exportar_pdf("Reporte General de Ventas", headers, rows)

        return Response(data)

    def _ventas_por_sucursal(self, request, fi, ff, formato):
        """Ventas agrupadas por sucursal."""
        qs = (
            Venta.objects
            .select_related("sucursal")
            .filter(
                fecha_emision__date__gte=fi,
                fecha_emision__date__lte=ff,
            )
            .exclude(estado=Venta.Estado.ANULADA)
            .values("sucursal__nombre")
            .annotate(
                cantidad=Count("id"),
                total=Coalesce(Sum("total"), Value(0, output_field=DecimalField())),
            )
            .order_by("-total")
        )
        data = list(qs)
        if formato == "excel":
            headers = ["Sucursal", "Cantidad Ventas", "Total Ventas"]
            rows = [[d["sucursal__nombre"], d["cantidad"], float(d["total"])] for d in data]
            return _exportar_excel(headers, rows, "Ventas_Por_Sucursal")
        if formato == "pdf":
            headers = ["Sucursal", "Cantidad Ventas", "Total Ventas"]
            rows = [[d["sucursal__nombre"], str(d["cantidad"]), str(float(d["total"]))] for d in data]
            return _exportar_pdf("Ventas por Sucursal", headers, rows)
        return Response({"data": [{"sucursal": d["sucursal__nombre"], "cantidad": d["cantidad"], "total": float(d["total"])} for d in data]})

    def _ventas_detalladas(self, request, fi, ff, sucursal_id, formato):
        """Ventas con su detalle de ítems."""
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)

        qs = (
            DetalleVenta.objects
            .select_related("venta__cliente", "venta__sucursal", "venta__tipo_comprobante", "repuesto")
            .filter(
                venta__fecha_emision__date__gte=fi,
                venta__fecha_emision__date__lte=ff,
            )
            .exclude(venta__estado=Venta.Estado.ANULADA)
        )
        if sucursal_id:
            qs = qs.filter(venta__sucursal_id=sucursal_id)

        total = qs.count()
        offset = (page - 1) * page_size
        detalles = qs.order_by("-venta__fecha_emision")[offset: offset + page_size]

        data = []
        for d in detalles:
            data.append({
                "fecha": d.venta.fecha_emision.strftime("%d/%m/%Y") if d.venta.fecha_emision else "—",
                "comprobante": d.venta.serie_correlativo or "—",
                "cliente": f"{d.venta.cliente.nombres} {d.venta.cliente.apellidos}",
                "producto": d.repuesto.nombre if d.repuesto else d.descripcion_servicio or "—",
                "cantidad": float(d.cantidad),
                "precio_unitario": float(d.precio_unitario),
                "subtotal": float(d.subtotal_linea),
                "sucursal": d.venta.sucursal.nombre,
            })

        if formato == "excel":
            headers = ["Fecha", "Comprobante", "Cliente", "Producto/Servicio", "Cantidad", "P. Unitario", "Subtotal", "Sucursal"]
            rows = [[d["fecha"], d["comprobante"], d["cliente"], d["producto"],
                     d["cantidad"], d["precio_unitario"], d["subtotal"], d["sucursal"]] for d in data]
            return _exportar_excel(headers, rows, "Ventas_Detalladas")
        if formato == "pdf":
            headers = ["Fecha", "Comprobante", "Cliente", "Producto", "Cant.", "P. Unit.", "Subtotal"]
            rows = [[d["fecha"], d["comprobante"], d["cliente"], d["producto"],
                     str(d["cantidad"]), str(d["precio_unitario"]), str(d["subtotal"])] for d in data]
            return _exportar_pdf("Ventas Detalladas", headers, rows)

        return Response({"data": data, "total": total, "page": page, "page_size": page_size,
                         "total_pages": (total + page_size - 1) // page_size})

    def _compras_detalladas(self, request, fi, ff, formato):
        """Compras con su detalle de ítems."""
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)

        from apps.compras.models import DetalleCompra
        qs = (
            DetalleCompra.objects
            .select_related("compra__proveedor", "compra__tipo_comprobante_fk", "repuesto")
            .exclude(compra__estado='Anulada')
            .filter(
                compra__fecha_emision__gte=fi,
                compra__fecha_emision__lte=ff,
            )
        )
        total = qs.count()
        offset = (page - 1) * page_size
        detalles = qs.order_by("-compra__fecha_emision")[offset: offset + page_size]

        data = []
        for d in detalles:
            data.append({
                "fecha": d.compra.fecha_emision.strftime("%d/%m/%Y") if d.compra.fecha_emision else "—",
                "proveedor": d.compra.proveedor.nombre_o_razon_social,
                "comprobante": f"{d.compra.serie}-{d.compra.numero_comprobante}",
                "repuesto": d.repuesto.nombre,
                "cantidad": float(d.cantidad),
                "precio_unitario": float(d.precio_unitario),
                "subtotal": float(d.subtotal),
            })

        if formato == "excel":
            headers = ["Fecha", "Proveedor", "Comprobante", "Repuesto", "Cantidad", "P. Unitario", "Subtotal"]
            rows = [[d["fecha"], d["proveedor"], d["comprobante"], d["repuesto"],
                     d["cantidad"], d["precio_unitario"], d["subtotal"]] for d in data]
            return _exportar_excel(headers, rows, "Compras_Detalladas")
        if formato == "pdf":
            headers = ["Fecha", "Proveedor", "Repuesto", "Cant.", "P. Unit.", "Subtotal"]
            rows = [[d["fecha"], d["proveedor"], d["repuesto"],
                     str(d["cantidad"]), str(d["precio_unitario"]), str(d["subtotal"])] for d in data]
            return _exportar_pdf("Compras Detalladas", headers, rows)

        return Response({"data": data, "total": total, "page": page, "page_size": page_size,
                         "total_pages": (total + page_size - 1) // page_size})

    def _ordenes_servicio(self, request, fi, ff, formato):
        """Órdenes de trabajo detalladas."""
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)

        qs = (
            OrdenTrabajo.objects
            .select_related(
                "vehiculo", "cliente",
                "recepcionista", "mecanico_asignado",
                "tipo_servicio",
            )
            .exclude(estado=OrdenTrabajo.Estado.CANCELADO)
            .filter(fecha_ingreso__date__gte=fi, fecha_ingreso__date__lte=ff)
        )
        total = qs.count()
        offset = (page - 1) * page_size
        ordenes = qs.order_by("-fecha_ingreso")[offset: offset + page_size]

        data = []
        for o in ordenes:
            data.append({
                "numero": o.numero,
                "fecha_ingreso": o.fecha_ingreso.strftime("%d/%m/%Y"),
                "placa": o.vehiculo.placa,
                "cliente": f"{o.cliente.nombres} {o.cliente.apellidos}" if o.cliente else "—",
                "mecanico": f"{o.mecanico_asignado.nombres} {o.mecanico_asignado.apellidos}" if o.mecanico_asignado else "—",
                "tipo_servicio": o.tipo_servicio.nombre if o.tipo_servicio else "—",
                "estado": o.estado,
                "fecha_finalizacion": o.fecha_finalizacion.strftime("%d/%m/%Y") if o.fecha_finalizacion else "—",
            })

        if formato == "excel":
            headers = ["N° Orden", "Fecha Ingreso", "Placa", "Cliente", "Mecánico", "Tipo Servicio", "Estado", "Fecha Fin"]
            rows = [[d["numero"], d["fecha_ingreso"], d["placa"], d["cliente"],
                     d["mecanico"], d["tipo_servicio"], d["estado"], d["fecha_finalizacion"]] for d in data]
            return _exportar_excel(headers, rows, "Ordenes_Servicio_Detallado")
        if formato == "pdf":
            headers = ["N° Orden", "Fecha", "Placa", "Cliente", "Estado", "Fecha Fin"]
            rows = [[d["numero"], d["fecha_ingreso"], d["placa"], d["cliente"],
                     d["estado"], d["fecha_finalizacion"]] for d in data]
            return _exportar_pdf("Órdenes de Servicio Detalladas", headers, rows)

        return Response({"data": data, "total": total, "page": page, "page_size": page_size,
                         "total_pages": (total + page_size - 1) // page_size})

    def _resumen_dia(self, request, sucursal_id):
        """
        Resumen gerencial del día para las tarjetas del Dashboard.

        Criterio de "ingreso" (definido junto al cliente):
        - Solo cuentan como ingreso las ventas ya PAGADAS y los cobros de
          cuotas de crédito (MovimientoCaja.COBRO_CUOTA) que entraron hoy.
        - Las ventas AL_CREDITO generadas hoy NO suman al ingreso (no ha
          entrado dinero todavía); se muestran aparte solo informativamente,
          para no perder visibilidad de que sí hubo negocio ese día.
        - Egreso = todo MovimientoCaja tipo EGRESO ya aprobado (incluye
          gasto operativo, pago a proveedor, retiro, etc.).
        """
        hoy = date.today()
        inicio_tendencia = hoy - timedelta(days=6)

        ventas_hoy_qs = Venta.objects.filter(fecha_emision__date=hoy)
        movimientos_hoy_qs = MovimientoCaja.objects.filter(
            fecha__date=hoy, estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO
        ).exclude(concepto__in=CONCEPTOS_TRANSFERENCIA_INTERNA)
        if sucursal_id:
            ventas_hoy_qs = ventas_hoy_qs.filter(sucursal_id=sucursal_id)
            movimientos_hoy_qs = movimientos_hoy_qs.filter(sesion__caja__sucursal_id=sucursal_id)

        ventas_pagadas_hoy = ventas_hoy_qs.filter(estado=Venta.Estado.PAGADA)
        ingresos_ventas = _totales_ventas_en_soles(ventas_pagadas_hoy)["total"]
        # Desglose taller/mostrador por resta (no por exclude()): un
        # ticket_kiosko nulo con exclude(startswith=...) se cae de ambos
        # lados por la lógica de tres valores de SQL con NULL, así que se
        # calcula el total de taller aparte y el resto es mostrador.
        ingresos_taller = _totales_ventas_en_soles(
            ventas_pagadas_hoy.filter(ticket_kiosko__startswith="OT-")
        )["total"]
        ingresos_mostrador = ingresos_ventas - ingresos_taller

        generado_credito_hoy = _totales_ventas_en_soles(
            ventas_hoy_qs.filter(estado=Venta.Estado.AL_CREDITO)
        )["total"]

        cero = Value(0, output_field=DecimalField(max_digits=14, decimal_places=2))
        cobros_credito = float(
            movimientos_hoy_qs.filter(
                tipo=MovimientoCaja.Tipo.INGRESO, concepto=MovimientoCaja.Concepto.COBRO_CUOTA
            ).aggregate(t=Coalesce(Sum("monto"), cero))["t"]
        )
        egresos_por_concepto = list(
            movimientos_hoy_qs.filter(tipo=MovimientoCaja.Tipo.EGRESO)
            .values("concepto")
            .annotate(total=Coalesce(Sum("monto"), cero))
            .order_by("-total")
        )
        egresado_hoy = sum(float(e["total"]) for e in egresos_por_concepto)

        ingresado_hoy = ingresos_ventas + cobros_credito
        neto_hoy = ingresado_hoy - egresado_hoy

        ordenes_hoy_qs = OrdenTrabajo.objects.exclude(estado=OrdenTrabajo.Estado.CANCELADO).filter(
            fecha_ingreso__date=hoy
        )
        ordenes_por_estado = list(
            ordenes_hoy_qs.values("estado").annotate(cantidad=Count("id")).order_by("-cantidad")
        )

        # Tendencia de los últimos 7 días (incluye hoy), para el gráfico del Dashboard.
        ventas_tendencia_qs = Venta.objects.filter(
            estado=Venta.Estado.PAGADA,
            fecha_emision__date__gte=inicio_tendencia,
            fecha_emision__date__lte=hoy,
        )
        movs_tendencia_qs = MovimientoCaja.objects.filter(
            estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO,
            fecha__date__gte=inicio_tendencia,
            fecha__date__lte=hoy,
        ).exclude(concepto__in=CONCEPTOS_TRANSFERENCIA_INTERNA)
        if sucursal_id:
            ventas_tendencia_qs = ventas_tendencia_qs.filter(sucursal_id=sucursal_id)
            movs_tendencia_qs = movs_tendencia_qs.filter(sesion__caja__sucursal_id=sucursal_id)

        ventas_por_dia = {
            v["dia"]: float(v["total"])
            for v in ventas_tendencia_qs.annotate(dia=TruncDate("fecha_emision"))
            .values("dia")
            .annotate(
                total=Coalesce(
                    Sum(ExpressionWrapper(F("total") * F("tipo_cambio"), output_field=DecimalField(max_digits=14, decimal_places=2))),
                    cero,
                )
            )
        }
        cobros_por_dia = {
            v["dia"]: float(v["total"])
            for v in movs_tendencia_qs.filter(
                tipo=MovimientoCaja.Tipo.INGRESO, concepto=MovimientoCaja.Concepto.COBRO_CUOTA
            )
            .annotate(dia=TruncDate("fecha"))
            .values("dia")
            .annotate(total=Coalesce(Sum("monto"), cero))
        }
        egresos_por_dia = {
            v["dia"]: float(v["total"])
            for v in movs_tendencia_qs.filter(tipo=MovimientoCaja.Tipo.EGRESO)
            .annotate(dia=TruncDate("fecha"))
            .values("dia")
            .annotate(total=Coalesce(Sum("monto"), cero))
        }

        tendencia = []
        for i in range(7):
            dia = inicio_tendencia + timedelta(days=i)
            ingresos_dia = ventas_por_dia.get(dia, 0) + cobros_por_dia.get(dia, 0)
            tendencia.append({
                "fecha": dia.isoformat(),
                "ingresos": round(ingresos_dia, 2),
                "egresos": round(egresos_por_dia.get(dia, 0), 2),
            })

        return Response({
            "fecha": hoy.isoformat(),
            "ingresado_hoy": round(ingresado_hoy, 2),
            "egresado_hoy": round(egresado_hoy, 2),
            "neto_hoy": round(neto_hoy, 2),
            "desglose_ingresos": {
                "ventas_taller": round(ingresos_taller, 2),
                "ventas_mostrador": round(ingresos_mostrador, 2),
                "cobros_credito_anterior": round(cobros_credito, 2),
            },
            "generado_credito_hoy": round(generado_credito_hoy, 2),
            "egresos_por_concepto": [
                {"concepto": e["concepto"], "total": round(float(e["total"]), 2)} for e in egresos_por_concepto
            ],
            "ordenes_ingresadas_hoy": sum(o["cantidad"] for o in ordenes_por_estado),
            "ordenes_por_estado": ordenes_por_estado,
            "tendencia_7_dias": tendencia,
        })

    def _rentabilidad(self, request, fi, ff, sucursal_id, formato):
        """
        Reporte de Rentabilidad: ingresos vs egresos del negocio en un rango
        de fechas, con serie diaria para graficar tendencia. Mismo criterio
        acordado con el cliente que en el resumen del día del Dashboard:
        - Ingreso = ventas PAGADA emitidas en el rango + cobros de cuota
          (MovimientoCaja.COBRO_CUOTA) del rango. Las ventas AL_CREDITO
          generadas en el rango no cuentan como ingreso todavía (no ha
          entrado dinero); se muestran aparte, solo informativamente.
        - Egreso = MovimientoCaja tipo EGRESO aprobados del rango (gasto
          operativo, pago a proveedor, retiro, etc.).
        """
        if (ff - fi).days > 366:
            raise ValidationError("El rango de fechas no puede superar 1 año. Acota fecha_inicio/fecha_fin.")

        ventas_qs = Venta.objects.filter(fecha_emision__date__gte=fi, fecha_emision__date__lte=ff)
        movimientos_qs = MovimientoCaja.objects.filter(
            fecha__date__gte=fi, fecha__date__lte=ff, estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO
        ).exclude(concepto__in=CONCEPTOS_TRANSFERENCIA_INTERNA)
        if sucursal_id:
            ventas_qs = ventas_qs.filter(sucursal_id=sucursal_id)
            movimientos_qs = movimientos_qs.filter(sesion__caja__sucursal_id=sucursal_id)

        ventas_pagadas = ventas_qs.filter(estado=Venta.Estado.PAGADA)
        ingresos_ventas = _totales_ventas_en_soles(ventas_pagadas)["total"]
        ingresos_taller = _totales_ventas_en_soles(
            ventas_pagadas.filter(ticket_kiosko__startswith="OT-")
        )["total"]
        ingresos_mostrador = ingresos_ventas - ingresos_taller

        generado_credito = _totales_ventas_en_soles(ventas_qs.filter(estado=Venta.Estado.AL_CREDITO))["total"]

        cero = Value(0, output_field=DecimalField(max_digits=14, decimal_places=2))
        cobros_credito = float(
            movimientos_qs.filter(
                tipo=MovimientoCaja.Tipo.INGRESO, concepto=MovimientoCaja.Concepto.COBRO_CUOTA
            ).aggregate(t=Coalesce(Sum("monto"), cero))["t"]
        )
        egresos_por_concepto = list(
            movimientos_qs.filter(tipo=MovimientoCaja.Tipo.EGRESO)
            .values("concepto")
            .annotate(total=Coalesce(Sum("monto"), cero))
            .order_by("-total")
        )
        egresado = sum(float(e["total"]) for e in egresos_por_concepto)

        ingresado = ingresos_ventas + cobros_credito
        neto = ingresado - egresado
        margen = (neto / ingresado * 100) if ingresado > 0 else 0

        # Serie diaria del rango completo, para graficar tendencia.
        ventas_por_dia = {
            v["dia"]: float(v["total"])
            for v in ventas_pagadas.annotate(dia=TruncDate("fecha_emision"))
            .values("dia")
            .annotate(
                total=Coalesce(
                    Sum(ExpressionWrapper(F("total") * F("tipo_cambio"), output_field=DecimalField(max_digits=14, decimal_places=2))),
                    cero,
                )
            )
        }
        cobros_por_dia = {
            v["dia"]: float(v["total"])
            for v in movimientos_qs.filter(
                tipo=MovimientoCaja.Tipo.INGRESO, concepto=MovimientoCaja.Concepto.COBRO_CUOTA
            )
            .annotate(dia=TruncDate("fecha"))
            .values("dia")
            .annotate(total=Coalesce(Sum("monto"), cero))
        }
        egresos_por_dia = {
            v["dia"]: float(v["total"])
            for v in movimientos_qs.filter(tipo=MovimientoCaja.Tipo.EGRESO)
            .annotate(dia=TruncDate("fecha"))
            .values("dia")
            .annotate(total=Coalesce(Sum("monto"), cero))
        }

        serie_diaria = []
        cursor = fi
        while cursor <= ff:
            ingresos_dia = ventas_por_dia.get(cursor, 0) + cobros_por_dia.get(cursor, 0)
            egresos_dia = egresos_por_dia.get(cursor, 0)
            serie_diaria.append({
                "fecha": cursor.isoformat(),
                "ingresos": round(ingresos_dia, 2),
                "egresos": round(egresos_dia, 2),
                "neto": round(ingresos_dia - egresos_dia, 2),
            })
            cursor += timedelta(days=1)

        ordenes_ingresadas = OrdenTrabajo.objects.exclude(estado=OrdenTrabajo.Estado.CANCELADO).filter(
            fecha_ingreso__date__gte=fi, fecha_ingreso__date__lte=ff
        ).count()

        data = {
            "fecha_inicio": fi.isoformat(),
            "fecha_fin": ff.isoformat(),
            "ingresado": round(ingresado, 2),
            "egresado": round(egresado, 2),
            "neto": round(neto, 2),
            "margen_porcentaje": round(margen, 2),
            "desglose_ingresos": {
                "ventas_taller": round(ingresos_taller, 2),
                "ventas_mostrador": round(ingresos_mostrador, 2),
                "cobros_credito": round(cobros_credito, 2),
            },
            "generado_credito": round(generado_credito, 2),
            "egresos_por_concepto": [
                {"concepto": e["concepto"], "total": round(float(e["total"]), 2)} for e in egresos_por_concepto
            ],
            "ordenes_ingresadas": ordenes_ingresadas,
            "serie_diaria": serie_diaria,
        }

        if formato in ("excel", "pdf"):
            headers = ["Fecha", "Ingresos", "Egresos", "Neto"]
            rows = [[d["fecha"], d["ingresos"], d["egresos"], d["neto"]] for d in serie_diaria]
            rows.append(["TOTAL", round(ingresado, 2), round(egresado, 2), round(neto, 2)])
            if formato == "excel":
                return _exportar_excel(headers, rows, "Reporte_Rentabilidad")
            rows_pdf = [[r[0], str(r[1]), str(r[2]), str(r[3])] for r in rows]
            return _exportar_pdf(f"Reporte de Rentabilidad ({fi.isoformat()} a {ff.isoformat()})", headers, rows_pdf)

        return Response(data)
