import logging

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import Vehiculo
from .services import ConsultaVehicularService


logger = logging.getLogger(__name__)
DATOS_PUBLICOS = ('placa', 'marca', 'modelo', 'anio_fabricacion', 'tipo_combustible')


class ConsultaPlacaPublicaView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'placa_publica'

    def get(self, request):
        placa = request.query_params.get('placa', '').strip().replace('-', '').replace(' ', '').upper()
        if not placa.isalnum() or not 6 <= len(placa) <= 10:
            return Response({'error': 'Ingresa una placa valida.'}, status=400)
        try:
            datos = Vehiculo.objects.filter(placa=placa, estado=True).values(*DATOS_PUBLICOS).first()
            origen = 'local'
            if datos is None:
                origen = 'api'
                datos = ConsultaVehicularService().consultar_placa(placa)
            return Response({'origen': origen, 'data': {campo: datos.get(campo) for campo in DATOS_PUBLICOS}})
        except ValueError:
            return Response({'error': 'No se encontraron datos para esa placa.'}, status=400)
        except ConnectionError:
            return Response({'error': 'La consulta de placas no esta disponible por el momento.'}, status=503)
        except Exception:
            logger.exception('Error en consulta publica de placa')
            return Response({'error': 'No se pudo consultar la placa.'}, status=500)
