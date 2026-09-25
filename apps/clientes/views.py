from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import filters
from rest_framework.permissions import AllowAny
from .models import Cliente, Proveedor, Transportista
from .serializers import ClienteSerializer, ProveedorSerializer, TransportistaSerializer
from .services import ConsultaOrchestrator
from apps.seguridad.permissions import PermisoPorMetodoMixin
import logging

logger = logging.getLogger(__name__)

class ClienteViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = "CONTACTOS.CLIENTES.VER"
    permiso_crear = "CONTACTOS.CLIENTES.CREAR"
    permiso_editar = "CONTACTOS.CLIENTES.EDITAR"
    permiso_eliminar = "CONTACTOS.CLIENTES.ELIMINAR"
    queryset = Cliente.objects.filter(estado=True)
    serializer_class = ClienteSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['dni', 'nombres', 'apellidos']
    # Reglas del skill Python Seguro aplicadas: la paginación global ya está en settings.py

    def perform_destroy(self, instance):
        # Soft delete: el campo ya existe para esto pero no se usaba.
        instance.estado = False
        instance.save(update_fields=['estado'])

    @action(
        detail=False, methods=['get'],
        url_path='kiosko/buscar-cliente',
        permission_classes=[AllowAny]  # Ruta pública: el kiosko funciona sin sesión de usuario
    )
    def kiosko_buscar_cliente(self, request):
        """
        Endpoint público para el kiosko de autoatención.
        Busca un cliente por DNI: primero en la base de datos local,
        si no existe consulta la API externa de la RENIEC.
        No requiere autenticación porque el kiosko es una pantalla pública.
        """
        dni = request.query_params.get('dni', '').strip()
        if not dni or len(dni) != 8 or not dni.isdigit():
            return Response(
                {'error': 'El DNI debe ser un número de 8 dígitos.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # 1. Buscar en la BD local primero (evita cuota de API externa)
            cliente = Cliente.objects.filter(dni=dni, estado=True).first()
            if cliente:
                serializer = self.get_serializer(cliente)
                return Response({'origen': 'local', 'data': serializer.data})

            # 2. Si no existe localmente, consultar la API externa
            orchestrator = ConsultaOrchestrator()
            datos = orchestrator.consultar_dni(dni)
            return Response({'origen': 'api', 'data': datos})

        except ValueError as e:
            logger.warning(f"[Kiosko] Error de API al consultar DNI {dni}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as e:
            logger.error(f"[Kiosko] Error de conexión al consultar DNI {dni}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"[Kiosko] Error inesperado al consultar DNI {dni}: {e}", exc_info=True)
            return Response(
                {'error': 'Error interno al procesar la consulta.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(
        detail=False, methods=['post'],
        url_path='kiosko/crear-cliente',
        permission_classes=[AllowAny]  # Ruta pública: el kiosko necesita crear clientes nuevos
    )
    def kiosko_crear_cliente(self, request):
        """
        Endpoint público para que el kiosko cree un cliente nuevo cuando
        el DNI no existe en la base de datos y el usuario completa sus datos.
        """
        serializer = ClienteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cliente = serializer.save()
        logger.info(f"[Kiosko] Cliente creado desde terminal pública: {cliente.id} - DNI: {cliente.dni}")
        return Response(ClienteSerializer(cliente).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='consulta-dni')
    def consulta_dni(self, request):
        """
        Endpoint que consulta el DNI en base de datos local y, si no existe,
        utiliza el servicio orquestador para consultar APIs externas.
        """
        dni = request.data.get('dni')
        if not dni:
            return Response({'error': 'El campo DNI es obligatorio.'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            # Primero buscamos en base de datos local para ahorrar cuota de API
            cliente = Cliente.objects.filter(dni=dni).first()
            if cliente:
                serializer = self.get_serializer(cliente)
                return Response({'origen': 'local', 'data': serializer.data})
                
            # Si no existe, usamos la API externa
            orchestrator = ConsultaOrchestrator()
            datos = orchestrator.consultar_dni(dni)
            return Response({'origen': 'api', 'data': datos})
            
        except ValueError as e:
            logger.warning(f"Error de API al consultar DNI {dni}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as e:
            logger.error(f"Error de conexión al consultar DNI {dni}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error interno inesperado al consultar DNI {dni}: {e}", exc_info=True)
            return Response({'error': 'Error interno del servidor al procesar la consulta.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='consulta-ruc')
    def consulta_ruc(self, request):
        """
        Endpoint que consulta el RUC en base de datos local y, si no existe,
        utiliza el servicio orquestador para consultar APIs externas.
        """
        ruc = request.data.get('ruc')
        if not ruc:
            return Response({'error': 'El campo RUC es obligatorio.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # El RUC se guarda en el mismo campo 'dni' (numero de documento del cliente)
            cliente = Cliente.objects.filter(dni=ruc).first()
            if cliente:
                serializer = self.get_serializer(cliente)
                return Response({'origen': 'local', 'data': serializer.data})

            orchestrator = ConsultaOrchestrator()
            datos = orchestrator.consultar_ruc(ruc)
            # Normalizamos a la misma forma que consulta_dni (nombres/apellidos)
            # para que el frontend use un unico manejador de respuesta.
            return Response({'origen': 'api', 'data': {
                'dni': datos.get('ruc', ruc),
                'nombres': datos.get('razon_social', ''),
                'apellidos': '',
                'direccion': datos.get('direccion', ''),
            }})
            
        except ValueError as e:
            logger.warning(f"Error de API al consultar RUC {ruc}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as e:
            logger.error(f"Error de conexión al consultar RUC {ruc}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error interno inesperado al consultar RUC {ruc}: {e}", exc_info=True)
            return Response({'error': 'Error interno del servidor al procesar la consulta.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _resumen_vehiculos(self, cliente):
        """Vehículos asociados al cliente con un resumen de sus ingresos al
        taller. prefetch_related evita N+1: una sola query trae todas las
        órdenes de todos los vehículos del cliente."""
        vehiculos_qs = cliente.vehiculos.filter(estado=True).prefetch_related('ordenes_trabajo').order_by('placa')
        data = []
        for v in vehiculos_qs:
            ordenes = list(v.ordenes_trabajo.all())
            ultima_orden = max(ordenes, key=lambda o: o.fecha_ingreso) if ordenes else None
            data.append({
                'id': v.id,
                'placa': v.placa,
                'marca': v.marca,
                'modelo': v.modelo,
                'anio_fabricacion': v.anio_fabricacion,
                'total_ordenes': len(ordenes),
                'ultimo_ingreso': ultima_orden.fecha_ingreso if ultima_orden else None,
                'ultimo_estado': ultima_orden.get_estado_display() if ultima_orden else None,
            })
        return data

    @action(detail=True, methods=['get'], url_path='vehiculos')
    def vehiculos(self, request, pk=None):
        """Vehículos que este cliente tiene o ha tenido asociados, con
        cuántas veces ingresó cada uno al taller y su último estado."""
        cliente = self.get_object()
        vehiculos_data = self._resumen_vehiculos(cliente)
        return Response({
            'cliente': {
                'id': cliente.id,
                'tipo_documento': cliente.tipo_documento,
                'dni': cliente.dni,
                'nombres': cliente.nombres,
                'apellidos': cliente.apellidos,
                'telefono': cliente.telefono,
                'direccion': cliente.direccion,
            },
            'total_vehiculos': len(vehiculos_data),
            'vehiculos': vehiculos_data,
        })

    @action(detail=True, methods=['get'], url_path='vehiculos/pdf')
    def vehiculos_pdf(self, request, pk=None):
        """Ficha en PDF de los vehículos asociados a un cliente, para
        entregar al gerente o al propio cliente."""
        cliente = self.get_object()
        vehiculos_data = self._resumen_vehiculos(cliente)

        from apps.seguridad.pdf_utils import contexto_empresa_pdf

        context = {
            'cliente': cliente,
            'vehiculos': vehiculos_data,
            'total_vehiculos': len(vehiculos_data),
            **contexto_empresa_pdf(),
        }

        from django.template.loader import render_to_string
        from django.http import HttpResponse
        from xhtml2pdf import pisa

        html_string = render_to_string('clientes/vehiculos_pdf.html', context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="vehiculos_{cliente.dni}.pdf"'

        pisa_status = pisa.CreatePDF(html_string, dest=response)
        if pisa_status.err:
            logger.error(f"Error generando PDF de vehículos para cliente {cliente.dni}")
            return HttpResponse('Error generando PDF', status=500)
        return response


class ProveedorViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = "CONTACTOS.PROVEEDORES.VER"
    permiso_crear = "CONTACTOS.PROVEEDORES.CREAR"
    permiso_editar = "CONTACTOS.PROVEEDORES.EDITAR"
    permiso_eliminar = "CONTACTOS.PROVEEDORES.ELIMINAR"
    queryset = Proveedor.objects.filter(estado=True)
    serializer_class = ProveedorSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['numero_documento', 'nombre_o_razon_social']

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save(update_fields=['estado'])

    @action(detail=False, methods=['post'], url_path='consulta-documento')
    def consulta_documento(self, request):
        tipo_documento = request.data.get('tipo_documento')
        numero_documento = request.data.get('numero_documento')

        if not tipo_documento or not numero_documento:
            return Response({'error': 'tipo_documento y numero_documento son obligatorios.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            proveedor = Proveedor.objects.filter(numero_documento=numero_documento).first()
            if proveedor:
                serializer = self.get_serializer(proveedor)
                return Response({'origen': 'local', 'data': serializer.data})
                
            orchestrator = ConsultaOrchestrator()
            if tipo_documento == 'DNI':
                datos = orchestrator.consultar_dni(numero_documento)
                # Formatear a la estructura del frontend
                return Response({'origen': 'api', 'data': {
                    'nombre_o_razon_social': (datos.get('nombres', '') + ' ' + datos.get('apellido_paterno', '') + ' ' + datos.get('apellido_materno', '')).strip(),
                    'direccion': datos.get('direccion', '')
                }})
            elif tipo_documento == 'RUC':
                datos = orchestrator.consultar_ruc(numero_documento)
                return Response({'origen': 'api', 'data': {
                    'nombre_o_razon_social': datos.get('razon_social', ''),
                    'direccion': datos.get('direccion', '')
                }})
            else:
                return Response({'error': 'Tipo de documento no válido.'}, status=status.HTTP_400_BAD_REQUEST)
                
        except ValueError as e:
            logger.warning(f"Error de API al consultar documento {numero_documento}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as e:
            logger.error(f"Error de conexión al consultar documento {numero_documento}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error interno inesperado al consultar documento {numero_documento}: {e}", exc_info=True)
            return Response({'error': 'Error interno del servidor al procesar la consulta.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TransportistaViewSet(PermisoPorMetodoMixin, viewsets.ModelViewSet):
    permiso_ver = "CONTACTOS.TRANSPORTISTAS.VER"
    permiso_crear = "CONTACTOS.TRANSPORTISTAS.CREAR"
    permiso_editar = "CONTACTOS.TRANSPORTISTAS.EDITAR"
    permiso_eliminar = "CONTACTOS.TRANSPORTISTAS.ELIMINAR"
    queryset = Transportista.objects.filter(estado=True)
    serializer_class = TransportistaSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['numero_documento', 'nombre_o_razon_social']

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save(update_fields=['estado'])

    @action(detail=False, methods=['post'], url_path='consulta-documento')
    def consulta_documento(self, request):
        tipo_documento = request.data.get('tipo_documento')
        numero_documento = request.data.get('numero_documento')
        
        if not tipo_documento or not numero_documento:
            return Response({'error': 'tipo_documento y numero_documento son obligatorios.'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            transportista = Transportista.objects.filter(numero_documento=numero_documento).first()
            if transportista:
                serializer = self.get_serializer(transportista)
                return Response({'origen': 'local', 'data': serializer.data})
                
            orchestrator = ConsultaOrchestrator()
            if tipo_documento == 'DNI':
                datos = orchestrator.consultar_dni(numero_documento)
                return Response({'origen': 'api', 'data': {
                    'nombre_o_razon_social': (datos.get('nombres', '') + ' ' + datos.get('apellido_paterno', '') + ' ' + datos.get('apellido_materno', '')).strip(),
                    'direccion': datos.get('direccion', '')
                }})
            elif tipo_documento == 'RUC':
                datos = orchestrator.consultar_ruc(numero_documento)
                return Response({'origen': 'api', 'data': {
                    'nombre_o_razon_social': datos.get('razon_social', ''),
                    'direccion': datos.get('direccion', '')
                }})
            else:
                return Response({'error': 'Tipo de documento no válido.'}, status=status.HTTP_400_BAD_REQUEST)
                
        except ValueError as e:
            logger.warning(f"Error de API al consultar documento {numero_documento}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as e:
            logger.error(f"Error de conexión al consultar documento {numero_documento}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error interno inesperado al consultar documento {numero_documento}: {e}", exc_info=True)
            return Response({'error': 'Error interno del servidor al procesar la consulta.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

