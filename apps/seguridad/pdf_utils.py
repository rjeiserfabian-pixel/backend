"""Utilidades compartidas para generar PDFs con xhtml2pdf en distintas apps."""
import base64
import logging
import mimetypes

logger = logging.getLogger(__name__)


def contexto_empresa_pdf():
    """
    Datos de la empresa configurada + su logo embebido como data URI.

    xhtml2pdf (a diferencia del navegador) no resuelve de forma confiable una
    ruta de archivo o una URL del propio servidor para cargar imágenes, así
    que el logo se incrusta directo en el HTML como base64.
    """
    from .models import Empresa

    empresa, _ = Empresa.objects.get_or_create(id=1, defaults={
        "razon_social": "Mi Empresa",
        "ruc": "00000000000",
        "direccion": "Dirección no configurada",
    })
    empresa_logo_data_uri = None
    if empresa.logo:
        try:
            with empresa.logo.open('rb') as f:
                logo_bytes = f.read()
            mime_type = mimetypes.guess_type(empresa.logo.name)[0] or 'image/png'
            empresa_logo_data_uri = f"data:{mime_type};base64,{base64.b64encode(logo_bytes).decode('ascii')}"
        except (OSError, ValueError) as e:
            logger.error(f"No se pudo incrustar el logo de la empresa en el PDF: {e}")

    return {'empresa': empresa, 'empresa_logo_data_uri': empresa_logo_data_uri}
