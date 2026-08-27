"""
urls.py — Rutas del módulo de Seguridad.
Prefijo: /api/seguridad/ (definido en taller_core/urls.py)
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register(r'departamentos', views.DepartamentoViewSet, basename='departamentos')
router.register(r'provincias', views.ProvinciaViewSet, basename='provincias')
router.register(r'distritos', views.DistritoViewSet, basename='distritos')

urlpatterns = [
    path("", include(router.urls)),

    # Autenticación
    path("login/", views.LoginView.as_view(), name="seguridad-login"),
    path("logout/", views.LogoutView.as_view(), name="seguridad-logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="seguridad-token-refresh"),

    # Usuarios y Perfil
    path("usuarios/", views.UsuarioListCreateView.as_view(), name="seguridad-usuarios"),
    path("usuarios/<uuid:pk>/", views.UsuarioDetalleView.as_view(), name="seguridad-usuario-detalle"),
    path("mi-perfil/", views.MiPerfilView.as_view(), name="seguridad-mi-perfil"),

    # Roles
    path("roles/", views.RolListCreateView.as_view(), name="seguridad-roles"),
    path("roles/<int:pk>/", views.RolDetalleView.as_view(), name="seguridad-rol-detalle"),
    path("roles/<int:pk>/permisos/", views.AsignarPermisosRolView.as_view(), name="seguridad-rol-permisos"),

    # Catálogos
    path("permisos/", views.PermisoListView.as_view(), name="seguridad-permisos"),
    path("modulos/", views.ModuloListView.as_view(), name="seguridad-modulos"),

    # Empresa (Configuración Global)
    path("empresa/", views.EmpresaView.as_view(), name="seguridad-empresa"),
]
