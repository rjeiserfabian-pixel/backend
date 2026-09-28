class FacturacionError(ValueError):
    """
    Error de validación de negocio del módulo de facturación electrónica
    (datos faltantes, estado inválido para la operación pedida, etc.).
    Las vistas lo capturan y lo devuelven como HTTP 400 con el mensaje tal cual.
    """


class ApiSunatError(Exception):
    """
    Error de comunicación (red, timeout, respuesta no interpretable) con la
    API SUNAT propia del taller. Distinto de un rechazo/observación de SUNAT,
    que es una respuesta HTTP válida indicando que el documento no fue aceptado.
    """
