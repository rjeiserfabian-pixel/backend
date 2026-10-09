import io
import os
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario
from apps.ventas.models import MetodoPago


def _imagen(formato='PNG', tamano=(40, 40), nombre='qr.png'):
    buffer = io.BytesIO()
    Image.new('RGB', tamano, 'white').save(buffer, formato)
    return SimpleUploadedFile(nombre, buffer.getvalue(), content_type=f'image/{formato.lower()}')


class QRMetodoPagoTests(TestCase):
    """QR de cobro (Yape/Plin) por método de pago: se sube aparte, se valida y no rompe el CRUD."""

    def setUp(self):
        self.carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(self.carpeta.cleanup)
        configuracion = override_settings(MEDIA_ROOT=self.carpeta.name)
        configuracion.enable()
        self.addCleanup(configuracion.disable)

        self.admin = Usuario.objects.create_superuser(
            username='admin_qr_test', email='admin_qr_test@example.com',
            nombres='Admin', apellidos='QrTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.metodo = MetodoPago.objects.create(nombre='Yape Test', requiere_referencia=True)
        self.url = f'/api/ventas/metodos-pago/{self.metodo.id}/qr/'

    def test_sube_el_qr_y_el_listado_lo_informa_con_ruta_relativa(self):
        resp = self.client.post(self.url, {'imagen': _imagen(), 'descripcion': 'Yape 987 654 321'}, format='multipart')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data['tiene_qr'])
        self.assertTrue(resp.data['qr_url'].startswith('/media/pagos_qr/'))
        self.assertEqual(resp.data['qr_descripcion'], 'Yape 987 654 321')
        self.assertNotIn('qr_imagen', resp.data)

        listado = self.client.get('/api/ventas/metodos-pago/').data
        filas = listado['results'] if isinstance(listado, dict) else listado
        fila = next(f for f in filas if f['id'] == self.metodo.id)
        self.assertTrue(fila['tiene_qr'])

    def _existe(self, qr_url):
        """Comprueba en disco que el archivo del QR existe (la ruta pública ya se prueba en seguridad)."""
        return os.path.exists(os.path.join(self.carpeta.name, qr_url.replace('/media/', '', 1)))

    def test_el_qr_subido_queda_en_la_carpeta_publica_de_qr(self):
        ruta = self.client.post(self.url, {'imagen': _imagen()}, format='multipart').data['qr_url']
        self.assertTrue(ruta.startswith('/media/pagos_qr/'))
        self.assertTrue(self._existe(ruta))

    def test_reemplazar_borra_el_archivo_anterior(self):
        primero = self.client.post(self.url, {'imagen': _imagen()}, format='multipart').data['qr_url']
        segundo = self.client.post(self.url, {'imagen': _imagen(nombre='otro.png')}, format='multipart').data['qr_url']
        self.assertNotEqual(primero, segundo)
        self.assertFalse(self._existe(primero))
        self.assertTrue(self._existe(segundo))

    def test_quitar_el_qr(self):
        self.client.post(self.url, {'imagen': _imagen()}, format='multipart')
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['tiene_qr'])
        self.assertIsNone(resp.data['qr_url'])

    def test_rechaza_archivos_que_no_son_imagen_o_formato_no_permitido(self):
        falso = SimpleUploadedFile('qr.png', b'no soy una imagen', content_type='image/png')
        self.assertEqual(self.client.post(self.url, {'imagen': falso}, format='multipart').status_code, 400)
        gif = _imagen('GIF', nombre='qr.gif')
        self.assertEqual(self.client.post(self.url, {'imagen': gif}, format='multipart').status_code, 400)

    def test_exige_imagen_la_primera_vez(self):
        resp = self.client.post(self.url, {'descripcion': 'solo texto'}, format='multipart')
        self.assertEqual(resp.status_code, 400)

    def test_editar_el_metodo_con_json_sigue_funcionando(self):
        self.client.post(self.url, {'imagen': _imagen()}, format='multipart')
        resp = self.client.put(
            f'/api/ventas/metodos-pago/{self.metodo.id}/',
            {'nombre': 'Yape Test', 'requiere_referencia': True, 'estado': True}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.metodo.refresh_from_db()
        self.assertTrue(self.metodo.qr_imagen)  # el QR sobrevive a una edición normal

    def test_usuario_sin_permiso_no_puede_subir_qr(self):
        sin_permiso = Usuario.objects.create_user(
            username='sin_permiso_qr', email='sin_permiso_qr@example.com',
            nombres='Sin', apellidos='Permiso', password='x',
        )
        api = APIClient()
        api.force_authenticate(user=sin_permiso)
        self.assertEqual(api.post(self.url, {'imagen': _imagen()}, format='multipart').status_code, 403)
