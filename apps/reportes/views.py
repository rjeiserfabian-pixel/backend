"""
views.py — Módulo de Reportes
Cada vista devuelve JSON para visualización en pantalla.
Para exportación (PDF/Excel) se usan action endpoints separados.
Reglas aplicadas:
- select_related/prefetch_related para evitar N+1.
- Filtros siempre en la BD, nunca en Python.
- Paginación en vistas de listado.
- Ningún secreto hardcodeado.
- transaction.atomic() donde aplica.
"""
import logging
import io
from datetime import date, timedelta

from django.db.models import Sum, Count, F, Q, Value, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework import status

from apps.ventas.models import (
    Venta, MovimientoCaja, SesionCaja, Caja, MetodoPago, PagoVenta, DetalleVenta
)
from apps.compras.models import Compra
from apps.inventario.models import Repuesto, Sucursal
from apps.clientes.models import Cliente
from apps.taller.models import OrdenTrabajo
from apps.vehiculos.models import Vehiculo
from apps.seguridad.permissions import TienePermiso

from .helpers import CONCEPTOS_TRANSFERENCIA_INTERNA  # noqa: F401  (se re-exporta: otros módulos lo usan desde aquí)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. REPORTE DE CAJA
# ─────────────────────────────────────────────────────────────────────────────

class ReporteCajaView(APIView):
    """
    GET /api/reportes/caja/
    Parámetros: fecha_inicio, fecha_fin, caja_id (opcional), formato (json|excel|pdf)
    """
    def get_permissions(self):
        if self.request.query_params.get('formato') in ('excel', 'pdf'):
            return [TienePermiso("REPORTES.CAJA.EXPORTAR")]
        return [TienePermiso("REPORTES.CAJA.VER")]

    def get(self, request):
        fecha_inicio, fecha_fin = _parse_date_range(request)
        caja_id = request.query_params.get("caja_id")
        formato = request.query_params.get("formato", "json")
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)

        # Filtrar sesiones en el rango de fechas
        sesiones_qs = (
            SesionCaja.objects
            .select_related("caja", "caja__sucursal", "usuario")
            .filter(
                fecha_apertura__date__gte=fecha_inicio,
                fecha_apertura__date__lte=fecha_fin,
            )
        )
        if caja_id:
            sesiones_qs = sesiones_qs.filter(caja_id=caja_id)
        sesiones_qs = sesiones_qs.order_by("-fecha_apertura")

        if formato in ("excel", "pdf"):
            # Para exportar traemos todas las sesiones sin paginar (límite 5000)
            data = self._construir_data(sesiones_qs[:5000])
            headers = ["Caja", "Sucursal", "Usuario", "Apertura", "Cierre", "Saldo Inicial",
                       "Saldo Cierre", "Efectivo", "Tarjeta", "Yape", "Plin", "Estado"]
            if formato == "excel":
                rows = [
                    [d["caja"], d["sucursal"], d["usuario"], d["fecha_apertura"], d["fecha_cierre"],
                     d["saldo_inicial"], d["saldo_cierre_real"], d["efectivo"], d["tarjeta"],
                     d["yape"], d["plin"], d["estado"]]
                    for d in data
                ]
                return _exportar_excel(headers, rows, "Reporte_Caja")
            headers_pdf = ["Caja", "Sucursal", "Apertura", "Cierre", "S. Inicial", "S. Cierre", "Efectivo", "Tarjeta", "Yape", "Plin"]
            rows = [
                [d["caja"], d["sucursal"], d["fecha_apertura"], d["fecha_cierre"],
                 str(d["saldo_inicial"]), str(d["saldo_cierre_real"]),
                 str(d["efectivo"]), str(d["tarjeta"]), str(d["yape"]), str(d["plin"])]
                for d in data
            ]
            return _exportar_pdf("Reporte de Caja", headers_pdf, rows)

        total = sesiones_qs.count()
        offset = (page - 1) * page_size
        data = self._construir_data(sesiones_qs[offset: offset + page_size])

        return Response({
            "data": data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        })

    def _construir_data(self, sesiones_qs):
        # Traer todos los movimientos APROBADOS de esas sesiones en un solo query.
        # Un movimiento pendiente o rechazado no debe inflar el saldo mostrado.
        sesion_ids = [s.id for s in sesiones_qs]
        movimientos_qs = (
            MovimientoCaja.objects
            .select_related("metodo_pago")
            .filter(
                sesion_id__in=sesion_ids,
                tipo=MovimientoCaja.Tipo.INGRESO,
                estado_movimiento=MovimientoCaja.EstadoMovimiento.APROBADO,
            )
            .values("sesion_id", "metodo_pago__nombre")
            .annotate(total=Sum("monto"), cantidad=Count("id"))
        )

        # Indexar movimientos por sesión y método de pago
        mov_index = {}
        for m in movimientos_qs:
            sid = m["sesion_id"]
            if sid not in mov_index:
                mov_index[sid] = {}
            mov_index[sid][m["metodo_pago__nombre"].upper()] = {
                "total": float(m["total"]),
                "cantidad": m["cantidad"],
            }

        data = []
        for sesion in sesiones_qs:
            movs = mov_index.get(sesion.id, {})
            data.append({
                "id_sesion": sesion.id,
                "caja": sesion.caja.nombre,
                "sucursal": sesion.caja.sucursal.nombre,
                "usuario": f"{sesion.usuario.nombres} {sesion.usuario.apellidos}",
                "fecha_apertura": sesion.fecha_apertura.strftime("%d/%m/%Y %H:%M"),
                "fecha_cierre": sesion.fecha_cierre.strftime("%d/%m/%Y %H:%M") if sesion.fecha_cierre else "Abierta",
                "saldo_inicial": float(sesion.saldo_inicial),
                "saldo_cierre_real": float(sesion.saldo_cierre_real),
                "efectivo": movs.get("EFECTIVO", {}).get("total", 0),
                "tarjeta": movs.get("TARJETA", {}).get("total", 0),
                "yape": movs.get("YAPE", {}).get("total", 0),
                "plin": movs.get("PLIN", {}).get("total", 0),
                "estado": sesion.estado,
            })
        return data


# ─────────────────────────────────────────────────────────────────────────────
# 2. REPORTE DE VENTAS
# ─────────────────────────────────────────────────────────────────────────────

class ReporteVentasView(APIView):
    """
    GET /api/reportes/ventas/
    Parámetros: fecha_inicio, fecha_fin, sucursal_id, cliente_id,
                vendedor_id, producto_id, formato (json|excel|pdf)
    Paginado: page, page_size (default 50)
    """
    def get_permissions(self):
        if self.request.query_params.get('formato') in ('excel', 'pdf'):
            return [TienePermiso("REPORTES.VENTAS.EXPORTAR")]
        return [TienePermiso("REPORTES.VENTAS.VER")]

    def get(self, request):
        fecha_inicio, fecha_fin = _parse_date_range(request)
        sucursal_id = request.query_params.get("sucursal_id")
        cliente_id = request.query_params.get("cliente_id")
        vendedor_id = request.query_params.get("vendedor_id")
        producto_id = request.query_params.get("producto_id")
        formato = request.query_params.get("formato", "json")
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)

        qs = (
            Venta.objects
            .select_related(
                "cliente",
                "tipo_comprobante",
                "sucursal",
                "sesion_caja__usuario",
            )
            .exclude(estado__in=[Venta.Estado.PRE_VENTA, Venta.Estado.ANULADA])
            .filter(
                fecha_emision__date__gte=fecha_inicio,
                fecha_emision__date__lte=fecha_fin,
            )
        )

        if sucursal_id:
            qs = qs.filter(sucursal_id=sucursal_id)
        if cliente_id:
            qs = qs.filter(cliente_id=cliente_id)
        if vendedor_id:
            qs = qs.filter(sesion_caja__usuario_id=vendedor_id)
        if producto_id:
            qs = qs.filter(detalles__repuesto_id=producto_id).distinct()

        total = qs.count()
        offset = (page - 1) * page_size
        ventas = qs.order_by("-fecha_emision")[offset: offset + page_size]

        data = []
        for v in ventas:
            vendedor = ""
            if v.sesion_caja and v.sesion_caja.usuario:
                u = v.sesion_caja.usuario
                vendedor = f"{u.nombres} {u.apellidos}"
            data.append({
                "id": v.id,
                "fecha_emision": v.fecha_emision.strftime("%d/%m/%Y") if v.fecha_emision else "",
                "tipo_comprobante": v.tipo_comprobante.nombre if v.tipo_comprobante else "—",
                "serie_correlativo": v.serie_correlativo or "—",
                "cliente_dni": v.cliente.dni,
                "cliente_nombre": f"{v.cliente.nombres} {v.cliente.apellidos}",
                "vendedor": vendedor,
                "moneda": v.moneda,
                "subtotal": float(v.subtotal),
                "igv": float(v.igv),
                "total": float(v.total),
                "estado": v.estado,
                "sucursal": v.sucursal.nombre,
            })

        if formato in ("excel", "pdf"):
            # Para exportar traemos todos los resultados sin paginar (límite 5000)
            all_ventas = qs.order_by("-fecha_emision")[:5000]
            all_data = []
            for v in all_ventas:
                vendedor = ""
                if v.sesion_caja and v.sesion_caja.usuario:
                    u = v.sesion_caja.usuario
                    vendedor = f"{u.nombres} {u.apellidos}"
                all_data.append([
                    v.fecha_emision.strftime("%d/%m/%Y") if v.fecha_emision else "",
                    v.tipo_comprobante.nombre if v.tipo_comprobante else "—",
                    v.serie_correlativo or "—",
                    v.cliente.dni,
                    f"{v.cliente.nombres} {v.cliente.apellidos}",
                    vendedor,
                    v.moneda,
                    float(v.subtotal),
                    float(v.igv),
                    float(v.total),
                    v.estado,
                    v.sucursal.nombre,
                ])
            headers = ["Fecha", "Tipo Comp.", "Serie-Correlativo", "DNI Cliente",
                       "Cliente", "Vendedor", "Moneda", "Subtotal", "IGV", "Total", "Estado", "Sucursal"]
            if formato == "excel":
                return _exportar_excel(headers, all_data, "Reporte_Ventas")
            return _exportar_pdf("Reporte de Ventas", headers, all_data)

        return Response({
            "data": data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            # Ventas en distintas monedas se convierten a soles usando el
            # tipo_cambio guardado en cada venta antes de sumar (nunca se
            # deben sumar montos de monedas distintas directamente).
            "totales": _totales_ventas_en_soles(qs),
        })


# ─────────────────────────────────────────────────────────────────────────────
# 3. REPORTE DE PRODUCTOS (INVENTARIO)
# ─────────────────────────────────────────────────────────────────────────────

class ReporteProductosView(APIView):
    """
    GET /api/reportes/productos/
    Parámetros: categoria_id, marca_id, sucursal_id, stock_estado (bajo|normal|agotado),
                formato (json|excel|pdf)
    Paginado: page, page_size (default 50)
    """
    def get_permissions(self):
        if self.request.query_params.get('formato') in ('excel', 'pdf'):
            return [TienePermiso("REPORTES.PRODUCTOS.EXPORTAR")]
        return [TienePermiso("REPORTES.PRODUCTOS.VER")]

    def get(self, request):
        categoria_id = request.query_params.get("categoria_id")
        marca_id = request.query_params.get("marca_id")
        stock_estado = request.query_params.get("stock_estado")
        formato = request.query_params.get("formato", "json")
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)

        qs = (
            Repuesto.objects
            .select_related("categoria", "marca", "unidad_medida")
            .filter(estado=True)
            .annotate(
                stock_total=Coalesce(
                    Sum("inventario_stock__stock_disponible"),
                    Value(0),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )
        )
        if categoria_id:
            qs = qs.filter(categoria_id=categoria_id)
        if marca_id:
            qs = qs.filter(marca_id=marca_id)
        if stock_estado == "agotado":
            qs = qs.filter(stock_total__lte=0)
        elif stock_estado == "bajo":
            qs = qs.filter(stock_total__gt=0, stock_total__lte=5)
        elif stock_estado == "normal":
            qs = qs.filter(stock_total__gt=5)

        total = qs.count()
        offset = (page - 1) * page_size
        repuestos = qs.order_by("nombre")[offset: offset + page_size]

        data = []
        for r in repuestos:
            data.append({
                "id": r.id,
                "codigo": r.codigo,
                "nombre": r.nombre,
                "categoria": r.categoria.nombre,
                "marca": r.marca.nombre,
                "unidad": r.unidad_medida.abreviatura if r.unidad_medida else "—",
                "precio_compra": float(r.precio_compra),
                "precio_lista": float(r.precio_lista),
                "precio_cash": float(r.precio_cash),
                "stock": float(r.stock_total),
                "alerta_precio": r.alerta_precio,
            })

        if formato == "excel":
            headers = ["Código", "Nombre", "Categoría", "Marca", "Unidad",
                       "P. Compra", "P. Lista", "P. Cash", "Stock", "Alerta"]
            rows = [
                [d["codigo"], d["nombre"], d["categoria"], d["marca"], d["unidad"],
                 d["precio_compra"], d["precio_lista"], d["precio_cash"], d["stock"],
                 "Sí" if d["alerta_precio"] else "No"]
                for d in data
            ]
            return _exportar_excel(headers, rows, "Reporte_Productos")

        if formato == "pdf":
            headers = ["Código", "Nombre", "Categoría", "Marca", "P. Compra", "P. Lista", "Stock"]
            rows = [
                [d["codigo"], d["nombre"], d["categoria"], d["marca"],
                 str(d["precio_compra"]), str(d["precio_lista"]), str(d["stock"])]
                for d in data
            ]
            return _exportar_pdf("Reporte de Productos", headers, rows)

        return Response({
            "data": data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        })


# ─────────────────────────────────────────────────────────────────────────────
# 4. REPORTE DE CLIENTES
# ─────────────────────────────────────────────────────────────────────────────

class ReporteClientesView(APIView):
    """
    GET /api/reportes/clientes/
    Parámetros: fecha_inicio, fecha_fin, deuda_estado (con_saldo|al_dia), formato
    Paginado: page, page_size (default 50)
    """
    def get_permissions(self):
        if self.request.query_params.get('formato') in ('excel', 'pdf'):
            return [TienePermiso("REPORTES.CLIENTES.EXPORTAR")]
        return [TienePermiso("REPORTES.CLIENTES.VER")]

    def get(self, request):
        fecha_inicio, fecha_fin = _parse_date_range(request)
        deuda_estado = request.query_params.get("deuda_estado")
        formato = request.query_params.get("formato", "json")
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)

        qs = (
            Cliente.objects
            .filter(estado=True)
            .annotate(
                total_comprado=Coalesce(
                    Sum(
                        "ventas__total",
                        filter=Q(
                            ventas__fecha_emision__date__gte=fecha_inicio,
                            ventas__fecha_emision__date__lte=fecha_fin,
                        ) & ~Q(ventas__estado=Venta.Estado.ANULADA)
                    ),
                    Value(0, output_field=DecimalField()),
                ),
                saldo_pendiente=Coalesce(
                    Sum(
                        "ventas__cuenta_por_cobrar__saldo_pendiente",
                        filter=~Q(ventas__estado=Venta.Estado.ANULADA)
                    ),
                    Value(0, output_field=DecimalField()),
                ),
            )
        )

        if deuda_estado == "con_saldo":
            qs = qs.filter(saldo_pendiente__gt=0)
        elif deuda_estado == "al_dia":
            qs = qs.filter(saldo_pendiente=0)

        total = qs.count()
        offset = (page - 1) * page_size
        clientes = qs.order_by("-total_comprado")[offset: offset + page_size]

        data = []
        for c in clientes:
            data.append({
                "id": c.id,
                "dni": c.dni,
                "nombre": f"{c.nombres} {c.apellidos}",
                "telefono": c.telefono or "—",
                "email": c.email or "—",
                "total_comprado": float(c.total_comprado),
                "saldo_pendiente": float(c.saldo_pendiente),
            })

        if formato == "excel":
            headers = ["DNI", "Nombre", "Teléfono", "Email", "Total Comprado", "Saldo Pendiente"]
            rows = [
                [d["dni"], d["nombre"], d["telefono"], d["email"],
                 d["total_comprado"], d["saldo_pendiente"]]
                for d in data
            ]
            return _exportar_excel(headers, rows, "Reporte_Clientes")

        if formato == "pdf":
            headers = ["DNI", "Nombre", "Teléfono", "Total Comprado", "Saldo Pendiente"]
            rows = [
                [d["dni"], d["nombre"], d["telefono"],
                 str(d["total_comprado"]), str(d["saldo_pendiente"])]
                for d in data
            ]
            return _exportar_pdf("Reporte de Clientes", headers, rows)

        return Response({
            "data": data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        })


# ─────────────────────────────────────────────────────────────────────────────
# 5. REPORTE DE COMPRAS
# ─────────────────────────────────────────────────────────────────────────────

class ReporteComprasView(APIView):
    """
    GET /api/reportes/compras/
    Parámetros: fecha_inicio, fecha_fin, proveedor_id, estado_pago (Pendiente|Pagada|Parcial),
                formato (json|excel|pdf)
    Paginado: page, page_size (default 50)
    """
    def get_permissions(self):
        if self.request.query_params.get('formato') in ('excel', 'pdf'):
            return [TienePermiso("REPORTES.COMPRAS.EXPORTAR")]
        return [TienePermiso("REPORTES.COMPRAS.VER")]

    def get(self, request):
        fecha_inicio, fecha_fin = _parse_date_range(request)
        proveedor_id = request.query_params.get("proveedor_id")
        estado_pago = request.query_params.get("estado_pago")
        formato = request.query_params.get("formato", "json")
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)

        qs = (
            Compra.objects
            .select_related("proveedor", "tipo_comprobante_fk", "usuario")
            .prefetch_related("cuenta_por_pagar")
            .exclude(estado='Anulada')
            .filter(
                fecha_emision__gte=fecha_inicio,
                fecha_emision__lte=fecha_fin,
            )
        )
        if proveedor_id:
            qs = qs.filter(proveedor_id=proveedor_id)
        if estado_pago:
            qs = qs.filter(cuenta_por_pagar__estado=estado_pago)

        total = qs.count()
        offset = (page - 1) * page_size
        compras = qs.order_by("-fecha_emision")[offset: offset + page_size]

        data = []
        for c in compras:
            cpp = getattr(c, "cuenta_por_pagar", None)
            data.append({
                "id": c.id,
                "fecha": c.fecha_emision.strftime("%d/%m/%Y") if c.fecha_emision else "—",
                "proveedor_doc": c.proveedor.numero_documento,
                "proveedor": c.proveedor.nombre_o_razon_social,
                "tipo_comprobante": c.tipo_comprobante_fk.nombre if c.tipo_comprobante_fk else "—",
                "serie": c.serie,
                "numero": c.numero_comprobante,
                "tipo_pago": c.tipo_pago,
                "subtotal": float(c.subtotal),
                "igv": float(c.igv),
                "total": float(c.total),
                "estado": c.estado,
                "estado_pago": cpp.estado if cpp else "—",
                "saldo_pendiente": float(cpp.saldo_pendiente) if cpp else 0,
            })

        if formato == "excel":
            headers = ["Fecha", "RUC/DNI Proveedor", "Proveedor", "Tipo Comp.",
                       "Serie", "Número", "Tipo Pago", "Subtotal", "IGV",
                       "Total", "Estado Compra", "Estado Pago", "Saldo"]
            rows = [
                [d["fecha"], d["proveedor_doc"], d["proveedor"], d["tipo_comprobante"],
                 d["serie"], d["numero"], d["tipo_pago"], d["subtotal"], d["igv"],
                 d["total"], d["estado"], d["estado_pago"], d["saldo_pendiente"]]
                for d in data
            ]
            return _exportar_excel(headers, rows, "Reporte_Compras")

        if formato == "pdf":
            headers = ["Fecha", "Proveedor", "Comprobante", "Subtotal", "IGV", "Total", "Est. Pago", "Saldo"]
            rows = [
                [d["fecha"], d["proveedor"], f"{d['serie']}-{d['numero']}",
                 str(d["subtotal"]), str(d["igv"]), str(d["total"]),
                 d["estado_pago"], str(d["saldo_pendiente"])]
                for d in data
            ]
            return _exportar_pdf("Reporte de Compras", headers, rows)

        return Response({
            "data": data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "totales": {
                "subtotal": float(qs.aggregate(s=Sum("subtotal"))["s"] or 0),
                "igv": float(qs.aggregate(s=Sum("igv"))["s"] or 0),
                "total": float(qs.aggregate(s=Sum("total"))["s"] or 0),
            },
        })


# ─────────────────────────────────────────────────────────────────────────────
# 6. REPORTE AVANZADO
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# 7. REPORTE DE VEHÍCULOS
# ─────────────────────────────────────────────────────────────────────────────

class ReporteVehiculosView(APIView):
    """
    GET /api/reportes/vehiculos/
    Parámetros: placa, cliente_id, formato (json|excel|pdf)
    Paginado: page, page_size (default 50)
    """
    def get_permissions(self):
        if self.request.query_params.get('formato') in ('excel', 'pdf'):
            return [TienePermiso("REPORTES.VEHICULO.EXPORTAR")]
        return [TienePermiso("REPORTES.VEHICULO.VER")]

    def get(self, request):
        placa = request.query_params.get("placa", "").strip().upper()
        cliente_id = request.query_params.get("cliente_id")
        formato = request.query_params.get("formato", "json")
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 200)

        qs = (
            Vehiculo.objects
            .prefetch_related("ordenes_trabajo__cliente")
            .filter(estado=True)
        )
        if placa:
            qs = qs.filter(placa__icontains=placa)
        if cliente_id:
            qs = qs.filter(ordenes_trabajo__cliente_id=cliente_id).distinct()

        total = qs.count()
        offset = (page - 1) * page_size
        vehiculos = qs.order_by("placa")[offset: offset + page_size]

        data = []
        for v in vehiculos:
            # Materializado una sola vez: re-encadenar .order_by()/.count() sobre
            # la relación invalida el prefetch_related y dispara 2 queries extra
            # por vehículo.
            ordenes = list(v.ordenes_trabajo.all())
            ultima_orden = max(ordenes, key=lambda o: o.fecha_ingreso) if ordenes else None
            clientes_set = set()
            for o in ordenes:
                if o.cliente:
                    clientes_set.add(f"{o.cliente.nombres} {o.cliente.apellidos}")

            data.append({
                "id": v.id,
                "placa": v.placa,
                "marca": v.marca,
                "modelo": v.modelo,
                "anio": v.anio_fabricacion,
                "propietarios": ", ".join(clientes_set) if clientes_set else "—",
                "total_ordenes": len(ordenes),
                "ultimo_ingreso": ultima_orden.fecha_ingreso.strftime("%d/%m/%Y") if ultima_orden else "—",
                "ultimo_estado": ultima_orden.estado if ultima_orden else "—",
            })

        if formato == "excel":
            headers = ["Placa", "Marca", "Modelo", "Año", "Propietario(s)",
                       "Total OT", "Último Ingreso", "Último Estado"]
            rows = [
                [d["placa"], d["marca"], d["modelo"], d["anio"], d["propietarios"],
                 d["total_ordenes"], d["ultimo_ingreso"], d["ultimo_estado"]]
                for d in data
            ]
            return _exportar_excel(headers, rows, "Reporte_Vehiculos")

        if formato == "pdf":
            headers = ["Placa", "Marca", "Modelo", "Año", "Total OT", "Último Ingreso"]
            rows = [
                [d["placa"], d["marca"], d["modelo"], str(d["anio"]),
                 str(d["total_ordenes"]), d["ultimo_ingreso"]]
                for d in data
            ]
            return _exportar_pdf("Reporte de Vehículos", headers, rows)

        return Response({
            "data": data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        })


# ─────────────────────────────────────────────────────────────────────────────
# 7B. REPORTE DE KIOSKOS
# ─────────────────────────────────────────────────────────────────────────────

class ReporteKioskosView(APIView):
    """
    GET /api/reportes/kioskos/
    Parámetros: fecha_inicio, fecha_fin, sucursal_id, formato (json|excel|pdf)
    Agrupa las ventas cobradas por kiosko de origen: cuántos tickets generó
    cada terminal y cuánto vendió, para que el dueño del taller sepa qué
    kiosko se usa más (y detecte uno que dejó de generar ventas).
    """
    def get_permissions(self):
        if self.request.query_params.get('formato') in ('excel', 'pdf'):
            return [TienePermiso("REPORTES.KIOSKOS.EXPORTAR")]
        return [TienePermiso("REPORTES.KIOSKOS.VER")]

    def get(self, request):
        fecha_inicio, fecha_fin = _parse_date_range(request)
        sucursal_id = request.query_params.get("sucursal_id")
        formato = request.query_params.get("formato", "json")

        qs = (
            Venta.objects
            .filter(
                kiosko__isnull=False,
                fecha_emision__date__gte=fecha_inicio,
                fecha_emision__date__lte=fecha_fin,
            )
            .exclude(estado__in=[Venta.Estado.PRE_VENTA, Venta.Estado.ANULADA])
        )
        if sucursal_id:
            qs = qs.filter(sucursal_id=sucursal_id)

        total_monto = ExpressionWrapper(F("total") * F("tipo_cambio"), output_field=DecimalField(max_digits=14, decimal_places=2))

        agrupado = (
            qs.values("kiosko_id", "kiosko__nombre", "kiosko__sucursal__nombre")
            .annotate(
                total_tickets=Count("id"),
                total_vendido=Coalesce(Sum(total_monto), Value(0), output_field=DecimalField(max_digits=14, decimal_places=2)),
            )
            .order_by("-total_vendido")
        )

        data = []
        for row in agrupado:
            total_tickets = row["total_tickets"]
            total_vendido = float(row["total_vendido"] or 0)
            data.append({
                "kiosko_id": row["kiosko_id"],
                "kiosko_nombre": row["kiosko__nombre"] or "—",
                "sucursal": row["kiosko__sucursal__nombre"] or "—",
                "total_tickets": total_tickets,
                "total_vendido": total_vendido,
                "ticket_promedio": round(total_vendido / total_tickets, 2) if total_tickets else 0,
            })

        resumen = {
            "total_tickets": sum(d["total_tickets"] for d in data),
            "total_vendido": round(sum(d["total_vendido"] for d in data), 2),
        }

        if formato in ("excel", "pdf"):
            headers = ["Kiosko", "Sucursal", "Tickets", "Total Vendido (S/)", "Ticket Promedio (S/)"]
            rows = [
                [d["kiosko_nombre"], d["sucursal"], d["total_tickets"], d["total_vendido"], d["ticket_promedio"]]
                for d in data
            ]
            if formato == "excel":
                return _exportar_excel(headers, rows, "Reporte_Kioskos")
            return _exportar_pdf("Reporte de Kioskos", headers, rows)

        return Response({"data": data, "resumen": resumen})


# ─────────────────────────────────────────────────────────────────────────────
# 8. FILTROS AUXILIARES (para los selectores del frontend)
# ─────────────────────────────────────────────────────────────────────────────

class FiltrosAuxiliaresView(APIView):
    """
    GET /api/reportes/filtros/
    Retorna las listas de cajas, sucursales, categorías, marcas, clientes y proveedores
    para poblar los selectores del frontend en un solo request.
    Nota: solo retorna id + nombre para minimizar el payload.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.inventario.models import Categoria, MarcaRepuesto

        cajas = list(
            Caja.objects.select_related("sucursal").filter(estado=True)
            .values("id", "nombre", "sucursal__nombre")
        )
        sucursales = list(
            Sucursal.objects.filter(estado=True).values("id", "nombre")
        )
        categorias = list(
            Categoria.objects.filter(estado=True).values("id", "nombre")
        )
        marcas = list(
            MarcaRepuesto.objects.filter(estado=True).values("id", "nombre")
        )
        clientes = list(
            Cliente.objects.filter(estado=True).values("id", "dni", "nombres", "apellidos")[:500]
        )
        from apps.clientes.models import Proveedor
        proveedores = list(
            Proveedor.objects.filter(estado=True).values("id", "numero_documento", "nombre_o_razon_social")[:500]
        )

        return Response({
            "cajas": cajas,
            "sucursales": sucursales,
            "categorias": categorias,
            "marcas": marcas,
            "clientes": [
                {"id": c["id"], "label": f"{c['dni']} - {c['nombres']} {c['apellidos']}"}
                for c in clientes
            ],
            "proveedores": [
                {"id": p["id"], "label": f"{p['numero_documento']} - {p['nombre_o_razon_social']}"}
                for p in proveedores
            ],
        })


# Los bloques movidos se re-exportan para que `from .views import ...` siga funcionando.
from .helpers import (  # noqa: E402,F401
    _parse_date_range,
    _totales_ventas_en_soles,
    _exportar_excel,
    _exportar_pdf,
)
from .avanzado_views import (  # noqa: E402,F401
    ReporteAvanzadoView,
)
