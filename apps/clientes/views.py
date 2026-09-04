from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import filters
from .models import Cliente, Proveedor, Transportista
from .serializers import ClienteSerializer, ProveedorSerializer, TransportistaSerializer
from .services import ConsultaOrchestrator
import logging

logger = logging.getLogger(__name__)

class ClienteViewSet(viewsets.ModelViewSet):
    queryset = Cliente.objects.all()
    serializer_class = ClienteSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['dni', 'nombres', 'apellidos']
    # Reglas del skill Python Seguro aplicadas: la paginación global ya está en settings.py
    
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
            cliente = Cliente.objects.filter(ruc=ruc).first()
            if cliente:
                serializer = self.get_serializer(cliente)
                return Response({'origen': 'local', 'data': serializer.data})
                
            orchestrator = ConsultaOrchestrator()
            datos = orchestrator.consultar_ruc(ruc)
            return Response({'origen': 'api', 'data': datos})
            
        except ValueError as e:
            logger.warning(f"Error de API al consultar RUC {ruc}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ConnectionError as e:
            logger.error(f"Error de conexión al consultar RUC {ruc}: {e}")
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error interno inesperado al consultar RUC {ruc}: {e}", exc_info=True)
            return Response({'error': 'Error interno del servidor al procesar la consulta.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProveedorViewSet(viewsets.ModelViewSet):
    queryset = Proveedor.objects.all()
    serializer_class = ProveedorSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['numero_documento', 'nombre_o_razon_social']

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


class TransportistaViewSet(viewsets.ModelViewSet):
    queryset = Transportista.objects.all()
    serializer_class = TransportistaSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['numero_documento', 'nombre_o_razon_social']

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

