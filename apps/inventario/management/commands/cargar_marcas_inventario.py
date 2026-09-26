from django.core.management.base import BaseCommand

from apps.inventario.models import MarcaRepuesto


MARCAS_INVENTARIO = (
    "ACDelco",
    "Aktion",
    "Autoboss",
    "Beste",
    "Bravaria Filter Werke",
    "CAT",
    "CNH",
    "Coralfly",
    "Daruma",
    "Donaldson",
    "Energetic",
    "Evoparts",
    "Filpower",
    "Fleetguard",
    "FMX",
    "G&G Motors",
    "Gazpromneft",
    "G-Box",
    "General Parts",
    "G-Energy",
    "Great Wall",
    "Hino",
    "Hyundai",
    "Hyundai-Kia",
    "IHP Filters",
    "Jamo",
    "Jhoy Parts",
    "Knorr-Bremse",
    "Lider",
    "Liqui Moly",
    "Lubexol",
    "LYS Filtros",
    "Mahindra",
    "Mann Filter",
    "Marfak",
    "Mercedes-Benz",
    "Mitsubishi",
    "Mitsufil",
    "Motul",
    "Nissan",
    "Parker",
    "Parker Racor",
    "Plus Parts",
    "Purolator",
    "Renault Group",
    "Rund Filters",
    "Sakura",
    "Seineca",
    "STP",
    "Sukki Filters",
    "Suzuki",
    "Takumi",
    "Toto Water Pump",
    "Toyota",
    "Valvoline",
    "Vistony",
    "Volkswagen",
    "Wanlanda",
    "Wega",
    "Wix Filters",
    "Wolver",
    "Wuling Motors",
    "Wurth",
    "Yassian",
)


class Command(BaseCommand):
    help = "Carga las marcas normalizadas obtenidas del inventario Excel."

    def handle(self, *args, **options):
        creadas = []
        existentes = []

        for nombre in MARCAS_INVENTARIO:
            marca = MarcaRepuesto.objects.filter(nombre__iexact=nombre).first()
            if marca:
                existentes.append(marca.nombre)
                continue

            MarcaRepuesto.objects.create(nombre=nombre)
            creadas.append(nombre)

        if creadas:
            self.stdout.write(self.style.SUCCESS(
                f"Marcas creadas ({len(creadas)}): {', '.join(creadas)}"
            ))
        if existentes:
            self.stdout.write(
                f"Marcas ya registradas ({len(existentes)}): {', '.join(existentes)}"
            )
