import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone

# Los documentos (SOAT, licencias, tarjetas de propiedad) son datos personales:
# se guardan FUERA de MEDIA_ROOT y solo se entregan por un endpoint autenticado
# que verifica permisos, nunca por una URL pública /media/.
def almacen_privado():
    # Función (no instancia) para que las migraciones no guarden una ruta absoluta
    # de esta computadora, y el sistema funcione igual al moverlo a otro servidor.
    return FileSystemStorage(location=settings.BASE_DIR / 'archivos_privados')

DIAS_POR_VENCER = 30


def _ruta_archivo(instance, filename):
    extension = os.path.splitext(filename)[1].lower()
    carpeta = f'vehiculo_{instance.vehiculo_id}' if instance.vehiculo_id else f'cliente_{instance.cliente_id}'
    return f'{carpeta}/{uuid.uuid4().hex}{extension}'


class Documento(models.Model):
    """Documento de un vehículo O de un cliente, con vencimiento y archivo escaneado opcional."""

    class Tipo(models.TextChoices):
        SOAT = 'SOAT', 'SOAT'
        REVISION_TECNICA = 'REVISION_TECNICA', 'Revisión técnica'
        TARJETA_PROPIEDAD = 'TARJETA_PROPIEDAD', 'Tarjeta de propiedad'
        CERTIFICADO_GAS = 'CERTIFICADO_GAS', 'Certificado de gas (GNV/GLP)'
        LICENCIA_CONDUCIR = 'LICENCIA_CONDUCIR', 'Licencia de conducir'
        DOCUMENTO_IDENTIDAD = 'DOCUMENTO_IDENTIDAD', 'Copia de documento de identidad'
        CONTRATO = 'CONTRATO', 'Contrato / convenio'
        OTRO = 'OTRO', 'Otro'

    # Tipos permitidos según a quién pertenece el documento.
    TIPOS_VEHICULO = {'SOAT', 'REVISION_TECNICA', 'TARJETA_PROPIEDAD', 'CERTIFICADO_GAS', 'OTRO'}
    TIPOS_CLIENTE = {'LICENCIA_CONDUCIR', 'DOCUMENTO_IDENTIDAD', 'CONTRATO', 'OTRO'}

    vehiculo = models.ForeignKey(
        'vehiculos.Vehiculo', on_delete=models.CASCADE, null=True, blank=True, related_name='documentos'
    )
    cliente = models.ForeignKey(
        'clientes.Cliente', on_delete=models.CASCADE, null=True, blank=True, related_name='documentos'
    )
    tipo = models.CharField(max_length=25, choices=Tipo.choices, db_index=True)
    numero = models.CharField(max_length=60, blank=True, default='')
    entidad_emisora = models.CharField(max_length=120, blank=True, default='', help_text='Aseguradora, planta de revisión, etc.')
    fecha_emision = models.DateField(null=True, blank=True)
    fecha_vencimiento = models.DateField(null=True, blank=True, db_index=True)
    archivo = models.FileField(storage=almacen_privado, upload_to=_ruta_archivo, null=True, blank=True)
    nombre_archivo = models.CharField(max_length=200, blank=True, default='')
    observaciones = models.TextField(blank=True, default='')
    activo = models.BooleanField(default=True, db_index=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='documentos_creados'
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'documento'
        verbose_name = 'Documento'
        verbose_name_plural = 'Documentos'
        ordering = ['fecha_vencimiento', '-id']
        constraints = [
            # Exactamente uno: vehículo o cliente.
            models.CheckConstraint(
                condition=(
                    models.Q(vehiculo__isnull=False, cliente__isnull=True)
                    | models.Q(vehiculo__isnull=True, cliente__isnull=False)
                ),
                name='documento_vehiculo_o_cliente',
            ),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.numero}'.strip()

    @property
    def dias_para_vencer(self):
        if not self.fecha_vencimiento:
            return None
        return (self.fecha_vencimiento - timezone.localdate()).days

    @property
    def estado_vigencia(self):
        dias = self.dias_para_vencer
        if dias is None:
            return 'SIN_VENCIMIENTO'
        if dias < 0:
            return 'VENCIDO'
        if dias <= DIAS_POR_VENCER:
            return 'POR_VENCER'
        return 'VIGENTE'

    @staticmethod
    def limite_por_vencer():
        return timezone.localdate() + timedelta(days=DIAS_POR_VENCER)
