from django.core.management.base import BaseCommand, CommandError

from apps.seguridad.acceso_publico import ConfiguracionAccesoPublicoSerializer, obtener_url_publica
from apps.seguridad.models import ConfiguracionAccesoPublico


class Command(BaseCommand):
    help = 'Lee o configura la direccion publica, sin modificar credenciales de Ngrok.'

    def add_arguments(self, parser):
        parser.add_argument('--url')

    def handle(self, *args, **options):
        if options['url'] is not None:
            registro = ConfiguracionAccesoPublico.objects.filter(pk=1).first()
            serializer = ConfiguracionAccesoPublicoSerializer(registro, data={'url_base': options['url']})
            if not serializer.is_valid():
                raise CommandError(str(serializer.errors))
            serializer.save(pk=1)
        self.stdout.write(obtener_url_publica())
