import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("O endereço de e-mail é obrigatório.")
        email = self.normalize_email(email)
        extra_fields.setdefault("role", User.Role.LEITOR)
        extra_fields.setdefault("is_active", True)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superusuário deve ter is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusuário deve ter is_superuser=True.")

        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrador"
        ANALISTA = "analista", "Analista"
        LEITOR = "leitor", "Leitor"

    email = models.EmailField("E-mail", unique=True, max_length=255)
    full_name = models.CharField("Nome Completo", max_length=150, blank=True)
    role = models.CharField("Papel", max_length=20, choices=Role.choices, default=Role.LEITOR)

    # Proteção de objeto / IDOR: Usuário só vê grupos expressamente atribuídos a ele (Admin vê todos)
    assigned_groups = models.ManyToManyField(
        "groups.Group",
        related_name="assigned_users",
        blank=True,
        verbose_name="Grupos Atribuídos",
    )

    is_active = models.BooleanField("Ativo", default=True)
    is_staff = models.BooleanField("Equipe", default=False)
    date_joined = models.DateTimeField("Data de Criação", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"
        ordering = ["-date_joined"]

    def __str__(self):
        return self.email

    @property
    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_analista(self) -> bool:
        return self.role in (self.Role.ADMIN, self.Role.ANALISTA) or self.is_superuser

    @property
    def can_export(self) -> bool:
        # Apenas admin e analista podem exportar dados
        return self.is_analista

    @property
    def can_reveal_phone(self) -> bool:
        # Apenas admin e analista podem revelar telefones mascarados
        return self.is_analista

    def can_view_group(self, group) -> bool:
        if self.is_admin:
            return True
        return self.assigned_groups.filter(pk=group.pk).exists()


class UserInvitation(models.Model):
    """
    Convites de uso único com expiração para novos usuários.
    Sem cadastro público aberto.
    """

    email = models.EmailField("E-mail Convidado", max_length=255)
    role = models.CharField(
        "Papel", max_length=20, choices=User.Role.choices, default=User.Role.LEITOR
    )
    token = models.CharField("Token Único", max_length=64, unique=True, db_index=True)
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="invitations_sent",
        verbose_name="Convidado Por",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField("Expira Em")
    used_at = models.DateTimeField("Usado Em", null=True, blank=True)

    class Meta:
        verbose_name = "Convite de Usuário"
        verbose_name_plural = "Convites de Usuários"
        ordering = ["-created_at"]

    @classmethod
    def create_invitation(cls, email: str, role: str, invited_by: User, valid_days: int = 3):
        token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(days=valid_days)
        return cls.objects.create(
            email=email,
            role=role,
            token=token,
            invited_by=invited_by,
            expires_at=expires_at,
        )

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and timezone.now() <= self.expires_at


class RecoveryCode(models.Model):
    """
    Códigos de recuperação de backup para 2FA armazenados com hash SHA-256.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=64, db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def generate_codes(cls, user: User, count: int = 8) -> list[str]:
        # Invalida códigos antigos
        cls.objects.filter(user=user, used_at__isnull=True).delete()
        raw_codes = []
        for _ in range(count):
            raw_code = f"{secrets.token_hex(4)}-{secrets.token_hex(4)}"
            code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
            cls.objects.create(user=user, code_hash=code_hash)
            raw_codes.append(raw_code)
        return raw_codes

    @classmethod
    def verify_and_use_code(cls, user: User, raw_code: str) -> bool:
        code_hash = hashlib.sha256(raw_code.strip().encode("utf-8")).hexdigest()
        code_obj = cls.objects.filter(user=user, code_hash=code_hash, used_at__isnull=True).first()
        if code_obj:
            code_obj.used_at = timezone.now()
            code_obj.save(update_fields=["used_at"])
            return True
        return False
