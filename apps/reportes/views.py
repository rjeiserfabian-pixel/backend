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

logger = logging.getLogger(__name__)

# Transferencias entre cajas del mismo negocio: siempre se crean en pareja
# (misma transacción, mismo monto — ver apps/cajas/views.py), así que no son
# un ingreso/egreso real y deben excluirse de cualquier cálculo de
# ingresos/egresos del negocio (resumen del día, rentabilidad).
CONCEPTOS_TRANSFERENCIA_INTERNA = [
    MovimientoCaja.Concepto.TRANSFERENCIA_SALIENTE,
    MovimientoCaja.Concepto.TRANSFERENCIA_ENTRANTE,
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date_range(request):
    """
    Lee 'fecha_inicio' y 'fecha_fin' del query string.
    Si no se envían, usa el primer y último día del mes actual.
    Si se envían pero con un formato inválido, rechaza la petición
    (antes se ignoraba en silencio y se usaba el mes actual, ocultando
    el error del usuario).
    Retorna dos objetos date.
    """
    today = date.today()
    fecha_inicio_str = request.query_params.get("fecha_inicio")
    fecha_fin_str = request.query_params.get("fecha_fin")
    try:
        fecha_inicio = date.fromisoformat(fecha_inicio_str) if fecha_inicio_str else today.replace(day=1)
        fecha_fin = date.fromisoformat(fecha_fin_str) if fecha_fin_str else today
    except ValueError:
        raise ValidationError("fecha_inicio y fecha_fin deben tener formato YYYY-MM-DD.")
    return fecha_inicio, fecha_fin


def _totales_ventas_en_soles(qs):
    """
    Suma subtotal/igv/total de un queryset de Venta convirtiendo cada fila
    a soles con su propio tipo_cambio (soles por unidad de moneda
    extranjera, 1.0000 para ventas en soles) antes de sumar. Sumar montos
    de monedas distintas sin convertir da un total sin sentido.
    """
    conversion = qs.aggregate(
        subtotal=Sum(
            ExpressionWrapper(F("subtotal") * F("tipo_cambio"), output_field=DecimalField(max_digits=14, decimal_places=2))
        ),
        igv=Sum(
            ExpressionWrapper(F("igv") * F("tipo_cambio"), output_field=DecimalField(max_digits=14, decimal_places=2))
        ),
        total=Sum(
            ExpressionWrapper(F("total") * F("tipo_cambio"), output_field=DecimalField(max_digits=14, decimal_places=2))
        ),
    )
    return {
        "subtotal": float(conversion["subtotal"] or 0),
        "igv": float(conversion["igv"] or 0),
        "total": float(conversion["total"] or 0),
    }


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

    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    from reportlab.platypus import Image
    from datetime import datetime
    from apps.seguridad.models import Empresa

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    elements = []

    # Obtener configuración de empresa
    empresa = Empresa.objects.first()
    
    # 1. Logo (Izquierda)
    logo_element = ""
    if empresa and empresa.logo:
        try:
            # reportlab Image soporta ruta de archivo local
            logo_img = Image(empresa.logo.path)
            # escalar a 2.5cm de alto aprox conservando la proporción
            aspect = logo_img.drawWidth / logo_img.drawHeight
            logo_img.drawHeight = 2.5 * cm
            logo_img.drawWidth = 2.5 * cm * aspect
            logo_element = logo_img
        except Exception:
            pass

    # 2. Título (Centro)
    titulo_element = Paragraph(title, styles["Title"])

    # 3. Empresa Info (Derecha)
    info_style = ParagraphStyle(
        name="InfoStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        textColor=colors.black,
        alignment=TA_RIGHT,
    )
    razon_social = empresa.razon_social if empresa else "Sistema de Gestión"
    fecha_actual = datetime.now().strftime("%d/%m/%Y %H:%M")
    info_text = f"<b>{razon_social}</b><br/>Fecha: {fecha_actual}"
    info_element = Paragraph(info_text, info_style)

    # Crear tabla para el encabezado (3 columnas sin bordes)
    ancho_total = landscape(A4)[0] - 3 * cm
    col_izq = 5 * cm
    col_der = 5 * cm
    col_cen = ancho_total - col_izq - col_der

    header_table = Table(
        [[logo_element, titulo_element, info_element]],
        colWidths=[col_izq, col_cen, col_der],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
    ]))
    
    elements.append(header_table)
    elements.append(Spacer(1, 0.5 * cm))

    # Estilos de párrafo para la tabla (permite salto de línea automático)
    header_style = ParagraphStyle(
        name="HeaderStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=colors.white,
        alignment=TA_CENTER,
    )
    
    row_style = ParagraphStyle(
        name="RowStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        textColor=colors.black,
        alignment=TA_CENTER,
    )

    # Envolver contenido en Paragraph
    wrapped_headers = [Paragraph(str(h), header_style) for h in headers]
    wrapped_rows = [[Paragraph(str(cell) if cell is not None else "", row_style) for cell in row] for row in rows]

    # Tabla
    table_data = [wrapped_headers] + wrapped_rows
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
