from django.core.management.base import BaseCommand, CommandError

from apps.inventario.models import Almacen, MarcaRepuesto, UbicacionFisica


MARCA_GENERICA = "Sin Marca"
CODIGO_UBICACION = "STOCK-INICIAL-EXCEL"
DESCRIPCION_UBICACION = "Stock inicial importado desde el inventario Excel"


class Command(BaseCommand):
    help = "Prepara la marca y ubicacion necesarias para importar repuestos desde Excel."

    def handle(self, *args, **options):
        almacen_principal = Almacen.objects.filter(
            estado=True,
            nombre__icontains="principal",
        ).first()
        if not almacen_principal:
            raise CommandError("No se encontro un Almacen Principal activo.")

        marca, marca_creada = MarcaRepuesto.objects.get_or_create(
            nombre=MARCA_GENERICA,
            defaults={"estado": True},
        )
        ubicacion, ubicacion_creada = UbicacionFisica.objects.get_or_create(
            almacen=almacen_principal,
            codigo=CODIGO_UBICACION,
            defaults={"descripcion": DESCRIPCION_UBICACION},
        )

        marca_estado = "creada" if marca_creada else "ya existente"
        ubicacion_estado = "creada" if ubicacion_creada else "ya existente"
        self.stdout.write(self.style.SUCCESS(
            f"Marca '{marca.nombre}': {marca_estado}."
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Ubicacion '{ubicacion.codigo}' en '{almacen_principal.nombre}': {ubicacion_estado}."
        ))
        self.stdout.write(
            "La importacion usara precios en 0.00 y movimientos INVENTARIO_INICIAL."
        )
