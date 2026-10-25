import logging
from datetime import date, datetime, time, timedelta
from rest_framework import viewsets, status, pagination
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from django.shortcuts import get_object_or_404
from .models import (
    BloqueoAgendaSucursal, Cita, CitaHistorial, ConfiguracionAgendaSucursal, HorarioAgendaSucursal,
    ListaEsperaCita, OrdenTrabajo, Hallazgo, HallazgoFoto, OrdenServicio, OrdenRepuesto, PlantillaPreventiva,
    PlantillaCorrectiva, PlantillaDiagnostico, TipoServicio, OrdenHistorialEstado, OrdenEvidenciaIngreso,
    AccesorioPertenencia, OrdenAccesorioPertenencia
)
from apps.vehiculos.models import Vehiculo
from .serializers import (
    BloqueoAgendaSucursalSerializer, CitaHistorialSerializer, CitaSerializer, ConfiguracionAgendaSucursalSerializer,
    ListaEsperaCitaSerializer, ReservaPublicaCitaSerializer,
    OrdenTrabajoListSerializer, OrdenTrabajoDetailSerializer,
    HallazgoSerializer, OrdenServicioSerializer, OrdenRepuestoSerializer,
    PlantillaPreventivaSerializer, PlantillaCorrectivaSerializer, PlantillaDiagnosticoSerializer, TipoServicioSerializer,
    OrdenEvidenciaIngresoSerializer,
    AccesorioPertenenciaSerializer, OrdenAccesorioPertenenciaSerializer
)
from .services import aprobar_cotizacion_orden
from apps.inventario.models import MovimientoInventario, InventarioStock, Sucursal
from apps.clientes.models import Cliente
from apps.ventas.models import Venta, DetalleVenta
from apps.ventas.services import VentasService
from apps.seguridad.permissions import TienePermiso, TieneAlgunPermiso, permisos_efectivos
import uuid

logger = logging.getLogger(__name__)


class OrdenTrabajoViewSet(viewsets.ModelViewSet):
    def get_permissions(self):
        if self.action == 'aprobar_servicios':
            return [TienePermiso("ORDENES_TRABAJO.APROBAR")]
        if self.action == 'finalizar_orden':
            return [TienePermiso("ORDENES_TRABAJO.FINALIZAR")]
        if self.action == 'enviar_a_pos':
            return [TienePermiso("VENTAS.POS.CREAR")]
        if self.action == 'anular':
            return [TienePermiso("ORDENES_TRABAJO.CAMBIAR_ESTADO")]
        if self.action == 'accesorios':
            if self.request.method == 'GET':
                return [TienePermiso("ORDENES_TRABAJO.VER")]
            return [TieneAlgunPermiso("ORDENES_TRABAJO.GESTIONAR_ACCESORIOS", "ORDENES_TRABAJO.CREAR")]
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("ORDENES_TRABAJO.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    def update(self, request, *args, **kwargs):
        # Extender el vencimiento de la cotización es una acción sensible
        # aparte de "editar la orden" en general (motivo, kilometraje, etc.):
        # exige su propio permiso aunque ambas viajen por el mismo PATCH
        # genérico del ViewSet. Ocultar el botón en el frontend no alcanza,
        # ya que este mismo endpoint acepta el campo sin distinción.
        if "fecha_vencimiento_cotizacion" in request.data:
            if "ORDENES_TRABAJO.EXTENDER_VENCIMIENTO" not in permisos_efectivos(request.user):
                raise PermissionDenied(
                    "No tienes permiso para extender el vencimiento de la cotización."
                )
        # Prometer/editar la fecha de entrega al cliente es igual de sensible,
        # pero con su propio permiso (a diferencia del vencimiento, el
        # Mecánico sí lo conserva por defecto).
        if "fecha_estimada_entrega" in request.data:
            if "ORDENES_TRABAJO.PROMETER_ENTREGA" not in permisos_efectivos(request.user):
                raise PermissionDenied(
                    "No tienes permiso para prometer/editar la fecha de entrega."
                )
        return super().update(request, *args, **kwargs)

    def get_queryset(self):
        # Evitar N+1 en las consultas, usando select_related para FK y prefetch para M:N
        queryset = OrdenTrabajo.objects.select_related(
            'vehiculo', 'recepcionista', 'mecanico_asignado', 'cliente'
        ).prefetch_related('vehiculo__clientes')
        
        if self.action == 'retrieve':
            queryset = queryset.prefetch_related(
                'hallazgos__fotos', 'servicios', 'evidencias_ingreso',
                'accesorios_pertenencias__accesorio',
                Prefetch('repuestos', queryset=OrdenRepuesto.objects.select_related('repuesto'))
            )
            
        # Filtrado por rol (Mecánico solo ve las suyas)
        user = self.request.user
        if hasattr(user, 'usuario_roles'):
            is_mecanico = user.usuario_roles.filter(id_rol__codigo='TÉCNICO_AUTOMOTRIZ', estado=True).exists()
            is_admin = user.usuario_roles.filter(id_rol__codigo='ADMINISTRADOR', estado=True).exists()
            
            if is_mecanico and not is_admin:
                queryset = queryset.filter(mecanico_asignado=user)

        params = self.request.query_params
        estado = params.get('estado')
        mecanico_asignado = params.get('mecanico_asignado')
        cliente = params.get('cliente')
        placa = params.get('placa')
        fecha_desde = params.get('fecha_desde')
        fecha_hasta = params.get('fecha_hasta')

        sucursal = params.get('sucursal') or params.get('sucursal_id')
        if sucursal:
            queryset = queryset.filter(sucursal_id=sucursal)
        if estado:
            queryset = queryset.filter(estado=estado)
        if mecanico_asignado:
            queryset = queryset.filter(mecanico_asignado_id=mecanico_asignado)
        if cliente:
            queryset = queryset.filter(cliente_id=cliente)
        if placa:
            queryset = queryset.filter(vehiculo__placa__icontains=placa)
        if fecha_desde:
            queryset = queryset.filter(fecha_ingreso__date__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(fecha_ingreso__date__lte=fecha_hasta)

        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return OrdenTrabajoListSerializer
        return OrdenTrabajoDetailSerializer

    def perform_create(self, serializer):
        # Generar numero de OT unico secuencial
        last_ot = OrdenTrabajo.objects.order_by('-id').first()
        next_num = 1 if not last_ot else last_ot.id + 1
        numero_ot = f"{next_num:06d}"

        # Obtener sucursal desde datos enviados o fallback
        sucursal_id = self.request.data.get('sucursal') or self.request.data.get('sucursal_id')
        sucursal = None
        if sucursal_id:
            from apps.inventario.models import Sucursal
            sucursal = Sucursal.objects.filter(pk=sucursal_id).first()
        if not sucursal:
            from apps.inventario.models import Sucursal
            sucursal = Sucursal.objects.first()
        
        orden = serializer.save(
            recepcionista=self.request.user,
            numero=numero_ot,
            sucursal=sucursal
        )
        
        if orden.cliente:
            orden.vehiculo.clientes.add(orden.cliente)
        
        if orden.kilometraje_ingreso is not None:
            vehiculo = orden.vehiculo
            vehiculo.kilometraje_actual = orden.kilometraje_ingreso
            vehiculo.save(update_fields=['kilometraje_actual'])
            
        # Generar primer historial de estado
        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=orden.estado,
            usuario=self.request.user
        )

    # Transiciones de estado permitidas fuera de las acciones dedicadas (aprobar_servicios,
    # finalizar_orden, enviar_a_pos, anular). Cualquier otro cambio de estado vía PATCH/PUT
    # directo se rechaza para evitar saltarse las validaciones de negocio de esas acciones.
    TRANSICIONES_MANUALES_PERMITIDAS = {
        (OrdenTrabajo.Estado.RECEPCIONADO, OrdenTrabajo.Estado.INSPECCION),
        (OrdenTrabajo.Estado.INSPECCION, OrdenTrabajo.Estado.ESPERANDO_APROBACION),
        # Atajo: si en recepción ya se sabe qué se necesita (ver "Generar Cotización
        # Directa" en el frontend), se puede cotizar sin pasar por INSPECCION.
        (OrdenTrabajo.Estado.RECEPCIONADO, OrdenTrabajo.Estado.ESPERANDO_APROBACION),
    }

    def perform_update(self, serializer):
        orden_anterior = self.get_object()
        estado_anterior = orden_anterior.estado
        nuevo_estado = serializer.validated_data.get('estado', estado_anterior)

        if nuevo_estado != estado_anterior and (estado_anterior, nuevo_estado) not in self.TRANSICIONES_MANUALES_PERMITIDAS:
            raise ValidationError(
                f"No se puede cambiar el estado de '{estado_anterior}' a '{nuevo_estado}' directamente. "
                "Usa la acción correspondiente (aprobar, finalizar, enviar a POS o anular)."
            )

        # Enviar la cotización al cliente (→ ESPERANDO_APROBACION) es una responsabilidad
        # distinta de editar la orden (agregar hallazgos/servicios/repuestos); requiere
        # ORDENES_TRABAJO.APROBAR aunque el usuario tenga EDITAR.
        if nuevo_estado == OrdenTrabajo.Estado.ESPERANDO_APROBACION and estado_anterior != nuevo_estado:
            if not self.request.user.is_superuser and 'ORDENES_TRABAJO.APROBAR' not in permisos_efectivos(self.request.user):
                raise PermissionDenied("No tiene permiso para enviar la cotización al cliente.")

        orden = serializer.save()
        
        # Guardar en el historial si el estado cambió
        if estado_anterior != orden.estado:
            OrdenHistorialEstado.objects.create(
                orden=orden,
                estado=orden.estado,
                usuario=self.request.user
            )
        
        # Transición a ESPERANDO_APROBACION: Calcular fecha de vencimiento si no tiene o si recién entra al estado
        if estado_anterior != OrdenTrabajo.Estado.ESPERANDO_APROBACION and orden.estado == OrdenTrabajo.Estado.ESPERANDO_APROBACION:
            from apps.seguridad.models import Empresa
            empresa = Empresa.objects.first()
            dias = empresa.dias_validez_cotizacion if empresa else 15
            orden.fecha_vencimiento_cotizacion = timezone.now() + timezone.timedelta(days=dias)
            orden.save(update_fields=['fecha_vencimiento_cotizacion'])

    @action(detail=True, methods=['post'])
    def aprobar_servicios(self, request, pk=None):
        """Endpoint para aprobar masivamente servicios y repuestos luego que el cliente revisa."""
        orden = self.get_object()
        
        # Validar vencimiento de la cotización
        if orden.fecha_vencimiento_cotizacion and timezone.now() > orden.fecha_vencimiento_cotizacion:
            return Response(
                {'error': 'La cotización ha expirado. Por favor, actualice la fecha de vencimiento en los detalles de la orden para proceder.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validar payload
        servicios_ids = request.data.get('servicios_aprobados', [])
        repuestos_ids = request.data.get('repuestos_aprobados', [])

        try:
            aprobar_cotizacion_orden(
                orden,
                servicios_ids,
                repuestos_ids,
                usuario=request.user,
            )
        except ValidationError as exc:
            detail = exc.detail[0] if isinstance(exc.detail, list) else exc.detail
            return Response({'error': detail}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'ok', 'message': 'Aprobacion registrada correctamente.'})
        
    @action(detail=True, methods=['post'])
    def finalizar_orden(self, request, pk=None):
        orden = self.get_object()
        
        if orden.estado != OrdenTrabajo.Estado.APROBADO:
            return Response({'error': 'La orden debe estar en estado APROBADO para finalizarse.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Validar servicios aprobados completados
        servicios_aprobados = orden.servicios.filter(aprobado_cliente=True)
        if servicios_aprobados.filter(completado=False).exists():
            return Response({'error': 'Todos los servicios aprobados deben estar marcados como Terminados.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Validar repuestos aprobados instalados
        repuestos_aprobados = orden.repuestos.filter(aprobado_cliente=True)
        if repuestos_aprobados.filter(instalado=False).exists():
            return Response({'error': 'Todos los repuestos aprobados deben estar marcados como Instalados.'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Cambiar estado
        orden.estado = OrdenTrabajo.Estado.FINALIZADO
        orden.fecha_finalizacion = timezone.now()
        orden.save(update_fields=['estado', 'fecha_finalizacion'])

        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=orden.estado,
            usuario=request.user,
        )

        return Response({'status': 'ok', 'message': 'Orden finalizada correctamente.', 'estado': orden.estado})

    @action(detail=True, methods=['post'])
    def enviar_a_pos(self, request, pk=None):
        orden = self.get_object()
        
        if orden.estado != OrdenTrabajo.Estado.FINALIZADO:
            return Response({'error': 'La orden debe estar en estado FINALIZADO para enviarse a POS.'}, status=status.HTTP_400_BAD_REQUEST)
            
        if not orden.cliente:
            return Response({'error': 'La orden no tiene un cliente asignado. Asigne un cliente en los detalles de la orden antes de cobrar.'}, status=status.HTTP_400_BAD_REQUEST)
            
        sucursal_id = request.data.get('sucursal_id')
        if not sucursal_id:
            raise ValidationError("Debe especificar la sucursal (sucursal_id) para enviar la orden al POS.")


        venta_existente = Venta.objects.filter(
            ticket_kiosko__startswith=f"OT-{orden.id}-",
            estado=Venta.Estado.PRE_VENTA
        ).first()
        
        if venta_existente:
            return Response({
                'status': 'ok', 
                'message': 'Ya existe un ticket en POS.', 
                'venta_id': venta_existente.id,
                'ticket': venta_existente.ticket_kiosko,
                'estado_orden': orden.estado
            })
            
        with transaction.atomic():
            ticket_code = f"OT-{orden.id}-{str(uuid.uuid4())[:4].upper()}"
            venta = Venta.objects.create(
                cliente=orden.cliente,
                vehiculo=orden.vehiculo,
                sucursal_id=sucursal_id,
                estado=Venta.Estado.PRE_VENTA,
                ticket_kiosko=ticket_code,
                kilometraje=orden.kilometraje_ingreso
            )
            
            subtotal_acumulado = 0
            
            # Repuestos
            # select_related('repuesto') evita una query extra por línea al
            # leer rep.repuesto.precio_compra para el costo histórico.
            for rep in orden.repuestos.filter(aprobado_cliente=True, instalado=True).select_related('repuesto'):
                subtotal_linea = rep.cantidad * rep.precio_unitario
                DetalleVenta.objects.create(
                    venta=venta,
                    repuesto=rep.repuesto,
                    cantidad=rep.cantidad,
                    precio_unitario=rep.precio_unitario,
                    costo_unitario=rep.repuesto.precio_compra,
                    subtotal_linea=subtotal_linea
                )
                subtotal_acumulado += subtotal_linea
                
            # Servicios
            for serv in orden.servicios.filter(aprobado_cliente=True, completado=True):
                subtotal_linea = serv.precio_estimado
                # Cantidad = 1, usando descripcion_servicio
                DetalleVenta.objects.create(
                    venta=venta,
                    descripcion_servicio=serv.descripcion,
                    cantidad=1,
                    precio_unitario=serv.precio_estimado,
                    subtotal_linea=subtotal_linea
                )
                subtotal_acumulado += subtotal_linea
                
            venta.total = subtotal_acumulado
            venta.subtotal, venta.igv = VentasService.descomponer_total_con_impuesto(venta.total)
            venta.save()
            
            # NOTA: Ya no cambiamos a FACTURADO aquí, se hará cuando se pague en POS.
            
        return Response({
            'status': 'ok',
            'message': 'Enviado a POS correctamente.',
            'venta_id': venta.id,
            'ticket': venta.ticket_kiosko,
            'estado_orden': orden.estado
        })

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def anular(self, request, pk=None):
        """
        Anula una orden de trabajo: libera las reservas de stock de sus repuestos
        aprobados aún no instalados y registra el motivo en el historial de estados.
        No revierte repuestos ya instalados (ese stock ya salió físicamente).
        """
        orden = self.get_object()

        if orden.estado in (OrdenTrabajo.Estado.FACTURADO, OrdenTrabajo.Estado.CANCELADO):
            raise ValidationError(f"No se puede anular una orden en estado '{orden.estado}'.")

        venta_existente = Venta.objects.filter(
            ticket_kiosko__startswith=f"OT-{orden.id}-",
            estado=Venta.Estado.PRE_VENTA
        ).exists()
        if venta_existente:
            raise ValidationError(
                "No se puede anular: ya existe un ticket en el Punto de Venta para esta orden. "
                "Cancele ese ticket antes de anular la orden."
            )

        if orden.repuestos.filter(instalado=True).exists():
            raise ValidationError(
                "No se puede anular: esta orden tiene repuestos ya instalados (ese stock ya salió del almacén)."
            )

        motivo = (request.data.get('motivo') or '').strip()
        if not motivo:
            raise ValidationError("Debe indicar el motivo de la anulación.")

        categoria = request.data.get('categoria') or None
        categorias_validas = OrdenHistorialEstado.MotivoCategoria.values
        if categoria and categoria not in categorias_validas:
            raise ValidationError(f"Categoría de motivo inválida: '{categoria}'.")

        for orp in orden.repuestos.filter(aprobado_cliente=True, instalado=False):
            stock_record = InventarioStock.objects.filter(repuesto=orp.repuesto).first()
            if stock_record and stock_record.stock_reservado >= orp.cantidad:
                stock_record.stock_disponible += orp.cantidad
                stock_record.stock_reservado -= orp.cantidad
                stock_record.save()

                MovimientoInventario.objects.create(
                    repuesto=orp.repuesto,
                    ubicacion=stock_record.ubicacion,
                    tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                    cantidad=orp.cantidad,
                    stock_resultante=stock_record.stock_disponible,
                    motivo=f"Liberación de reserva por anulación de OT-{orden.numero}",
                    usuario=request.user,
                    referencia_id=orden.id,
                    referencia_tipo='OT_ANULACION'
                )

        orden.estado = OrdenTrabajo.Estado.CANCELADO
        orden.save(update_fields=['estado'])

        OrdenHistorialEstado.objects.create(
            orden=orden,
            estado=orden.estado,
            usuario=request.user,
            observaciones=motivo,
            motivo_categoria=categoria
        )

        serializer = self.get_serializer(orden)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def generar_pdf(self, request, pk=None):
        orden = self.get_object()

        # Calcular totales
        total_servicios = sum(s.precio_estimado for s in orden.servicios.all())
        total_repuestos = sum(r.cantidad * r.precio_unitario for r in orden.repuestos.all())
        total_general = total_servicios + total_repuestos

        from apps.seguridad.models import CuentaBancaria, Empresa
        cuentas = CuentaBancaria.objects.filter(estado=True)

        # Configuración real de la empresa (la misma que ve el usuario en
        # Configuración > Empresa), no datos de ejemplo fijos.
        empresa, _ = Empresa.objects.get_or_create(id=1, defaults={
            "razon_social": "Mi Empresa",
            "ruc": "00000000000",
            "direccion": "Dirección no configurada"
        })
        empresa_logo_data_uri = None
        if empresa.logo:
            import base64
            import mimetypes
            try:
                # xhtml2pdf (a diferencia del navegador) no resuelve de forma
                # confiable una ruta de archivo o una URL del propio servidor
                # para cargar la imagen del logo. La forma robusta es incrustar
                # los bytes de la imagen directamente en el HTML como base64,
                # así el logo queda embebido en el PDF sin depender de ninguna
                # ruta externa.
                with empresa.logo.open('rb') as f:
                    logo_bytes = f.read()
                mime_type = mimetypes.guess_type(empresa.logo.name)[0] or 'image/png'
                empresa_logo_data_uri = f"data:{mime_type};base64,{base64.b64encode(logo_bytes).decode('ascii')}"
            except (OSError, ValueError) as e:
                logger.error(f"No se pudo incrustar el logo de la empresa en el PDF: {e}")
                empresa_logo_data_uri = None

        # Número de cotización: se genera una sola vez con la serie PROFORMA
        # (Configuración > Series Internas) de la sucursal indicada, y se
        # reutiliza en reimpresiones. Si aún no hay serie configurada para esa
        # sucursal, se sigue mostrando el número de la OT (comportamiento previo).
        if not orden.numero_cotizacion:
            sucursal_id = request.query_params.get('sucursal_id')
            if sucursal_id:
                from apps.ventas.models import SerieDocumentoInterno
                with transaction.atomic():
                    serie = SerieDocumentoInterno.objects.select_for_update().filter(
                        sucursal_id=sucursal_id,
                        tipo_documento=SerieDocumentoInterno.TipoDocumento.PROFORMA,
                        estado=True
                    ).first()
                    if serie:
                        numero = serie.generar_siguiente_correlativo()
                        serie.correlativo_actual += 1
                        serie.save(update_fields=['correlativo_actual'])
                        orden.numero_cotizacion = numero
                        orden.save(update_fields=['numero_cotizacion'])

        numero_documento = orden.numero_cotizacion or orden.numero

        # Consultar Estado Documentario del vehículo (SOAT y Revisión Técnica)
        documento_soat = None
        documento_rev_tecnica = None
        if orden.vehiculo:
            try:
                from apps.documentos.models import Documento
                docs_vehiculo = Documento.objects.filter(vehiculo=orden.vehiculo, activo=True)
                doc_soat = docs_vehiculo.filter(tipo=Documento.Tipo.SOAT).order_by('-fecha_vencimiento', '-id').first()
                doc_rt = docs_vehiculo.filter(tipo=Documento.Tipo.REVISION_TECNICA).order_by('-fecha_vencimiento', '-id').first()

                def _formatear_doc(doc):
                    if not doc:
                        return None
                    estado = doc.estado_vigencia
                    color = '#16a34a'  # Verde: Vigente
                    texto_estado = 'Vigente'
                    if estado == 'VENCIDO':
                        color = '#dc2626'  # Rojo: Vencido
                        texto_estado = 'Vencido'
                    elif estado == 'POR_VENCER':
                        color = '#ea580c'  # Naranja: Por vencer
                        texto_estado = 'Por vencer'
                    elif estado == 'SIN_VENCIMIENTO':
                        color = '#0284c7'  # Azul: Registrado
                        texto_estado = 'Registrado'

                    return {
                        'tipo': doc.get_tipo_display(),
                        'numero': doc.numero,
                        'entidad_emisora': doc.entidad_emisora,
                        'fecha_vencimiento': doc.fecha_vencimiento,
                        'estado_vigencia': estado,
                        'texto_estado': texto_estado,
                        'color': color,
                    }

                documento_soat = _formatear_doc(doc_soat)
                documento_rev_tecnica = _formatear_doc(doc_rt)
            except Exception as e:
                logger.warning(f"Error consultando documentos del vehículo {orden.vehiculo.placa}: {e}")

        # Configurar contexto
        context = {
            'orden': orden,
            'numero_documento': numero_documento,
            'total_servicios': total_servicios,
            'total_repuestos': total_repuestos,
            'total_general': total_general,
            'cuentas_bancarias': cuentas,
            'empresa': empresa,
            'empresa_logo_data_uri': empresa_logo_data_uri,
            'documento_soat': documento_soat,
            'documento_rev_tecnica': documento_rev_tecnica,
        }

        from django.template.loader import render_to_string
        from django.http import HttpResponse
        from xhtml2pdf import pisa

        html_string = render_to_string('taller/proforma_pdf.html', context)

        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="cotizacion_{numero_documento}.pdf"'

        pisa_status = pisa.CreatePDF(
            html_string, dest=response
        )

        if pisa_status.err:
            return HttpResponse('Error generando PDF', status=500)

        return response

    @action(detail=True, methods=['get', 'post'], url_path='evidencias')
    def evidencias(self, request, pk=None):
        orden = self.get_object()
        if request.method == 'GET':
            evidencias = orden.evidencias_ingreso.select_related('registrado_por').all()
            serializer = OrdenEvidenciaIngresoSerializer(evidencias, many=True)
            return Response(serializer.data)

        # POST: Recibir una o múltiples imágenes de evidencia de ingreso
        imagenes = request.FILES.getlist('imagenes')
        if not imagenes and 'imagen' in request.FILES:
            imagenes = [request.FILES['imagen']]

        if not imagenes:
            return Response({'error': 'No se enviaron imágenes para registrar.'}, status=status.HTTP_400_BAD_REQUEST)

        descripcion_general = request.data.get('descripcion', '').strip()
        descripciones_raw = request.data.get('descripciones', None)
        descripciones_list = []
        if descripciones_raw:
            import json
            try:
                descripciones_list = json.loads(descripciones_raw) if isinstance(descripciones_raw, str) else descripciones_raw
            except Exception:
                descripciones_list = []

        nuevas_evidencias = []
        with transaction.atomic():
            for idx, img in enumerate(imagenes):
                desc = descripciones_list[idx] if idx < len(descripciones_list) else descripcion_general
                evidencia = OrdenEvidenciaIngreso.objects.create(
                    orden=orden,
                    imagen=img,
                    descripcion=desc or '',
                    registrado_por=request.user
                )
                nuevas_evidencias.append(evidencia)

        serializer = OrdenEvidenciaIngresoSerializer(nuevas_evidencias, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='accesorios')
    def accesorios(self, request, pk=None):
        orden = self.get_object()
        if request.method == 'GET':
            items = orden.accesorios_pertenencias.select_related('accesorio').all()
            return Response(OrdenAccesorioPertenenciaSerializer(items, many=True).data)

        # POST: Guardar o sincronizar la lista de accesorios que el cliente deja
        items_data = request.data.get('accesorios', [])
        if not isinstance(items_data, list):
            return Response({'error': 'accesorios debe ser una lista'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            orden.accesorios_pertenencias.all().delete()
            nuevos = []
            for item in items_data:
                accesorio_id = item.get('accesorio_id') or item.get('accesorio')
                if not accesorio_id:
                    continue
                nuevos.append(
                    OrdenAccesorioPertenencia(
                        orden=orden,
                        accesorio_id=accesorio_id,
                        cantidad=max(1, int(item.get('cantidad', 1))),
                        estado_conservacion=item.get('estado_conservacion') or 'BUENO',
                        observaciones=(item.get('observaciones') or '').strip()
                    )
                )
            if nuevos:
                OrdenAccesorioPertenencia.objects.bulk_create(nuevos)

        items_actualizados = orden.accesorios_pertenencias.select_related('accesorio').all()
        return Response(
            OrdenAccesorioPertenenciaSerializer(items_actualizados, many=True).data,
            status=status.HTTP_200_OK
        )



class OrdenEvidenciaIngresoViewSet(viewsets.ModelViewSet):
    queryset = OrdenEvidenciaIngreso.objects.select_related('registrado_por', 'orden').all()
    serializer_class = OrdenEvidenciaIngresoSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    def perform_create(self, serializer):
        serializer.save(registrado_por=self.request.user)


class HallazgoViewSet(viewsets.ModelViewSet):
    queryset = Hallazgo.objects.prefetch_related('fotos').all()
    serializer_class = HallazgoSerializer
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    def perform_create(self, serializer):
        hallazgo = serializer.save(registrado_por=self.request.user)
        fotos = self.request.FILES.getlist('fotos')
        # Límite estricto: máximo 2 fotos por hallazgo
        for f in fotos[:2]:
            HallazgoFoto.objects.create(
                hallazgo=hallazgo,
                imagen=f,
                registrado_por=self.request.user
            )

    def perform_update(self, serializer):
        hallazgo = serializer.save()
        fotos = self.request.FILES.getlist('fotos')
        if fotos:
            fotos_actuales = hallazgo.fotos.count()
            disponibles = max(0, 2 - fotos_actuales)
            for f in fotos[:disponibles]:
                HallazgoFoto.objects.create(
                    hallazgo=hallazgo,
                    imagen=f,
                    registrado_por=self.request.user
                )

    @action(detail=True, methods=['delete'], url_path=r'fotos/(?P<foto_id>\d+)')
    def eliminar_foto(self, request, pk=None, foto_id=None):
        hallazgo = self.get_object()
        foto = get_object_or_404(HallazgoFoto, pk=foto_id, hallazgo=hallazgo)
        if foto.imagen:
            foto.imagen.delete(save=False)
        foto.delete()
        return Response({'status': 'Foto eliminada'}, status=status.HTTP_200_OK)

class OrdenServicioViewSet(viewsets.ModelViewSet):
    queryset = OrdenServicio.objects.all()
    serializer_class = OrdenServicioSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    @action(detail=True, methods=['patch'])
    def marcar_completado(self, request, pk=None):
        servicio = self.get_object()
        servicio.completado = not servicio.completado
        servicio.save(update_fields=['completado'])
        return Response({'status': 'ok', 'completado': servicio.completado})

class OrdenRepuestoViewSet(viewsets.ModelViewSet):
    queryset = OrdenRepuesto.objects.select_related('repuesto')
    serializer_class = OrdenRepuestoSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ORDENES_TRABAJO.ELIMINAR")]
        return [TienePermiso("ORDENES_TRABAJO.EDITAR")]

    @action(detail=True, methods=['patch'])
    @transaction.atomic
    def marcar_instalado(self, request, pk=None):
        """
        Alterna 'instalado'. Es simétrico: activar convierte la reserva en
        salida definitiva (Kardex SALIDA); desactivar devuelve esa reserva
        (Kardex RESERVA positivo). Antes solo activar tenía efecto, así que
        activar/desactivar repetidamente descontaba stock de más (bug real
        ya detectado en producción, corregido aquí).
        """
        repuesto_orden = self.get_object()
        orden = repuesto_orden.orden

        if orden.estado in (OrdenTrabajo.Estado.CANCELADO, OrdenTrabajo.Estado.FACTURADO):
            raise ValidationError(
                f"No se puede modificar la instalación de un repuesto en una orden '{orden.estado}'."
            )

        nuevo_valor = not repuesto_orden.instalado

        if nuevo_valor and not repuesto_orden.aprobado_cliente:
            raise ValidationError("No se puede instalar un repuesto que no ha sido aprobado.")

        sucursal = orden.sucursal
        qs_stock = InventarioStock.objects.select_for_update().filter(repuesto=repuesto_orden.repuesto)
        if sucursal:
            qs_stock_local = qs_stock.filter(ubicacion__almacen__sucursal=sucursal)
        else:
            qs_stock_local = qs_stock

        if nuevo_valor:
            # Para instalar, verificar de dónde proviene el stock físico:
            # 1. ¿Estaba en stock_reservado de la cotización en esta sucursal?
            # 2. O si no estaba reservado, ¿hay stock_disponible (ej. ingresó por compra posterior) en esta sucursal?
            stocks_reservados = list(
                qs_stock_local.filter(stock_reservado__gt=0).order_by('-stock_reservado')
            )
            total_reservado = sum(s.stock_reservado for s in stocks_reservados)

            if total_reservado >= repuesto_orden.cantidad:
                # Convertir la reserva previa en salida definitiva
                pendiente = repuesto_orden.cantidad
                for stock_record in stocks_reservados:
                    if pendiente <= 0:
                        break
                    descontar = min(stock_record.stock_reservado, pendiente)
                    stock_record.stock_reservado -= descontar
                    stock_record.save(update_fields=['stock_reservado'])
                    pendiente -= descontar

                    MovimientoInventario.objects.create(
                        repuesto=repuesto_orden.repuesto,
                        ubicacion=stock_record.ubicacion,
                        tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA,
                        cantidad=-descontar,
                        stock_resultante=stock_record.stock_disponible,
                        motivo=f"Instalación en OT-{orden.numero}",
                        usuario=request.user,
                        referencia_id=orden.id,
                        referencia_tipo='OT'
                    )
            else:
                # 2. Si no estaba reservado (aprobado sin stock), buscar en stock_disponible de esta sucursal
                stocks_disponibles = list(
                    qs_stock_local.filter(stock_disponible__gt=0).order_by('-stock_disponible')
                )
                total_disponible = sum(s.stock_disponible for s in stocks_disponibles)

                if total_disponible >= repuesto_orden.cantidad:
                    pendiente = repuesto_orden.cantidad
                    for stock_record in stocks_disponibles:
                        if pendiente <= 0:
                            break
                        descontar = min(stock_record.stock_disponible, pendiente)
                        stock_record.stock_disponible -= descontar
                        stock_record.save(update_fields=['stock_disponible'])
                        pendiente -= descontar

                        MovimientoInventario.objects.create(
                            repuesto=repuesto_orden.repuesto,
                            ubicacion=stock_record.ubicacion,
                            tipo_movimiento=MovimientoInventario.TipoMovimiento.SALIDA,
                            cantidad=-descontar,
                            stock_resultante=stock_record.stock_disponible,
                            motivo=f"Instalación en OT-{orden.numero} (compra directa)",
                            usuario=request.user,
                            referencia_id=orden.id,
                            referencia_tipo='OT'
                        )
                else:
                    # Validar si hay existencias en otra(s) sucursal(es)
                    total_otras_sucursales = 0
                    if sucursal:
                        qs_otras = InventarioStock.objects.filter(
                            repuesto=repuesto_orden.repuesto
                        ).exclude(ubicacion__almacen__sucursal=sucursal)
                        total_otras_sucursales = sum(s.stock_disponible for s in qs_otras)

                    nombre_sucursal = f"la sucursal '{sucursal.nombre}'" if sucursal else "el almacén"
                    if total_otras_sucursales >= repuesto_orden.cantidad:
                        raise ValidationError(
                            f"No se puede marcar como instalado: el repuesto '{repuesto_orden.repuesto.nombre}' no cuenta con stock en {nombre_sucursal} "
                            f"(requerido: {repuesto_orden.cantidad}, disponible en esta sede: {total_disponible}). "
                            f"Existen {total_otras_sucursales} unidades en otra(s) sucursal(es). "
                            "Debe realizar un Traslado de Almacén hacia esta sucursal antes de poder instalarlo."
                        )
                    else:
                        raise ValidationError(
                            f"No se puede marcar como instalado: el repuesto '{repuesto_orden.repuesto.nombre}' no cuenta con stock físico en {nombre_sucursal} "
                            f"(requerido: {repuesto_orden.cantidad}, disponible en esta sede: {total_disponible}). "
                            "Debe registrar la compra o ingreso de mercadería en el módulo de Compras antes de instalarlo."
                        )
        else:
            # Revertir instalación: devolver a la sucursal de la orden
            stock_record = qs_stock_local.order_by('-stock_disponible', '-stock_reservado').first()
            if not stock_record:
                stock_record = qs_stock.order_by('-stock_disponible', '-stock_reservado').first()

            if not stock_record:
                raise ValidationError(
                    f"No existe registro de inventario para el repuesto '{repuesto_orden.repuesto.nombre}'."
                )

            stock_record.stock_reservado += repuesto_orden.cantidad
            stock_record.save(update_fields=['stock_reservado'])
            MovimientoInventario.objects.create(
                repuesto=repuesto_orden.repuesto,
                ubicacion=stock_record.ubicacion,
                tipo_movimiento=MovimientoInventario.TipoMovimiento.RESERVA,
                cantidad=repuesto_orden.cantidad,
                stock_resultante=stock_record.stock_disponible,
                motivo=f"Reversión de instalación en OT-{orden.numero}",
                usuario=request.user,
                referencia_id=orden.id,
                referencia_tipo='OT'
            )

        repuesto_orden.instalado = nuevo_valor
        repuesto_orden.save(update_fields=['instalado'])

        return Response({'status': 'ok', 'instalado': repuesto_orden.instalado})

class PlantillaPreventivaViewSet(viewsets.ModelViewSet):
    queryset = PlantillaPreventiva.objects.all()
    serializer_class = PlantillaPreventivaSerializer

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("PLANTILLAS_TALLER.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("PLANTILLAS_TALLER.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("PLANTILLAS_TALLER.ELIMINAR")]
        return [TienePermiso("PLANTILLAS_TALLER.EDITAR")]


class PlantillaCorrectivaViewSet(viewsets.ModelViewSet):
    """
    CRUD de plantillas correctivas. Reutiliza los mismos permisos de
    PLANTILLAS_TALLER para no requerir nuevos perfiles de acceso.
    Filtro ?activo=true disponible para que NuevaOrdenPage solo cargue
    las plantillas visibles en recepción.
    """
    serializer_class = PlantillaCorrectivaSerializer

    def get_queryset(self):
        qs = PlantillaCorrectiva.objects.all()
        activo = self.request.query_params.get('activo')
        if activo is not None:
            qs = qs.filter(activo=(activo.lower() == 'true'))
        return qs

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("PLANTILLAS_TALLER.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("PLANTILLAS_TALLER.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("PLANTILLAS_TALLER.ELIMINAR")]
        return [TienePermiso("PLANTILLAS_TALLER.EDITAR")]


class PlantillaDiagnosticoViewSet(viewsets.ModelViewSet):
    """
    CRUD de plantillas de diagnóstico. Reutiliza los mismos permisos de
    PLANTILLAS_TALLER para no requerir nuevos perfiles de acceso.
    Filtro ?activo=true disponible para que NuevaOrdenPage solo cargue
    las plantillas visibles en recepción.
    """
    serializer_class = PlantillaDiagnosticoSerializer

    def get_queryset(self):
        qs = PlantillaDiagnostico.objects.all()
        activo = self.request.query_params.get('activo')
        if activo is not None:
            qs = qs.filter(activo=(activo.lower() == 'true'))
        return qs

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("PLANTILLAS_TALLER.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("PLANTILLAS_TALLER.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("PLANTILLAS_TALLER.ELIMINAR")]
        return [TienePermiso("PLANTILLAS_TALLER.EDITAR")]


class AccesorioPagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class AccesorioPertenenciaViewSet(viewsets.ModelViewSet):
    """
    CRUD del catálogo administrable de Accesorios y Pertenencias que pueden quedar en custodia.
    Soporta filtros ?activo=true, ?page_size=..., ?page=... y ?search=...
    """
    serializer_class = AccesorioPertenenciaSerializer
    pagination_class = AccesorioPagination

    def get_queryset(self):
        qs = AccesorioPertenencia.objects.all()
        activo = self.request.query_params.get('activo')
        if activo is not None:
            qs = qs.filter(activo=(activo.lower() == 'true'))
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(nombre__icontains=search.strip())
        return qs

    def paginate_queryset(self, queryset):
        if self.request.query_params.get('sin_paginar') == 'true':
            return None
        return super().paginate_queryset(queryset)

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ACCESORIOS_TALLER.VER")]
        if self.request.method == 'POST':
            return [TienePermiso("ACCESORIOS_TALLER.CREAR")]
        if self.request.method == 'DELETE':
            return [TienePermiso("ACCESORIOS_TALLER.ELIMINAR")]
        return [TienePermiso("ACCESORIOS_TALLER.EDITAR")]


class OrdenAccesorioPertenenciaViewSet(viewsets.ModelViewSet):
    """
    CRUD / listado de accesorios asociados a una orden de trabajo.
    Permite filtrar por ?orden_id=...
    """
    serializer_class = OrdenAccesorioPertenenciaSerializer

    def get_queryset(self):
        qs = OrdenAccesorioPertenencia.objects.select_related('accesorio', 'orden').all()
        orden_id = self.request.query_params.get('orden_id')
        if orden_id:
            qs = qs.filter(orden_id=orden_id)
        return qs

    def get_permissions(self):
        if self.request.method == 'GET':
            return [TienePermiso("ORDENES_TRABAJO.VER")]
        if self.request.method == 'POST':
            return [TieneAlgunPermiso("ORDENES_TRABAJO.GESTIONAR_ACCESORIOS", "ORDENES_TRABAJO.CREAR")]
        if self.request.method == 'DELETE':
            return [TieneAlgunPermiso("ORDENES_TRABAJO.GESTIONAR_ACCESORIOS", "ORDENES_TRABAJO.ELIMINAR")]
        return [TieneAlgunPermiso("ORDENES_TRABAJO.GESTIONAR_ACCESORIOS", "ORDENES_TRABAJO.EDITAR")]


# Los bloques movidos se re-exportan para que `from .views import ...` siga funcionando.
from .citas_views import (  # noqa: E402,F401
    asegurar_configuracion_agenda,
    registrar_historial_cita,
    calcular_disponibilidad_sucursal,
    obtener_o_crear_cliente_vehiculo,
    CitaPagination,
    TipoServicioViewSet,
    ConfiguracionAgendaSucursalViewSet,
    BloqueoAgendaSucursalViewSet,
    ListaEsperaCitaViewSet,
    CitasCatalogoPublicoView,
    DisponibilidadCitasPublicaView,
    ReservaCitaPublicaView,
    CitaViewSet,
)
from .publicas_views import (  # noqa: E402,F401
    ConsultaVehiculoPublicaView,
    AprobarCotizacionPublicaView,
)
