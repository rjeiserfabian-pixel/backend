from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.seguridad.models import Modulo, Permiso, Rol, RolPermiso, UsuarioRol

from .models import (
    AsignacionHerramienta,
    Herramienta,
    IncidenciaHerramienta,
    PlanMantenimientoHerramienta,
    RegistroMantenimiento,
)
from .test_asignaciones import AsignacionesBaseTestCase

PLANES = '/api/herramientas/planes-mantenimiento/'
MANTS = '/api/herramientas/mantenimientos/'
INCS = '/api/herramientas/incidencias/'
ASIG = '/api/herramientas/asignaciones/'
ALERTAS = '/api/herramientas/alertas/'


class FaseTresBase(AsignacionesBaseTestCase):
    def hoy(self):
        return timezone.localdate()

    def crear_plan(self, **extra):
        data = {'herramienta': self.herramienta.id, 'nombre': 'Cambio de carbones', 'intervalo_dias': 30}
        data.update(extra)
        return self.client.post(PLANES, data, format='json')

    def plan_vencido(self, **extra):
        resp = self.crear_plan(**extra)
        self.assertEqual(resp.status_code, 201, resp.data)
        PlanMantenimientoHerramienta.objects.get(pk=resp.data['id'])
        plan = PlanMantenimientoHerramienta.objects.get(pk=resp.data['id'])
        plan.ultima_fecha = self.hoy() - timedelta(days=60)
        plan.save()
        return plan

    def cliente_con_permisos(self, username, codigos, alcance='GLOBAL'):
        usuario = self._crear_usuario(username)
        modulo, _ = Modulo.objects.get_or_create(codigo='HERR_F3_TEST', defaults={'nombre': 'Test'})
        rol = Rol.objects.create(codigo=f'ROL_{username}', nombre=username)
        for codigo in codigos:
            permiso, _ = Permiso.objects.get_or_create(
                codigo=codigo, defaults={'id_modulo': modulo, 'nombre': codigo, 'accion': 'VER'}
            )
            RolPermiso.objects.create(id_rol=rol, id_permiso=permiso, alcance=alcance)
        UsuarioRol.objects.create(id_usuario=usuario, id_rol=rol)
        c = APIClient()
        c.force_authenticate(user=usuario)
        return usuario, c


class PlanTests(FaseTresBase):
    def test_crear_plan_por_dias_calcula_proxima_fecha(self):
        resp = self.crear_plan(intervalo_dias=30)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['proxima_fecha'], (self.hoy() + timedelta(days=30)).isoformat())
        self.assertEqual(resp.data['vencimiento'], 'AL_DIA')
        self.assertIsNone(resp.data['proximas_horas'])

    def test_plan_por_horas_toma_como_base_las_horas_actuales(self):
        Herramienta.objects.filter(pk=self.herramienta.pk).update(horas_uso_acumuladas=Decimal('40'))
        resp = self.crear_plan(intervalo_dias=None, intervalo_horas='100')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Decimal(resp.data['proximas_horas']), Decimal('140'))
        self.assertEqual(Decimal(resp.data['horas_restantes']), Decimal('100'))

    def test_requiere_algun_intervalo_y_valores_positivos(self):
        self.assertEqual(self.crear_plan(intervalo_dias=None).status_code, 400)
        self.assertEqual(self.crear_plan(intervalo_dias=0).status_code, 400)
        self.assertEqual(self.crear_plan(intervalo_dias=None, intervalo_horas='-5').status_code, 400)
        self.assertEqual(self.crear_plan(intervalo_dias=-3).status_code, 400)
        self.assertEqual(self.crear_plan(nombre='   ').status_code, 400)
        self.assertEqual(PlanMantenimientoHerramienta.objects.count(), 0)

    def test_ultima_fecha_futura_rechazada(self):
        manana = (self.hoy() + timedelta(days=1)).isoformat()
        self.assertEqual(self.crear_plan(ultima_fecha=manana).status_code, 400)

    def test_restricciones_en_base_de_datos(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlanMantenimientoHerramienta.objects.create(herramienta=self.herramienta, nombre='x')

    def test_vencimiento_por_fecha_por_horas_y_por_vencer(self):
        vencido = self.plan_vencido()
        self.assertEqual(vencido.estado_vencimiento(), 'VENCIDO')
        resp = self.crear_plan(nombre='Por vencer', intervalo_dias=5)
        self.assertEqual(resp.data['vencimiento'], 'POR_VENCER')
        resp = self.crear_plan(nombre='Horas', intervalo_dias=None, intervalo_horas='50')
        plan_horas = PlanMantenimientoHerramienta.objects.get(pk=resp.data['id'])
        self.assertEqual(plan_horas.estado_vencimiento(), 'AL_DIA')
        Herramienta.objects.filter(pk=self.herramienta.pk).update(horas_uso_acumuladas=Decimal('46'))
        plan_horas.refresh_from_db()
        self.assertEqual(plan_horas.estado_vencimiento(), 'POR_VENCER')
        Herramienta.objects.filter(pk=self.herramienta.pk).update(horas_uso_acumuladas=Decimal('50'))
        plan_horas.refresh_from_db()
        self.assertEqual(plan_horas.estado_vencimiento(), 'VENCIDO')

    def test_filtros_de_vencimiento_coinciden_con_la_regla_en_python(self):
        self.plan_vencido(nombre='A')
        self.crear_plan(nombre='B', intervalo_dias=5)
        self.crear_plan(nombre='C', intervalo_dias=90)
        resp = self.client.get(PLANES, {'vencimiento': 'VENCIDO'})
        self.assertEqual([p['nombre'] for p in resp.data['results']], ['A'])
        resp = self.client.get(PLANES, {'vencimiento': 'POR_VENCER'})
        self.assertEqual([p['nombre'] for p in resp.data['results']], ['B'])
        self.assertEqual(self.client.get(PLANES).data['count'], 3)

    def test_filtro_por_horas_vencidas(self):
        self.crear_plan(nombre='H', intervalo_dias=None, intervalo_horas='10')
        Herramienta.objects.filter(pk=self.herramienta.pk).update(horas_uso_acumuladas=Decimal('12'))
        resp = self.client.get(PLANES, {'vencimiento': 'VENCIDO'})
        self.assertEqual([p['nombre'] for p in resp.data['results']], ['H'])

    def test_eliminar_desactiva_y_no_borra(self):
        pid = self.crear_plan().data['id']
        self.assertEqual(self.client.delete(f'{PLANES}{pid}/').status_code, 204)
        self.assertFalse(PlanMantenimientoHerramienta.objects.get(pk=pid).activo)
        self.assertEqual(self.client.get(PLANES).data['count'], 0)
        self.assertEqual(self.client.get(PLANES, {'activo': 'todos'}).data['count'], 1)

    def test_editar_intervalo_recalcula_y_no_cambia_herramienta(self):
        pid = self.crear_plan(intervalo_dias=30).data['id']
        resp = self.client.patch(f'{PLANES}{pid}/', {'intervalo_dias': 10}, format='json')
        self.assertEqual(resp.data['proxima_fecha'], (self.hoy() + timedelta(days=10)).isoformat())
        otra = Herramienta.objects.create(
            nombre='Otra', categoria=self.categoria, sucursal=self.sucursal, creado_por=self.admin
        )
        resp = self.client.patch(f'{PLANES}{pid}/', {'herramienta': otra.id}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_no_se_crea_plan_para_herramienta_perdida(self):
        Herramienta.objects.filter(pk=self.herramienta.pk).update(estado_operativo='PERDIDA')
        self.assertEqual(self.crear_plan().status_code, 400)


class BloqueoDeEntregaTests(FaseTresBase):
    def test_mantenimiento_vencido_bloquea_la_entrega(self):
        self.plan_vencido()
        resp = self.entregar()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data['errores']['codigo'], 'MANTENIMIENTO_VENCIDO')
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'DISPONIBLE')

    def test_por_vencer_o_plan_inactivo_no_bloquean(self):
        self.crear_plan(nombre='Pronto', intervalo_dias=5)
        plan = self.plan_vencido(nombre='Inactivo')
        PlanMantenimientoHerramienta.objects.filter(pk=plan.pk).update(activo=False)
        self.assertEqual(self.entregar().status_code, 201)

    def test_excepcion_requiere_permiso_y_motivo(self):
        self.plan_vencido()
        # sin motivo
        self.assertEqual(self.entregar(forzar=True).status_code, 400)
        # usuario que puede entregar pero NO autorizar excepciones
        usuario, c = self.cliente_con_permisos('entregador', ['HERRAMIENTAS.ASIGNACIONES.CREAR'])
        cuerpo = {'herramienta': self.herramienta.id, 'tecnico': str(self.tecnico.pk),
                  'forzar': True, 'motivo_excepcion': 'Urgente'}
        self.assertEqual(c.post(ASIG, cuerpo, format='json').status_code, 403)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'DISPONIBLE')
        # con el permiso de excepción sí
        usuario2, c2 = self.cliente_con_permisos(
            'autorizador', ['HERRAMIENTAS.ASIGNACIONES.CREAR', 'HERRAMIENTAS.ASIGNACIONES.EXCEPCION']
        )
        resp = c2.post(ASIG, cuerpo, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(self.herramienta.historial.filter(detalle__contains='EXCEPCIÓN').exists())

    def test_superusuario_puede_forzar(self):
        self.plan_vencido()
        resp = self.entregar(forzar=True, motivo_excepcion='Trabajo urgente')
        self.assertEqual(resp.status_code, 201, resp.data)


class MantenimientoTests(FaseTresBase):
    def iniciar(self, **extra):
        data = {'herramienta': self.herramienta.id, 'tipo': 'PREVENTIVO', 'descripcion': 'Cambio de carbones'}
        data.update(extra)
        return self.client.post(MANTS, data, format='json')

    def test_iniciar_preventivo_y_correctivo_cambian_el_estado(self):
        self.assertEqual(self.iniciar().status_code, 201)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'EN_MANTENIMIENTO')
        self.assertTrue(self.herramienta.historial.filter(accion='MANTENIMIENTO_INICIO').exists())
        otra = Herramienta.objects.create(
            nombre='Sierra', categoria=self.categoria, sucursal=self.sucursal, creado_por=self.admin
        )
        resp = self.iniciar(herramienta=otra.id, tipo='CORRECTIVO')
        self.assertEqual(resp.status_code, 201)
        otra.refresh_from_db()
        self.assertEqual(otra.estado_operativo, 'EN_REPARACION')

    def test_no_se_inicia_si_esta_asignada_o_en_curso_o_terminal(self):
        self.entregar()
        self.assertEqual(self.iniciar().status_code, 409)
        Herramienta.objects.filter(pk=self.herramienta.pk).update(estado_operativo='DISPONIBLE')
        AsignacionHerramienta.objects.all().update(estado='DEVUELTA')
        self.assertEqual(self.iniciar().status_code, 201)
        self.assertEqual(self.iniciar().status_code, 409)
        self.assertEqual(RegistroMantenimiento.objects.filter(estado='EN_CURSO').count(), 1)
        RegistroMantenimiento.objects.all().update(estado='CANCELADO')
        Herramienta.objects.filter(pk=self.herramienta.pk).update(estado_operativo='DADA_DE_BAJA')
        self.assertEqual(self.iniciar().status_code, 409)

    def test_descripcion_obligatoria_y_plan_ajeno_rechazado(self):
        self.assertEqual(self.iniciar(descripcion='  ').status_code, 400)
        otra = Herramienta.objects.create(
            nombre='Sierra', categoria=self.categoria, sucursal=self.sucursal, creado_por=self.admin
        )
        plan_ajeno = PlanMantenimientoHerramienta.objects.create(herramienta=otra, nombre='x', intervalo_dias=5)
        self.assertEqual(self.iniciar(plan=plan_ajeno.pk).status_code, 400)
        self.assertEqual(self.iniciar(herramienta=99999).status_code, 400)
        self.assertEqual(RegistroMantenimiento.objects.count(), 0)

    def test_finalizar_reinicia_el_plan_y_libera_la_herramienta(self):
        plan = self.plan_vencido()
        Herramienta.objects.filter(pk=self.herramienta.pk).update(horas_uso_acumuladas=Decimal('25'))
        rid = self.iniciar(plan=plan.pk).data['id']
        resp = self.client.post(
            f'{MANTS}{rid}/finalizar/',
            {'resultado': 'Listo', 'estado_fisico_final': 'BUENO', 'costo': '85.50'}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['estado'], 'FINALIZADO')
        self.assertEqual(Decimal(resp.data['costo']), Decimal('85.50'))
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'DISPONIBLE')
        plan.refresh_from_db()
        self.assertEqual(plan.ultima_fecha, self.hoy())
        self.assertEqual(plan.ultimas_horas, Decimal('25.00'))
        self.assertEqual(plan.proxima_fecha, self.hoy() + timedelta(days=30))
        self.assertEqual(plan.estado_vencimiento(), 'AL_DIA')
        self.assertEqual(self.entregar().status_code, 201)

    def test_finalizar_fuera_de_servicio_y_no_se_finaliza_dos_veces(self):
        rid = self.iniciar().data['id']
        cuerpo = {'resultado': 'No tiene arreglo', 'estado_fisico_final': 'MALO', 'dejar_fuera_de_servicio': True}
        self.assertEqual(self.client.post(f'{MANTS}{rid}/finalizar/', cuerpo, format='json').status_code, 200)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'FUERA_DE_SERVICIO')
        self.assertEqual(self.client.post(f'{MANTS}{rid}/finalizar/', cuerpo, format='json').status_code, 409)

    def test_fuera_de_servicio_no_reinicia_el_plan(self):
        plan = self.plan_vencido()
        antes = plan.ultima_fecha
        rid = self.iniciar(plan=plan.pk).data['id']
        self.client.post(
            f'{MANTS}{rid}/finalizar/',
            {'resultado': 'Sin arreglo', 'estado_fisico_final': 'MALO', 'dejar_fuera_de_servicio': True},
            format='json',
        )
        plan.refresh_from_db()
        self.assertEqual(plan.ultima_fecha, antes)

    def test_finalizar_valida_datos(self):
        rid = self.iniciar().data['id']
        for cuerpo in (
            {}, {'resultado': 'ok'}, {'resultado': '  ', 'estado_fisico_final': 'BUENO'},
            {'resultado': 'ok', 'estado_fisico_final': 'BUENO', 'costo': '-1'},
            {'resultado': 'ok', 'estado_fisico_final': 'XX'},
        ):
            self.assertEqual(self.client.post(f'{MANTS}{rid}/finalizar/', cuerpo, format='json').status_code, 400, cuerpo)
        self.assertEqual(RegistroMantenimiento.objects.get(pk=rid).estado, 'EN_CURSO')

    def test_cancelar_requiere_motivo_y_devuelve_la_herramienta(self):
        rid = self.iniciar().data['id']
        self.assertEqual(self.client.post(f'{MANTS}{rid}/cancelar/', {'motivo': ' '}, format='json').status_code, 400)
        resp = self.client.post(f'{MANTS}{rid}/cancelar/', {'motivo': 'Error de registro'}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['estado'], 'CANCELADO')
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'DISPONIBLE')
        self.assertEqual(self.client.post(f'{MANTS}{rid}/cancelar/', {'motivo': 'x'}, format='json').status_code, 409)

    def test_con_mantenimiento_en_curso_no_se_cambia_estado_ni_se_elimina(self):
        self.iniciar()
        resp = self.client.post(
            f'/api/herramientas/herramientas/{self.herramienta.pk}/cambiar-estado/',
            {'estado_operativo': 'DISPONIBLE', 'motivo': 'x'}, format='json',
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self.client.delete(f'/api/herramientas/herramientas/{self.herramienta.pk}/').status_code, 400)

    def test_el_costo_solo_lo_ve_y_guarda_quien_tiene_el_permiso(self):
        rid = self.iniciar().data['id']
        usuario, c = self.cliente_con_permisos(
            'jefe_taller', ['HERRAMIENTAS.MANTENIMIENTOS.VER', 'HERRAMIENTAS.MANTENIMIENTOS.EDITAR']
        )
        resp = c.post(
            f'{MANTS}{rid}/finalizar/',
            {'resultado': 'ok', 'estado_fisico_final': 'BUENO', 'costo': '999'}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertNotIn('costo', resp.data)
        self.assertNotIn('costo', c.get(MANTS).data['results'][0])
        self.assertIsNone(RegistroMantenimiento.objects.get(pk=rid).costo)  # el valor enviado se ignoró
        self.assertIn('costo', self.client.get(MANTS).data['results'][0])  # administrador sí lo ve

    def test_listado_y_filtros(self):
        self.iniciar()
        self.assertEqual(self.client.get(MANTS).data['count'], 1)
        self.assertEqual(self.client.get(MANTS, {'estado': 'FINALIZADO'}).data['count'], 0)
        self.assertEqual(self.client.get(MANTS, {'search': 'carbones'}).data['count'], 1)


class IncidenciaTests(FaseTresBase):
    def reportar(self, cliente=None, **extra):
        data = {'herramienta': self.herramienta.id, 'tipo': 'DANO', 'descripcion': 'Se cayó del banco'}
        data.update(extra)
        return (cliente or self.client).post(INCS, data, format='json')

    def test_crear_registra_responsable_y_historial(self):
        self.entregar()
        resp = self.reportar()
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['estado'], 'ABIERTA')
        self.assertEqual(resp.data['responsable_nombre'], 'Tecnico1 Prueba')
        self.assertTrue(self.herramienta.historial.filter(accion='INCIDENCIA').exists())

    def test_validaciones(self):
        self.assertEqual(self.reportar(descripcion='  ').status_code, 400)
        self.assertEqual(self.reportar(tipo='XX').status_code, 400)
        self.assertEqual(self.reportar(herramienta=99999).status_code, 400)

    def test_tecnico_solo_reporta_de_sus_herramientas_y_solo_ve_las_suyas(self):
        self.entregar()
        otra = Herramienta.objects.create(
            nombre='Sierra', categoria=self.categoria, sucursal=self.sucursal, creado_por=self.admin
        )
        c = APIClient()
        permisos = ['HERRAMIENTAS.INCIDENCIAS.VER', 'HERRAMIENTAS.INCIDENCIAS.CREAR']
        modulo, _ = Modulo.objects.get_or_create(codigo='HERR_F3_TEST', defaults={'nombre': 'Test'})
        rol = Rol.objects.create(codigo='ROL_TEC_INC', nombre='x')
        for codigo in permisos:
            permiso, _ = Permiso.objects.get_or_create(
                codigo=codigo, defaults={'id_modulo': modulo, 'nombre': codigo, 'accion': 'VER'}
            )
            RolPermiso.objects.create(id_rol=rol, id_permiso=permiso, alcance='PROPIO')
        UsuarioRol.objects.create(id_usuario=self.tecnico, id_rol=rol)
        c.force_authenticate(user=self.tecnico)

        self.assertEqual(self.reportar(c).status_code, 201)
        self.assertEqual(self.reportar(c, herramienta=otra.id).status_code, 400)
        self.reportar(herramienta=otra.id)  # la reporta el administrador
        self.assertEqual(c.get(INCS).data['count'], 1)
        self.assertEqual(self.client.get(INCS).data['count'], 2)

    def test_resolver_reparar_abre_mantenimiento_correctivo(self):
        iid = self.reportar().data['id']
        resp = self.client.post(f'{INCS}{iid}/resolver/', {'decision': 'REPARAR', 'notas': 'Al taller'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['estado'], 'RESUELTA')
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'EN_REPARACION')
        self.assertTrue(RegistroMantenimiento.objects.filter(tipo='CORRECTIVO', estado='EN_CURSO').exists())
        self.assertEqual(self.client.post(f'{INCS}{iid}/resolver/', {'decision': 'SIN_ACCION'}, format='json').status_code, 409)

    def test_reparar_no_aplica_a_robo_ni_a_herramienta_asignada(self):
        iid = self.reportar(tipo='ROBO').data['id']
        self.assertEqual(self.client.post(f'{INCS}{iid}/resolver/', {'decision': 'REPARAR'}, format='json').status_code, 400)
        self.entregar()
        iid2 = self.reportar().data['id']
        self.assertEqual(self.client.post(f'{INCS}{iid2}/resolver/', {'decision': 'REPARAR'}, format='json').status_code, 409)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'ASIGNADA')

    def test_dar_de_baja_exige_permiso_de_baja(self):
        iid = self.reportar().data['id']
        usuario, c = self.cliente_con_permisos(
            'resolvedor', ['HERRAMIENTAS.INCIDENCIAS.EDITAR', 'HERRAMIENTAS.INCIDENCIAS.VER']
        )
        resp = c.post(f'{INCS}{iid}/resolver/', {'decision': 'DAR_DE_BAJA'}, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(IncidenciaHerramienta.objects.get(pk=iid).estado, 'ABIERTA')
        usuario2, c2 = self.cliente_con_permisos(
            'aprobador', ['HERRAMIENTAS.INCIDENCIAS.EDITAR', 'HERRAMIENTAS.BAJA.APROBAR']
        )
        self.assertEqual(c2.post(f'{INCS}{iid}/resolver/', {'decision': 'DAR_DE_BAJA'}, format='json').status_code, 200)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'DADA_DE_BAJA')

    def test_robo_de_herramienta_asignada_la_marca_perdida_y_cierra_la_asignacion(self):
        self.entregar()
        iid = self.reportar(tipo='ROBO').data['id']
        resp = self.client.post(f'{INCS}{iid}/resolver/', {'decision': 'DAR_DE_BAJA'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'PERDIDA')
        self.assertEqual(AsignacionHerramienta.objects.get().estado, 'NO_DEVUELTA')
        self.assertEqual(self.entregar().status_code, 409)

    def test_dano_de_herramienta_asignada_no_se_da_de_baja_sin_devolver(self):
        self.entregar()
        iid = self.reportar().data['id']
        self.assertEqual(self.client.post(f'{INCS}{iid}/resolver/', {'decision': 'DAR_DE_BAJA'}, format='json').status_code, 409)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'ASIGNADA')

    def test_reponer_y_sin_accion_solo_cierran(self):
        for decision in ('REPONER', 'SIN_ACCION'):
            iid = self.reportar().data['id']
            self.assertEqual(self.client.post(f'{INCS}{iid}/resolver/', {'decision': decision}, format='json').status_code, 200)
        self.herramienta.refresh_from_db()
        self.assertEqual(self.herramienta.estado_operativo, 'DISPONIBLE')


class AlertasYPermisosTests(FaseTresBase):
    def test_alertas_cuentan_vencidos_por_vencer_y_prestamos(self):
        self.plan_vencido(nombre='Vencido')
        self.crear_plan(nombre='Pronto', intervalo_dias=3)
        otra = Herramienta.objects.create(
            nombre='Sierra', categoria=self.categoria, sucursal=self.sucursal, creado_por=self.admin,
            garantia_hasta=self.hoy() + timedelta(days=10),
        )
        r = self.client.post(
            ASIG, {'herramienta': otra.id, 'tecnico': str(self.tecnico.pk)}, format='json'
        )
        AsignacionHerramienta.objects.filter(pk=r.data['id']).update(
            fecha_devolucion_esperada=self.hoy() - timedelta(days=4)
        )
        resp = self.client.get(ALERTAS)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['total'], 4)  # vencido + por vencer + préstamo + garantía
        urgencias = [i['urgencia'] for i in resp.data['results']]
        self.assertEqual(urgencias[:2], ['VENCIDO', 'VENCIDO'])
        self.assertEqual(resp.data['results'][0]['dias_vencido'], 60 - 30)
        self.assertEqual(self.client.get(ALERTAS, {'sucursal_id': self.otra_sucursal.id}).data['total'], 0)

    def test_alertas_ignoran_planes_inactivos_y_herramientas_de_baja(self):
        plan = self.plan_vencido()
        PlanMantenimientoHerramienta.objects.filter(pk=plan.pk).update(activo=False)
        self.assertEqual(self.client.get(ALERTAS).data['total'], 0)
        PlanMantenimientoHerramienta.objects.filter(pk=plan.pk).update(activo=True)
        Herramienta.objects.filter(pk=self.herramienta.pk).update(estado_operativo='DADA_DE_BAJA')
        self.assertEqual(self.client.get(ALERTAS).data['total'], 0)

    def test_sin_permisos_recibe_403_y_sin_login_401(self):
        nadie = self._crear_usuario('nadie_f3')
        c = APIClient()
        c.force_authenticate(user=nadie)
        for url in (PLANES, MANTS, INCS, ALERTAS):
            self.assertEqual(c.get(url).status_code, 403, url)
        self.assertEqual(c.post(PLANES, {'herramienta': self.herramienta.id, 'nombre': 'x', 'intervalo_dias': 5}, format='json').status_code, 403)
        self.assertEqual(c.post(MANTS, {'herramienta': self.herramienta.id, 'tipo': 'PREVENTIVO', 'descripcion': 'x'}, format='json').status_code, 403)
        self.assertEqual(c.post(INCS, {'herramienta': self.herramienta.id, 'tipo': 'DANO', 'descripcion': 'x'}, format='json').status_code, 403)
        self.assertEqual(APIClient().get(ALERTAS).status_code, 401)

    def test_metodos_no_soportados_responden_405(self):
        rid = self.client.post(
            MANTS, {'herramienta': self.herramienta.id, 'tipo': 'PREVENTIVO', 'descripcion': 'x'}, format='json'
        ).data['id']
        self.assertEqual(self.client.delete(f'{MANTS}{rid}/').status_code, 405)
        self.assertEqual(self.client.patch(f'{MANTS}{rid}/', {}, format='json').status_code, 405)
        iid = self.client.post(
            INCS, {'herramienta': self.herramienta.id, 'tipo': 'DANO', 'descripcion': 'x'}, format='json'
        ).data['id']
        self.assertEqual(self.client.delete(f'{INCS}{iid}/').status_code, 405)

    def test_cada_accion_de_mantenimiento_exige_su_permiso(self):
        rid = self.client.post(
            MANTS, {'herramienta': self.herramienta.id, 'tipo': 'PREVENTIVO', 'descripcion': 'x'}, format='json'
        ).data['id']
        usuario, c = self.cliente_con_permisos('solo_ver_mant', ['HERRAMIENTAS.MANTENIMIENTOS.VER'])
        self.assertEqual(c.get(MANTS).status_code, 200)
        self.assertEqual(c.post(f'{MANTS}{rid}/finalizar/', {'resultado': 'x', 'estado_fisico_final': 'BUENO'}, format='json').status_code, 403)
        self.assertEqual(c.post(f'{MANTS}{rid}/cancelar/', {'motivo': 'x'}, format='json').status_code, 403)
        self.assertEqual(c.post(PLANES, {'herramienta': self.herramienta.id, 'nombre': 'x', 'intervalo_dias': 5}, format='json').status_code, 403)
