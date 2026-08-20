"""
managers.py — Manager custom para el modelo Usuario.
Reemplaza al UserManager por defecto de Django para que funcione
con nuestro modelo AbstractBaseUser (username en lugar de email).
"""
import logging
from django.contrib.auth.models import BaseUserManager

logger = logging.getLogger(__name__)


class UsuarioManager(BaseUserManager):
    def create_user(self, username, email, nombres, apellidos, password=None, **extra_fields):
        if not username:
            raise ValueError("El username es obligatorio.")
        if not email:
            raise ValueError("El email es obligatorio.")

        email = self.normalize_email(email)
        user = self.model(
            username=username,
            email=email,
            nombres=nombres,
            apellidos=apellidos,
            **extra_fields,
        )
        # Usa el hashing seguro del framework (PBKDF2) — nunca texto plano
        user.set_password(password)
        user.save(using=self._db)
        logger.info("Usuario creado: %s <%s>", username, email)
        return user

    def create_superuser(self, username, email, nombres, apellidos, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("estado", "activo")

        if not extra_fields.get("is_staff"):
            raise ValueError("El superusuario debe tener is_staff=True.")
        if not extra_fields.get("is_superuser"):
            raise ValueError("El superusuario debe tener is_superuser=True.")

        return self.create_user(username, email, nombres, apellidos, password, **extra_fields)
