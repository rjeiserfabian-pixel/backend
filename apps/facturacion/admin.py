from django.contrib import admin

from .models import ComprobanteElectronico, ComprobanteElectronicoDetalle, ComprobanteEnvioLog


class ComprobanteEnvioLogInline(admin.TabularInline):
    model = ComprobanteEnvioLog
    extra = 0
    readonly_fields = ['endpoint', 'exitoso', 'mensaje', 'usuario', 'fecha']
    can_delete = False


class ComprobanteElectronicoDetalleInline(admin.TabularInline):
    model = ComprobanteElectronicoDetalle
    extra = 0


@admin.register(ComprobanteElectronico)
class ComprobanteElectronicoAdmin(admin.ModelAdmin):
    list_display = ['id', 'tipo_documento', 'serie', 'numero', 'estado', 'cliente_nombre', 'total', 'creado_en']
    list_filter = ['tipo_documento', 'estado', 'sucursal']
    search_fields = ['serie', 'numero', 'cliente_documento', 'cliente_nombre']
    inlines = [ComprobanteElectronicoDetalleInline, ComprobanteEnvioLogInline]
