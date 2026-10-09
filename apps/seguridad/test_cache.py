from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.seguridad.models import Empresa, Modulo, Permiso, Rol, RolPermiso, Usuario, UsuarioRol

MENU = '/api/seguridad/modulos/'
MIS_PERMISOS = '/api/seguridad/mis-permisos/'
EMPRESA = '/api/seguridad/empresa/'


class CacheSeguridadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = Usuario.objects.create_superuser(
            username='admin_cache_test', email='admin_cache_test@example.com',
            nombres='Admin', apellidos='CacheTest', password='x',
        )
        self.api_admin = APIClient()
        self.api_admin.force_authenticate(user=self.admin)

        # Usuario normal con un rol que da un permiso y un módulo visible
        self.modulo = Modulo.objects.create(codigo='MOD_CACHE', nombre='Mod Cache', orden=1, ruta='/cache',
                                            permiso_ver='MOD_CACHE.VER_MENU')
        self.permiso = Permiso.objects.create(id_modulo=self.modulo, codigo='MOD_CACHE.VER_MENU', nombre='Ver', accion='VER_MENU')
        self.permiso_2 = Permiso.objects.create(id_modulo=self.modulo, codigo='MOD_CACHE.OTRO', nombre='Otro', accion='VER')
        self.rol = Rol.objects.create(codigo='ROL_CACHE', nombre='Rol Cache')
        RolPermiso.objects.create(id_rol=self.rol, id_permiso=self.permiso, alcance='GLOBAL')
        self.usuario = Usuario.objects.create_user(
            username='user_cache_test', email='user_cache_test@example.com', nombres='Usu', apellidos='Cache', password='x',
        )
        UsuarioRol.objects.create(id_usuario=self.usuario, id_rol=self.rol)
        self.api = APIClient()
        self.api.force_authenticate(user=self.usuario)

    def _consultas(self, api, url):
        with CaptureQueriesContext(connection) as q:
            resp = api.get(url)
        self.assertEqual(resp.status_code, 200)
        return len(q), resp.data

    # --- permisos y menú ---
    def test_la_segunda_peticion_de_permisos_y_menu_no_toca_la_base(self):
        primera, datos = self._consultas(self.api, MIS_PERMISOS)
        segunda, datos_2 = self._consultas(self.api, MIS_PERMISOS)
        self.assertGreater(primera, 0)
        self.assertEqual(segunda, 0)
        self.assertEqual(datos, datos_2)

        _, menu = self._consultas(self.api, MENU)
        consultas_menu, menu_2 = self._consultas(self.api, MENU)
        self.assertEqual(consultas_menu, 0)
        self.assertEqual(menu, menu_2)
        self.assertIn('MOD_CACHE', [m['codigo'] for m in menu['data']])

    def test_cambiar_los_permisos_del_rol_por_la_api_se_ve_de_inmediato(self):
        self._consultas(self.api, MIS_PERMISOS)  # llena el caché
        self.assertIn('MOD_CACHE.VER_MENU', self.api.get(MIS_PERMISOS).data['data']['codigos'])

        resp = self.api_admin.post(
            f'/api/seguridad/roles/{self.rol.id_rol}/permisos/',
            {'permisos': [{'id_permiso': self.permiso_2.id_permiso, 'alcance': 'GLOBAL'}]}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        codigos = self.api.get(MIS_PERMISOS).data['data']['codigos']
        self.assertEqual(codigos, ['MOD_CACHE.OTRO'])
        self.assertNotIn('MOD_CACHE', [m['codigo'] for m in self.api.get(MENU).data['data']])

    def test_quitar_un_rol_al_usuario_invalida_el_caché(self):
        self.assertIn('MOD_CACHE.VER_MENU', self.api.get(MIS_PERMISOS).data['data']['codigos'])
        UsuarioRol.objects.filter(id_usuario=self.usuario).delete()
        self.assertEqual(self.api.get(MIS_PERMISOS).data['data']['codigos'], [])

    def test_cambiar_un_modulo_actualiza_el_menu(self):
        self.api.get(MENU)
        self.modulo.nombre = 'Nombre Nuevo'
        self.modulo.save()
        menu = self.api.get(MENU).data['data']
        self.assertEqual(next(m for m in menu if m['codigo'] == 'MOD_CACHE')['nombre'], 'Nombre Nuevo')

    def test_el_inicio_de_sesion_no_borra_el_caché(self):
        self.api.get(MIS_PERMISOS)
        self.usuario.ultimo_acceso = timezone.now()
        self.usuario.save(update_fields=['ultimo_acceso'])  # lo que hace cada login
        self.assertEqual(self._consultas(self.api, MIS_PERMISOS)[0], 0)

    def test_un_cambio_de_usuario_si_invalida_el_caché(self):
        self.api.get(MIS_PERMISOS)
        self.usuario.is_superuser = True
        self.usuario.save()
        self.assertGreater(self._consultas(self.api, MIS_PERMISOS)[0], 0)

    def test_cada_usuario_tiene_su_propio_caché(self):
        otro = Usuario.objects.create_user(
            username='otro_cache_test', email='otro_cache_test@example.com', nombres='Otro', apellidos='Cache', password='x',
        )
        api_otro = APIClient()
        api_otro.force_authenticate(user=otro)
        self.api.get(MIS_PERMISOS)
        self.assertEqual(api_otro.get(MIS_PERMISOS).data['data']['codigos'], [])
        self.assertIn('MOD_CACHE.VER_MENU', self.api.get(MIS_PERMISOS).data['data']['codigos'])

    # --- empresa ---
    def test_empresa_se_cachea_y_se_actualiza_al_editarla(self):
        Empresa.objects.update_or_create(id=1, defaults={'razon_social': 'Taller Uno', 'ruc': '20111111111', 'direccion': 'Av 1'})
        cache.clear()
        self.assertEqual(self.api_admin.get(EMPRESA).data['data']['razon_social'], 'Taller Uno')
        self.assertEqual(self._consultas(self.api_admin, EMPRESA)[0], 0)

        resp = self.api_admin.put(EMPRESA, {'razon_social': 'Taller Dos'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.api_admin.get(EMPRESA).data['data']['razon_social'], 'Taller Dos')

    def test_la_empresa_publica_y_la_completa_no_se_mezclan(self):
        Empresa.objects.update_or_create(
            id=1, defaults={'razon_social': 'Taller', 'ruc': '20111111111', 'direccion': 'Av 1', 'sunat_usuario_secundario': 'SECRETO'},
        )
        cache.clear()
        completa = self.api_admin.get(EMPRESA).data['data']
        publica = APIClient().get(EMPRESA).data['data']  # kiosko, sin sesión
        self.assertNotEqual(set(completa), set(publica))
        self.assertNotIn('SECRETO', str(publica))
        # y el orden de las peticiones no cambia lo que ve cada uno
        self.assertNotIn('SECRETO', str(APIClient().get(EMPRESA).data['data']))
        self.assertEqual(self.api_admin.get(EMPRESA).data['data'], completa)
