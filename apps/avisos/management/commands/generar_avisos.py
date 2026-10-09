from django.core.management.base import BaseCommand

from apps.avisos.servicios import generar_avisos


class Command(BaseCommand):
    help = (
        'Genera los avisos pendientes a clientes (citas, vehículos listos, cotizaciones, mantenimientos, '
        'documentos y cuotas). Es seguro ejecutarlo varias veces al día; se puede programar en el '
        'Programador de tareas de Windows.'
    )

    def handle(self, *args, **options):
        resultado = generar_avisos()
        self.stdout.write(self.style.SUCCESS(
            f"Avisos nuevos: {resultado['creados']} | descartados por ya no aplicar: {resultado['descartados']}"
        ))
