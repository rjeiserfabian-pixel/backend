from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Vehiculo
from .serializers import VehiculoSerializer

class VehiculoViewSet(viewsets.ModelViewSet):
    queryset = Vehiculo.objects.filter(estado=True).order_by('-id')
    serializer_class = VehiculoSerializer
    permission_classes = [IsAuthenticated]

    def perform_destroy(self, instance):
        # Soft delete
        instance.estado = False
        instance.save()
