from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.seguridad.models import (
    Modulo, Permiso, Rol, RolPermiso, Usuario, UsuarioRol, UsuarioSucursal,
)

from .models import AsignacionHerramienta, Herramienta
from .tests import HerramientasBaseTestCase

ROL_TECNICO = 'TÉCNICO_AUTOMOTRIZ'
URL_ASIG = '/api/herramientas/asignaciones/'


class AsignacionesBaseTestCase(HerramientasBaseTestCase):
    def setUp(self):
        super().setUp()
        self.rol_tecnico = Rol.objects.create(codigo=ROL_TECNICO, nombre='Técnico Automotriz')
        self.tecnico = self._crear_usuario('tecnico1', con_rol=True)
        self.herramienta = Herramienta.objects.create(
            nombre='Amoladora', categoria=self.categoria, sucursal=self.sucursal,
            creado_por=self.admin, estado_fisico='BUENO',
        )

    def _crear_usuario(self, username, con_rol=False, estado='activo'):
        u = Usuario.objects.create_user(
            username=username, email=f'{username}@example.com',
            nombres=username.capitalize(), apellidos='Prueba', password='x', estado=estado,
        )
        if con_rol:
            UsuarioRol.objects.create(id_usuario=u, id_rol=self.rol_tecnico)
        return u

    def entregar(self, **extra):
        data = {'herramienta': self.herramienta.id, 'tecnico': str(self.tecnico.pk)}
        data.update(extra)
        return self.client.post(URL_ASIG, data, format='json')


class EntregaTests(AsignacionesBaseTestCase):
    def test_entrega_correcta(self):
        resp = self.entregar(observaciones='Con disco nuevo')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['estado'], 'ACTIVA')
        self.assertEqual(resp.data['estado_fisico_entrega'], 'BUENO')
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'ASIGNADA')
        self.assertTrue(self.herramienta.historial.filter(accion='ENTREGA').exists())

    def test_no_se_entrega_dos_veces(self):
        self.assertEqual(self.entregar().status_code, 201)
        resp = self.entregar()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(AsignacionHerramienta.objects.count(), 1)

    def test_no_se_entrega_herramienta_no_disponible(self):
        for estado in ('EN_MANTENIMIENTO', 'EN_REPARACION', 'FUERA_DE_SERVICIO', 'PERDIDA', 'DADA_DE_BAJA'):
            Herramienta.objects.filter(pk=self.herramienta.pk).update(estado_operativo=estado)
            self.assertEqual(self.entregar().status_code, 409, estado)
        self.assertEqual(AsignacionHerramienta.objects.count(), 0)

    def test_herramienta_eliminada_o_inexistente(self):
        Herramienta.objects.filter(pk=self.herramienta.pk).update(estado=False)
        self.assertEqual(self.entregar().status_code, 400)
        self.assertEqual(self.entregar(herramienta=99999).status_code, 400)

    def test_solo_a_usuarios_con_rol_tecnico_activos(self):
        sin_rol = self._crear_usuario('sinrol')
        self.assertEqual(self.entregar(tecnico=str(sin_rol.pk)).status_code, 400)
        inactivo = self._crear_usuario('inactivo', con_rol=True, estado='inactivo')
        self.assertEqual(self.entregar(tecnico=str(inactivo.pk)).status_code, 400)
        eliminado = self._crear_usuario('eliminado', con_rol=True)
        Usuario.objects.filter(pk=eliminado.pk).update(fecha_eliminacion=timezone.now())
        self.assertEqual(self.entregar(tecnico=str(eliminado.pk)).status_code, 400)
        UsuarioRol.objects.filter(id_usuario=self.tecnico).update(
            fecha_expiracion=timezone.now() - timedelta(days=1)
        )
        self.assertEqual(self.entregar().status_code, 400)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'DISPONIBLE')

    def test_tecnico_con_id_invalido(self):
        self.assertEqual(self.entregar(tecnico='no-es-uuid').status_code, 400)

    def test_fecha_devolucion_no_puede_ser_pasada(self):
        ayer = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.assertEqual(self.entregar(fecha_devolucion_esperada=ayer).status_code, 400)
        manana = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self.entregar(fecha_devolucion_esperada=manana).status_code, 201)

    def test_advertencia_si_el_tecnico_es_de_otra_sucursal(self):
        UsuarioSucursal.objects.create(id_usuario=self.tecnico, sucursal=self.otra_sucursal)
        resp = self.entregar()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.data['advertencias']), 1)

    def test_restriccion_unica_en_base_de_datos(self):
        self.entregar()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AsignacionHerramienta.objects.create(
                herramienta=self.herramienta, tecnico=self.tecnico, entregado_por=self.admin,
                estado_fisico_entrega='BUENO',
            )

    def test_herramienta_asignada_no_cambia_de_estado_ni_se_elimina(self):
        self.entregar()
        resp = self.client.post(
            f'/api/herramientas/herramientas/{self.herramienta.pk}/cambiar-estado/',
            {'estado_operativo': 'DISPONIBLE', 'motivo': 'x'}, format='json',
        )
        self.assertEqual(resp.status_code, 409)
        resp = self.client.delete(f'/api/herramientas/herramientas/{self.herramienta.pk}/')
        self.assertEqual(resp.status_code, 400)


class DevolucionTests(AsignacionesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.asig = self.entregar().data
        self.url = f"{URL_ASIG}{self.asig['id']}/devolver/"

    def test_devolucion_normal_suma_horas_y_deja_disponible(self):
        resp = self.client.post(
            self.url, {'estado_fisico_devolucion': 'BUENO', 'horas_uso_periodo': '12.5'}, format='json'
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['estado'], 'DEVUELTA')
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'DISPONIBLE')
        self.assertEqual(self.herramienta.horas_uso_acumuladas, Decimal('12.50'))
        self.assertTrue(self.herramienta.historial.filter(accion='DEVOLUCION').exists())
        # una segunda asignación acumula horas sobre la anterior
        self.assertEqual(self.entregar().status_code, 201)
        nueva = AsignacionHerramienta.objects.get(estado='ACTIVA')
        self.client.post(
            f'{URL_ASIG}{nueva.pk}/devolver/',
            {'estado_fisico_devolucion': 'BUENO', 'horas_uso_periodo': '7.5'}, format='json',
        )
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.horas_uso_acumuladas, Decimal('20.00'))

    def test_devolucion_en_mal_estado_pasa_a_mantenimiento(self):
        resp = self.client.post(self.url, {'estado_fisico_devolucion': 'MALO'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'EN_MANTENIMIENTO')
        self.assertEqual(self.herramienta.estado_fisico, 'MALO')

    def test_devolucion_marcada_como_requiere_mantenimiento(self):
        self.client.post(
            self.url, {'estado_fisico_devolucion': 'REGULAR', 'requiere_mantenimiento': True}, format='json'
        )
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'EN_MANTENIMIENTO')

    def test_no_se_devuelve_dos_veces(self):
        body = {'estado_fisico_devolucion': 'BUENO', 'horas_uso_periodo': '5'}
        self.assertEqual(self.client.post(self.url, body, format='json').status_code, 200)
        self.assertEqual(self.client.post(self.url, body, format='json').status_code, 409)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.horas_uso_acumuladas, Decimal('5.00'))

    def test_validaciones_de_horas_y_estado(self):
        for body in (
            {'estado_fisico_devolucion': 'BUENO', 'horas_uso_periodo': '-1'},
            {'estado_fisico_devolucion': 'BUENO', 'horas_uso_periodo': '999999'},
            {'estado_fisico_devolucion': 'XXX'},
            {},
        ):
            self.assertEqual(self.client.post(self.url, body, format='json').status_code, 400, body)
        self.assertEqual(AsignacionHerramienta.objects.get(pk=self.asig['id']).estado, 'ACTIVA')

    def test_devolver_inexistente_404(self):
        resp = self.client.post(
            f'{URL_ASIG}99999/devolver/', {'estado_fisico_devolucion': 'BUENO'}, format='json'
        )
        self.assertEqual(resp.status_code, 404)


class AnulacionTests(AsignacionesBaseTestCase):
    def setUp(self):
        super().setUp()
        self.asig = self.entregar().data
        self.url = f"{URL_ASIG}{self.asig['id']}/anular/"

    def test_anular_libera_la_herramienta(self):
        resp = self.client.post(self.url, {'motivo': 'Error de registro'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['estado'], 'ANULADA')
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'DISPONIBLE')
        self.assertTrue(self.herramienta.historial.filter(accion='ANULACION_ASIGNACION').exists())
        self.assertTrue(AsignacionHerramienta.objects.filter(pk=self.asig['id']).exists())
        self.assertEqual(self.entregar().status_code, 201)

    def test_motivo_obligatorio(self):
        self.assertEqual(self.client.post(self.url, {}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, {'motivo': '   '}, format='json').status_code, 400)

    def test_no_se_anula_una_devuelta(self):
        self.client.post(
            f"{URL_ASIG}{self.asig['id']}/devolver/", {'estado_fisico_devolucion': 'BUENO'}, format='json'
        )
        self.assertEqual(self.client.post(self.url, {'motivo': 'x'}, format='json').status_code, 409)

    def test_no_hay_delete_ni_patch(self):
        url = f"{URL_ASIG}{self.asig['id']}/"
        self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertEqual(self.client.patch(url, {'estado': 'DEVUELTA'}, format='json').status_code, 405)


class ListadoYSelectoresTests(AsignacionesBaseTestCase):
    def test_listado_filtros_y_vencidas(self):
        self.entregar()
        otra = Herramienta.objects.create(
            nombre='Sierra', categoria=self.categoria, sucursal=self.sucursal, creado_por=self.admin
        )
        r = self.client.post(URL_ASIG, {'herramienta': otra.id, 'tecnico': str(self.tecnico.pk)}, format='json')
        AsignacionHerramienta.objects.filter(pk=r.data['id']).update(
            fecha_devolucion_esperada=timezone.localdate() - timedelta(days=3)
        )
        self.assertEqual(self.client.get(URL_ASIG).data['count'], 2)
        resp = self.client.get(URL_ASIG, {'vencidas': '1'})
        self.assertEqual(resp.data['count'], 1)
        self.assertTrue(resp.data['results'][0]['vencida'])
        self.assertEqual(self.client.get(URL_ASIG, {'search': 'Sierra'}).data['count'], 1)
        self.assertEqual(self.client.get(URL_ASIG, {'estado': 'DEVUELTA'}).data['count'], 0)

    def test_selector_de_tecnicos_solo_trae_tecnicos_validos(self):
        self._crear_usuario('sinrol')
        resp = self.client.get(f'{URL_ASIG}tecnicos/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([t['id'] for t in resp.data], [str(self.tecnico.pk)])

    def test_selector_de_herramientas_disponibles(self):
        self.entregar()
        Herramienta.objects.create(
            nombre='Soldadora', categoria=self.categoria, sucursal=self.sucursal, creado_por=self.admin
        )
        resp = self.client.get(f'{URL_ASIG}herramientas-disponibles/')
        self.assertEqual([h['nombre'] for h in resp.data], ['Soldadora'])
        resp = self.client.get(f'{URL_ASIG}herramientas-disponibles/', {'search': 'zzz'})
        self.assertEqual(resp.data, [])


class PermisosAsignacionesTests(AsignacionesBaseTestCase):
    def _dar_permisos(self, usuario, codigos, alcance='GLOBAL'):
        modulo, _ = Modulo.objects.get_or_create(codigo='HERR_ASIG_TEST', defaults={'nombre': 'Test'})
        rol = Rol.objects.create(codigo=f'ROL_{usuario.username}', nombre=usuario.username)
        for codigo in codigos:
            permiso, _ = Permiso.objects.get_or_create(
                codigo=codigo, defaults={'id_modulo': modulo, 'nombre': codigo, 'accion': 'VER'}
            )
            RolPermiso.objects.create(id_rol=rol, id_permiso=permiso, alcance=alcance)
        UsuarioRol.objects.create(id_usuario=usuario, id_rol=rol)

    def test_sin_permisos_recibe_403(self):
        nadie = self._crear_usuario('nadie')
        c = APIClient()
        c.force_authenticate(user=nadie)
        self.assertEqual(c.get(URL_ASIG).status_code, 403)
        cuerpo = {'herramienta': self.herramienta.id, 'tecnico': str(self.tecnico.pk)}
        self.assertEqual(c.post(URL_ASIG, cuerpo, format='json').status_code, 403)
        self.assertEqual(c.get(f'{URL_ASIG}tecnicos/').status_code, 403)
        self.assertEqual(c.get(f'{URL_ASIG}herramientas-disponibles/').status_code, 403)
        asig = self.entregar().data
        devolver = c.post(f"{URL_ASIG}{asig['id']}/devolver/", {'estado_fisico_devolucion': 'BUENO'}, format='json')
        self.assertEqual(devolver.status_code, 403)
        self.assertEqual(c.post(f"{URL_ASIG}{asig['id']}/anular/", {'motivo': 'x'}, format='json').status_code, 403)

    def test_cada_accion_exige_su_permiso(self):
        asig = self.entregar().data
        solo_devolver = self._crear_usuario('devolvedor')
        self._dar_permisos(solo_devolver, ['HERRAMIENTAS.ASIGNACIONES.EDITAR'])
        c = APIClient()
        c.force_authenticate(user=solo_devolver)
        self.assertEqual(c.post(f"{URL_ASIG}{asig['id']}/anular/", {'motivo': 'x'}, format='json').status_code, 403)
        devolver = c.post(f"{URL_ASIG}{asig['id']}/devolver/", {'estado_fisico_devolucion': 'BUENO'}, format='json')
        self.assertEqual(devolver.status_code, 200)

    def test_tecnico_con_alcance_propio_solo_ve_lo_suyo(self):
        self.entregar()
        otro = self._crear_usuario('tecnico2', con_rol=True)
        otra = Herramienta.objects.create(
            nombre='Taladro', categoria=self.categoria, sucursal=self.sucursal, creado_por=self.admin
        )
        self.client.post(URL_ASIG, {'herramienta': otra.id, 'tecnico': str(otro.pk)}, format='json')

        self._dar_permisos(self.tecnico, ['HERRAMIENTAS.ASIGNACIONES.VER'], alcance='PROPIO')
        c = APIClient()
        c.force_authenticate(user=self.tecnico)
        resp = c.get(URL_ASIG)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['herramienta'], self.herramienta.id)
        ajena = AsignacionHerramienta.objects.get(tecnico=otro)
        self.assertEqual(c.get(f'{URL_ASIG}{ajena.pk}/').status_code, 404)
