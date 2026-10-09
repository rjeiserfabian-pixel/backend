import tempfile
from pathlib import Path

from django.test import TestCase, override_settings


class MediaPublicaTests(TestCase):
    """
    El backend NO debe servir su carpeta raíz: solo las carpetas de imágenes públicas.
    (Antes, con DEBUG=True, /media/.env entregaba las claves del sistema a cualquiera.)
    """

    def setUp(self):
        self.carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(self.carpeta.cleanup)
        raiz = Path(self.carpeta.name)
        (raiz / 'empresa').mkdir()
        (raiz / 'empresa' / 'logo.png').write_bytes(b'png-de-prueba')
        (raiz / '.env').write_text('SECRET_KEY=secreto')
        (raiz / 'settings.py').write_text('SECRET = 1')
        configuracion = override_settings(MEDIA_ROOT=self.carpeta.name)
        configuracion.enable()
        self.addCleanup(configuracion.disable)

    def test_sirve_las_imagenes_de_las_carpetas_publicas(self):
        resp = self.client.get('/media/empresa/logo.png')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'png-de-prueba')

    def test_no_sirve_archivos_de_la_raiz_del_backend(self):
        for ruta in ('/media/.env', '/media/settings.py', '/media/'):
            self.assertEqual(self.client.get(ruta).status_code, 404, ruta)

    def test_no_se_puede_escapar_de_la_carpeta_publica_con_puntos(self):
        for ruta in ('/media/empresa/../.env', '/media/empresa/../../settings.py', '/media/empresa//../.env'):
            self.assertEqual(self.client.get(ruta).status_code, 404, ruta)
