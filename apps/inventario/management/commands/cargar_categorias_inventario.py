from django.core.management.base import BaseCommand

from apps.inventario.models import Categoria


CATEGORIAS_INVENTARIO = (
    "Filtros",
    "Aceites y Lubricantes",
    "Fluidos Vehiculares",
    "Motor y Distribucion",
    "Direccion Hidraulica",
    "Sistema Electrico y Encendido",
    "Proteccion y Limpieza",
    "Sistema Neumatico y Frenos",
)


class Command(BaseCommand):
    help = "Carga las categorias iniciales obtenidas del inventario Excel."

    def handle(self, *args, **options):
        creadas = []
        existentes = []

        for nombre in CATEGORIAS_INVENTARIO:
            categoria = Categoria.objects.filter(nombre__iexact=nombre).first()
            if categoria:
                existentes.append(categoria.nombre)
                continue

            Categoria.objects.create(nombre=nombre)
            creadas.append(nombre)

        if creadas:
            self.stdout.write(self.style.SUCCESS(
                f"Categorias creadas ({len(creadas)}): {', '.join(creadas)}"
            ))
        if existentes:
            self.stdout.write(
                f"Categorias ya registradas ({len(existentes)}): {', '.join(existentes)}"
            )
