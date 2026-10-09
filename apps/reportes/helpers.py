"""
helpers.py — Funciones comunes de los reportes: rango de fechas, conversión a soles y
exportación a Excel/PDF. Se separaron de views.py por tamaño; su contenido no cambió.
"""
import logging
import io
from datetime import date

from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from django.http import HttpResponse
from rest_framework.exceptions import ValidationError

from apps.ventas.models import MovimientoCaja

logger = logging.getLogger(__name__)

# Transferencias entre cajas del mismo negocio: siempre se crean en pareja
# (misma transacción, mismo monto — ver apps/cajas/views.py), así que no son
# un ingreso/egreso real y deben excluirse de cualquier cálculo de
# ingresos/egresos del negocio (resumen del día, rentabilidad).
CONCEPTOS_TRANSFERENCIA_INTERNA = [
    MovimientoCaja.Concepto.TRANSFERENCIA_SALIENTE,
    MovimientoCaja.Concepto.TRANSFERENCIA_ENTRANTE,
]




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
