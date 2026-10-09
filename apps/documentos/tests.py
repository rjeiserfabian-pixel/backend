import shutil
import tempfile
from datetime import timedelta

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.documentos.models import Documento
from apps.seguridad.models import Usuario
from apps.vehiculos.models import Vehiculo

URL = '/api/documentos/'
PDF_VALIDO = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF'


class DocumentoTests(TestCase):
    def setUp(self):
        # Los archivos de prueba se guardan en una carpeta temporal, no en la real.
        self.tmp = tempfile.mkdtemp()
        campo = Documento._meta.get_field('archivo')
        self._storage_original = campo.storage
        campo.storage = FileSystemStorage(location=self.tmp)

        self.admin = Usuario.objects.create_superuser(
            username='admin_docs_test', email='admin_docs_test@example.com',
            nombres='Admin', apellidos='DocsTest', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.vehiculo = Vehiculo.objects.create(placa='DOC-001', marca='Toyota', modelo='Yaris')
        self.cliente = Cliente.objects.create(dni='70000099', nombres='Cliente', apellidos='Docs')

    def tearDown(self):
        Documento._meta.get_field('archivo').storage = self._storage_original
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _crear(self, **extra):
        datos = {'vehiculo': self.vehiculo.id, 'tipo': 'SOAT', 'numero': 'S-1'}
        datos.update(extra)
        return self.client.post(URL, datos, format='multipart')

    def _en_dias(self, dias):
        return (timezone.localdate() + timedelta(days=dias)).isoformat()

    # --- vigencia ---
    def test_estado_de_vigencia_segun_fecha(self):
        casos = {-1: 'VENCIDO', 10: 'POR_VENCER', 31: 'VIGENTE'}
        for dias, esperado in casos.items():
            resp = self._crear(fecha_vencimiento=self._en_dias(dias))
            self.assertEqual(resp.status_code, 201, resp.data)
            self.assertEqual(resp.data['estado_vigencia'], esperado, dias)
        sin_fecha = self._crear(tipo='TARJETA_PROPIEDAD')
        self.assertEqual(sin_fecha.data['estado_vigencia'], 'SIN_VENCIMIENTO')

    def test_filtro_de_alerta_trae_vencidos_y_por_vencer(self):
        self._crear(fecha_vencimiento=self._en_dias(-5))
        self._crear(fecha_vencimiento=self._en_dias(10))
        self._crear(fecha_vencimiento=self._en_dias(200))

        resp = self.client.get(URL, {'vigencia': 'alerta'})
        self.assertEqual(resp.data['count'], 2)
        resumen = self.client.get(URL + 'resumen/').data
        self.assertEqual((resumen['vencidos'], resumen['por_vencer']), (1, 1))

    # --- reglas de negocio ---
    def test_tipo_debe_corresponder_al_propietario(self):
        self.assertEqual(self._crear(tipo='LICENCIA_CONDUCIR').status_code, 400)
        resp = self.client.post(URL, {'cliente': self.cliente.id, 'tipo': 'LICENCIA_CONDUCIR'}, format='multipart')
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_debe_tener_exactamente_un_propietario(self):
        ambos = self.client.post(URL, {'vehiculo': self.vehiculo.id, 'cliente': self.cliente.id, 'tipo': 'OTRO'}, format='multipart')
        ninguno = self.client.post(URL, {'tipo': 'OTRO'}, format='multipart')
        self.assertEqual(ambos.status_code, 400)
        self.assertEqual(ninguno.status_code, 400)

    def test_vencimiento_no_puede_ser_anterior_a_la_emision(self):
        resp = self._crear(fecha_emision=self._en_dias(0), fecha_vencimiento=self._en_dias(-3))
        self.assertEqual(resp.status_code, 400)

    # --- archivos ---
    def test_rechaza_extension_no_permitida_y_contenido_falso(self):
        malo = SimpleUploadedFile('virus.exe', b'MZ....', content_type='application/octet-stream')
        self.assertEqual(self._crear(archivo=malo).status_code, 400)
        falso = SimpleUploadedFile('falso.pdf', b'esto no es un pdf', content_type='application/pdf')
        self.assertEqual(self._crear(archivo=falso).status_code, 400)

    def test_rechaza_archivo_de_mas_de_5mb(self):
        grande = SimpleUploadedFile('grande.pdf', PDF_VALIDO + b'0' * (5 * 1024 * 1024), content_type='application/pdf')
        self.assertEqual(self._crear(archivo=grande).status_code, 400)

    def test_sube_y_descarga_un_pdf_valido(self):
        archivo = SimpleUploadedFile('soat.pdf', PDF_VALIDO, content_type='application/pdf')
        creado = self._crear(archivo=archivo)
        self.assertEqual(creado.status_code, 201, creado.data)
        self.assertTrue(creado.data['tiene_archivo'])
        self.assertNotIn('archivo', creado.data)  # nunca se expone la ruta del archivo

        descarga = self.client.get(f"{URL}{creado.data['id']}/archivo/")
        self.assertEqual(descarga.status_code, 200)
        self.assertEqual(b''.join(descarga.streaming_content), PDF_VALIDO)
        self.assertEqual(descarga['X-Content-Type-Options'], 'nosniff')

    def test_documento_sin_archivo_responde_404_al_descargar(self):
        creado = self._crear()
        self.assertEqual(self.client.get(f"{URL}{creado.data['id']}/archivo/").status_code, 404)

    # --- permisos y baja ---
    def test_usuario_sin_permisos_no_ve_ni_crea(self):
        existente = self._crear().data['id']
        sin_permisos = Usuario.objects.create_user(
            username='sin_permisos_docs', email='sin_permisos_docs@example.com',
            nombres='Sin', apellidos='Permisos', password='x',
        )
        cliente_api = APIClient()
        cliente_api.force_authenticate(user=sin_permisos)

        self.assertEqual(cliente_api.get(URL).data['count'], 0)
        self.assertEqual(cliente_api.get(f'{URL}{existente}/archivo/').status_code, 404)
        creacion = cliente_api.post(URL, {'vehiculo': self.vehiculo.id, 'tipo': 'SOAT'}, format='multipart')
        self.assertEqual(creacion.status_code, 403)

    def test_eliminar_es_baja_logica(self):
        creado = self._crear().data['id']
        self.assertEqual(self.client.delete(f'{URL}{creado}/').status_code, 204)
        self.assertEqual(self.client.get(URL, {'vehiculo': self.vehiculo.id}).data['count'], 0)
        self.assertTrue(Documento.objects.filter(pk=creado, activo=False).exists())
