"""
Caché de datos de seguridad que se piden todo el tiempo y casi nunca cambian:
permisos efectivos de un usuario, menú y datos de la empresa.

Invalidación: cada clave incluye un número de "versión". Cuando cambia algo relevante
(rol, permiso, módulo, empresa...) se sube la versión y todas las claves anteriores quedan
inutilizadas al instante (ver signals.py y las llamadas explícitas a `invalidar()`).

Los permisos temporales vencen sin ninguna escritura en la base, por eso el tiempo de vida
es corto (TTL_SEGUNDOS). La autorización real de cada petición (TienePermiso) NO usa este caché:
siempre consulta la base; esto solo acelera el menú y la lista de permisos del frontend.

Nota: LocMemCache es por proceso. Con un solo proceso (Waitress con hilos) es suficiente; si algún
día se usan varios procesos, el cambio tardaría como máximo TTL_SEGUNDOS en verse en los demás.
"""
from django.core.cache import cache

VERSION_KEY = 'seguridad:version'
TTL_SEGUNDOS = 120


def _version():
    version = cache.get(VERSION_KEY)
    if version is None:
        cache.add(VERSION_KEY, 1, None)  # sin vencimiento
        version = cache.get(VERSION_KEY) or 1
    return version


def clave(nombre, *partes):
    return ':'.join(['seg', str(_version()), nombre, *map(str, partes)])


def invalidar():
    """Descarta todo lo cacheado de seguridad (permisos, menú, empresa)."""
    try:
        cache.incr(VERSION_KEY)
    except ValueError:
        cache.set(VERSION_KEY, 2, None)
