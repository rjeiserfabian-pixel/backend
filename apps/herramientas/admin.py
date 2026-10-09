from django.contrib import admin

from .models import CategoriaHerramienta, Herramienta, HistorialHerramienta

admin.site.register(CategoriaHerramienta)
admin.site.register(Herramienta)
admin.site.register(HistorialHerramienta)
