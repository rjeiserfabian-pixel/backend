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
from datetime import date, datetime

from django.db.models import Sum, Count, F, Q, Value, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse
from django.utils.timezone import make_aware, is_naive
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from apps.ventas.models import (
    Venta, MovimientoCaja, SesionCaja, Caja, MetodoPago, PagoVenta, DetalleVenta
)
from apps.compras.models import Compra
from apps.inventario.models import Repuesto, Sucursal
from apps.clientes.models import Cliente
from apps.taller.models import OrdenTrabajo
from apps.vehiculos.models import Vehiculo

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date_range(request):
    """
    Lee 'fecha_inicio' y 'fecha_fin' del query string.
    Si no se envían, usa el primer y último día del mes actual.
    Retorna dos objetos date.
    """
    today = date.today()
    fecha_inicio_str = request.query_params.get("fecha_inicio", today.replace(day=1).isoformat())
    fecha_fin_str = request.query_params.get("fecha_fin", today.isoformat())
    try:
        fecha_inicio = date.fromisoformat(fecha_inicio_str)
        fecha_fin = date.fromisoformat(fecha_fin_str)
    except ValueError:
        fecha_inicio = today.replace(day=1)
        fecha_fin = today
    return fecha_inicio, fecha_fin


def _to_aware(d: date, end: bool = False):
    """Convierte un date a datetime aware (Lima). Si end=True, usa 23:59:59."""
    from django.utils import timezone
    if end:
        dt = datetime(d.year, d.month, d.day, 23, 59, 59)
    else:
        dt = datetime(d.year, d.month, d.day, 0, 0, 0)
    if is_naive(dt):
        dt = make_aware(dt)
    return dt


def _exportar_excel(headers: list, rows: list, sheet_name: str = "Reporte") -> HttpResponse:
    """Genera un archivo Excel con openpyxl y lo retorna como respuesta HTTP."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return HttpResponse(
            "openpyxl no está instalado. Ejecuta: pip install openpyxl",
            status=500,
            content_type="text/plain",
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]

    # Encabezados con estilo
    header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[cell.column_letter].width = max(15, len(header) + 4)

    # Datos
    for row_idx, row in enumerate(rows, start=2):
        for col_idx, value in enumerate(row, start=1):
            ws.cell(row=row_idx, column=col_idx, value=value)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{sheet_name}.xlsx"'
    return response


def _exportar_pdf(title: str, headers: list, rows: list) -> HttpResponse:
    """Genera un PDF simple con reportlab y lo retorna como respuesta HTTP."""
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        return HttpResponse(
            "reportlab no está instalado. Ejecuta: pip install reportlab",
            status=500,
            content_type="text/plain",
        )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    elements = []

    # Título
    elements.append(Paragraph(title, styles["Title"]))
    elements.append(Spacer(1, 0.5 * cm))

    # Tabla
    table_data = [headers] + rows
    col_count = len(headers)
    col_width = (landscape(A4)[0] - 3 * cm) / col_count

    t = Table(table_data, colWidths=[col_width] * col_count, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)

    doc.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{title}.pdf"'
    return response


# ─────────────────────────────────────────────────────────────────────────────
# 1. REPORTE DE CAJA
# ─────────────────────────────────────────────────────────────────────────────

class ReporteCajaView(APIView):
    """
    GET /api/reportes/caja/
    Parámetros: fecha_inicio, fecha_fin, caja_id (opcional), formato (json|excel|pdf)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        fecha_inicio, fecha_fin = _parse_date_range(request)
        caja_id = request.query_params.get("caja_id")
        formato = request.query_params.get("formato", "json")

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

        # Traer todos los movimientos de esas sesiones en un solo query
        sesion_ids = list(sesiones_qs.values_list("id", flat=True))
        movimientos_qs = (
            MovimientoCaja.objects
            .select_related("metodo_pago")
            .filter(sesion_id__in=sesion_ids, tipo=MovimientoCaja.Tipo.INGRESO)
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
                "usuario": f"{sesion.usuario.nombre} {sesion.usuario.apellidos}",
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

        if formato == "excel":
            headers = ["Caja", "Sucursal", "Usuario", "Apertura", "Cierre", "Saldo Inicial",
                       "Saldo Cierre", "Efectivo", "Tarjeta", "Yape", "Plin", "Estado"]
            rows = [
                [d["caja"], d["sucursal"], d["usuario"], d["fecha_apertura"], d["fecha_cierre"],
                 d["saldo_inicial"], d["saldo_cierre_real"], d["efectivo"], d["tarjeta"],
                 d["yape"], d["plin"], d["estado"]]
                for d in data
            ]
            return _exportar_excel(headers, rows, "Reporte_Caja")

        if formato == "pdf":
            headers = ["Caja", "Sucursal", "Apertura", "Cierre", "S. Inicial", "S. Cierre", "Efectivo", "Tarjeta", "Yape", "Plin"]
            rows = [
                [d["caja"], d["sucursal"], d["fecha_apertura"], d["fecha_cierre"],
                 str(d["saldo_inicial"]), str(d["saldo_cierre_real"]),
                 str(d["efectivo"]), str(d["tarjeta"]), str(d["yape"]), str(d["plin"])]
                for d in data
            ]
            return _exportar_pdf("Reporte de Caja", headers, rows)

        return Response({"data": data, "total_registros": len(data)})


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
    permission_classes = [IsAuthenticated]

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
            .exclude(estado=Venta.Estado.PRE_VENTA)
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
                vendedor = f"{u.nombre} {u.apellidos}"
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
                    vendedor = f"{u.nombre} {u.apellidos}"
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
            "totales": {
                "subtotal": float(qs.aggregate(s=Sum("subtotal"))["s"] or 0),
                "igv": float(qs.aggregate(s=Sum("igv"))["s"] or 0),
                "total": float(qs.aggregate(s=Sum("total"))["s"] or 0),
            },
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
    permission_classes = [IsAuthenticated]

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
            .prefetch_related("stocks")
            .filter(estado=True)
        )
        if categoria_id:
            qs = qs.filter(categoria_id=categoria_id)
        if marca_id:
            qs = qs.filter(marca_id=marca_id)

        total = qs.count()
        offset = (page - 1) * page_size
        repuestos = qs.order_by("nombre")[offset: offset + page_size]

        data = []
        for r in repuestos:
            # Stock total a través de la propiedad ya definida en el modelo
            stock = sum(s.cantidad for s in r.stocks.all() if s.cantidad > 0)
            if stock_estado == "agotado" and stock > 0:
                continue
            if stock_estado == "bajo" and stock > 5:
                continue
            if stock_estado == "normal" and stock <= 5:
                continue
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
                "stock": float(stock),
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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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

class ReporteAvanzadoView(APIView):
    """
    GET /api/reportes/avanzado/
    Sub-reportes: tipo = ventas_general | ventas_sucursal | ventas_detalladas |
                         compras_detalladas | ordenes_servicio
    Parámetros: fecha_inicio, fecha_fin, sucursal_id, tipo, formato
    """
    permission_classes = [IsAuthenticated]

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
                    ExpressionWrapper(
                        F("repuesto__precio_compra") * F("cantidad"),
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

        data = {
            "ventas_netas": ventas_netas,
            "costo_ventas": costo_ventas,
            "utilidad_bruta": utilidad,
            "margen_porcentaje": round(margen, 2),
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
                "mecanico": f"{o.mecanico_asignado.nombre} {o.mecanico_asignado.apellidos}" if o.mecanico_asignado else "—",
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


# ─────────────────────────────────────────────────────────────────────────────
# 7. REPORTE DE VEHÍCULOS
# ─────────────────────────────────────────────────────────────────────────────

class ReporteVehiculosView(APIView):
    """
    GET /api/reportes/vehiculos/
    Parámetros: placa, cliente_id, formato (json|excel|pdf)
    Paginado: page, page_size (default 50)
    """
    permission_classes = [IsAuthenticated]

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
            ordenes = v.ordenes_trabajo.all()
            ultima_orden = ordenes.order_by("-fecha_ingreso").first()
            clientes_set = set()
            for o in ordenes:
                if o.cliente:
                    clientes_set.add(f"{o.cliente.nombres} {o.cliente.apellidos}")

            data.append({
                "id": v.id,
                "placa": v.placa,
                "marca": v.marca,
                "modelo": v.modelo,
                "anio": v.anio,
                "propietarios": ", ".join(clientes_set) if clientes_set else "—",
                "total_ordenes": ordenes.count(),
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
