from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.db.models import Q
from .models import Categoria, MarcaRepuesto, Repuesto
from .serializers import CategoriaSerializer, MarcaRepuestoSerializer, RepuestoSerializer

class CategoriaViewSet(viewsets.ModelViewSet):
    queryset = Categoria.objects.filter(estado=True).order_by('-id')
    serializer_class = CategoriaSerializer
    permission_classes = [IsAuthenticated]

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save()


class MarcaRepuestoViewSet(viewsets.ModelViewSet):
    queryset = MarcaRepuesto.objects.filter(estado=True).order_by('-id')
    serializer_class = MarcaRepuestoSerializer
    permission_classes = [IsAuthenticated]

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save()


class RepuestoViewSet(viewsets.ModelViewSet):
    # Usamos select_related y prefetch_related para evitar N+1
    queryset = Repuesto.objects.filter(estado=True).select_related('categoria', 'marca').prefetch_related('aplicaciones').order_by('-id')
    serializer_class = RepuestoSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save()

    @transaction.atomic
    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        instance.estado = False
        instance.save()

    @action(detail=False, methods=['get'])
    def compatibles(self, request):
        """
        Endpoint dinamico para obtener repuestos compatibles con un vehiculo
        Query Params esperados: marca, modelo, motor
        """
        marca = request.query_params.get('marca', None)
        modelo = request.query_params.get('modelo', None)
        motor = request.query_params.get('motor', None)

        if not marca:
            return Response({'error': 'La marca del vehiculo es requerida'}, status=400)

        # Iniciar query para buscar en las aplicaciones
        # Se requiere coincidencia en marca. Modelo y motor son opcionales en la BD, 
        # pero si existen, deben coincidir.
        
        # Obtenemos repuestos que tengan al menos una aplicación que coincida
        query = Q(aplicaciones__marca_vehiculo__iexact=marca)
        
        if modelo:
            # Si el vehiculo tiene modelo, la aplicación debe no tener modelo especificado (aplica a todos los modelos de la marca)
            # O debe tener el mismo modelo
            query &= (Q(aplicaciones__modelo_vehiculo__isnull=True) | Q(aplicaciones__modelo_vehiculo__iexact=modelo))
            
        if motor:
            query &= (Q(aplicaciones__motor__isnull=True) | Q(aplicaciones__motor__iexact=motor))

        repuestos = self.get_queryset().filter(query).distinct()
        
        # Paginacion manual si es requerida, o delegarla al paginador por defecto
        page = self.paginate_queryset(repuestos)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(repuestos, many=True)
        return Response(serializer.data)
