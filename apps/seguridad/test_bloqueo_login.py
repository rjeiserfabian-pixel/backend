from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.seguridad.models import Usuario

URL = '/api/seguridad/login/'
CLAVE = 'ClaveCorrecta123'


class BloqueoPorIntentosFallidosTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            username='cajero_bloqueo', email='cajero_bloqueo@example.com',
            nombres='Caje', apellidos='Ro', password=CLAVE,
        )
        self.api = APIClient()

    def _login(self, clave):
        return self.api.post(URL, {'username': 'cajero_bloqueo', 'password': clave}, format='json')

    def _fallar(self, veces):
        for _ in range(veces):
            self.assertEqual(self._login('incorrecta').status_code, 400)

    def test_cuatro_errores_no_bloquean_y_entrar_bien_reinicia_el_contador(self):
        self._fallar(4)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.intentos_fallidos, 4)
        self.assertIsNone(self.usuario.bloqueado_hasta)

        self.assertEqual(self._login(CLAVE).status_code, 200)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.intentos_fallidos, 0)
        # tras entrar bien, vuelve a tener sus 5 oportunidades completas
        self._fallar(4)
        self.assertEqual(self._login(CLAVE).status_code, 200)

    def test_al_quinto_error_la_cuenta_se_bloquea_aunque_despues_se_use_la_clave_correcta(self):
        self._fallar(5)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.esta_bloqueado())

        resp = self._login(CLAVE)  # clave correcta, pero la cuenta está bloqueada
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Cuenta bloqueada hasta', str(resp.data))

    def test_el_aviso_de_bloqueo_sale_tambien_con_clave_incorrecta_y_no_extiende_el_bloqueo(self):
        self._fallar(5)
        self.usuario.refresh_from_db()
        hasta = self.usuario.bloqueado_hasta

        resp = self._login('otra-incorrecta')
        self.assertIn('Cuenta bloqueada hasta', str(resp.data))
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.bloqueado_hasta, hasta)

    def test_el_bloqueo_se_levanta_solo_al_vencer(self):
        self._fallar(5)
        Usuario.objects.filter(pk=self.usuario.pk).update(bloqueado_hasta=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self._login(CLAVE).status_code, 200)

    def test_el_bloqueo_dura_unos_quince_minutos(self):
        self._fallar(5)
        self.usuario.refresh_from_db()
        minutos = (self.usuario.bloqueado_hasta - timezone.now()).total_seconds() / 60
        self.assertTrue(14 < minutos <= 15, minutos)

    def test_un_usuario_inexistente_no_rompe_ni_crea_nada(self):
        resp = self.api.post(URL, {'username': 'no_existe', 'password': 'x'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Usuario.objects.filter(username='no_existe').count(), 0)

    def test_equivocarse_con_un_usuario_no_afecta_a_otro(self):
        otro = Usuario.objects.create_user(
            username='otro_bloqueo', email='otro_bloqueo@example.com', nombres='Otro', apellidos='Uno', password=CLAVE,
        )
        self._fallar(5)
        resp = self.api.post(URL, {'username': 'otro_bloqueo', 'password': CLAVE}, format='json')
        self.assertEqual(resp.status_code, 200)
        otro.refresh_from_db()
        self.assertEqual(otro.intentos_fallidos, 0)
