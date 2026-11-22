"""Акаунти: користувач, профіль, права адмінів, верифікація, бани, журнал."""
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.storage import private_storage, validate_image


class UserManager(DjangoUserManager):
    """Менеджер користувачів: суперкористувач завжди стає головним адміном."""

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", CustomUser.ROLE_ADMIN_MAIN)
        extra_fields["is_staff"] = True
        extra_fields["is_superuser"] = True
        return super().create_superuser(username, email, password, **extra_fields)


class CustomUser(AbstractUser):
    """Користувач сайту. Нік і email унікальні, вхід за ніком або email."""

    id: int
    profile: "UserProfile"
    username = models.CharField(
        "Нік",
        max_length=20,
        unique=True,
        validators=[UnicodeUsernameValidator()],
        error_messages={"unique": "Такий нік уже зайнятий."},
    )
    display_name = models.CharField("Публічний нік", max_length=20, blank=True)

    ROLE_USER = "user"
    ROLE_ADMIN_2 = "admin_2"
    ROLE_ADMIN_1 = "admin_1"
    ROLE_ADMIN_MAIN = "admin_main"
    ROLE_CHOICES = [
        (ROLE_USER, "Користувач"),
        (ROLE_ADMIN_2, "Адмін 2-го рівня"),
        (ROLE_ADMIN_1, "Адмін 1-го рівня"),
        (ROLE_ADMIN_MAIN, "Головний адмін"),
    ]

    email = models.EmailField("Email", unique=True)
    avatar = models.ImageField("Аватар", upload_to="avatars/", blank=True, null=True)
    role = models.CharField("Роль", max_length=20, choices=ROLE_CHOICES, default=ROLE_USER)
    must_change_password = models.BooleanField("Змінити пароль при вході", default=False)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    objects = UserManager()

    class Meta(AbstractUser.Meta):  # pyright: ignore[reportAttributeAccessIssue]
        verbose_name = "Користувач"
        verbose_name_plural = "Користувачі"
        constraints = [
            # Email унікальний без урахування регістру
            models.UniqueConstraint(Lower("email"), name="uniq_email_case_insensitive"),
        ]

    def __str__(self):
        return self.username

    def save(self, *args, **kwargs):
        """Прапорці Django Admin завжди відповідають ролі: True лише в головного."""
        if not str(self.display_name or "").strip():
            self.display_name = self.username
        is_main = self.role == self.ROLE_ADMIN_MAIN
        self.is_staff = is_main
        self.is_superuser = is_main
        # Якщо save() викликають із update_fields, прапорці треба додати туди ж,
        # інакше зміна ролі не оновить їх у базі.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"is_staff", "is_superuser"}
        super().save(*args, **kwargs)

    # --- Ролі ---
    @property
    def is_admin_main(self):
        return self.role == self.ROLE_ADMIN_MAIN

    @property
    def is_admin_1(self):
        return self.role == self.ROLE_ADMIN_1

    @property
    def is_admin_2(self):
        return self.role == self.ROLE_ADMIN_2

    @property
    def is_staff_admin(self):
        return self.role in (self.ROLE_ADMIN_2, self.ROLE_ADMIN_1, self.ROLE_ADMIN_MAIN)

    def has_right(self, right):
        """Право адміна: home, products, support, complaints, bans.

        Права з AdminRight діють лише для адміна 2-го рівня; у головного й
        1-го рівня вони всі, у звичайного користувача — жодного.
        """
        if self.role in (self.ROLE_ADMIN_MAIN, self.ROLE_ADMIN_1):
            return True
        if self.role == self.ROLE_ADMIN_2:
            return self.admin_rights.filter(right=right).exists()  # pyright: ignore[reportAttributeAccessIssue]
        return False

    def is_banned(self):
        """Чи діє хоч один активний бан (не знятий і не прострочений)."""
        return Ban.objects.active().filter(user=self).exists()

    @classmethod
    def find_by_login(cls, login):
        """Пошук за ніком або email (без урахування регістру)."""
        if not login:
            return None
        return (
            cls.objects.filter(models.Q(username__iexact=login) | models.Q(email__iexact=login))
            .distinct()
            .first()
        )


class AdminRight(models.Model):
    """Право адміна 2-го рівня."""

    RIGHTS = [
        ("home", "Головна сторінка"),
        ("products", "Товари"),
        ("support", "Підтримка"),
        ("complaints", "Скарги"),
        ("bans", "Бани"),
        ("wallet", "Гаманець"),
        ("orders", "Замовлення"),
    ]

    user = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="admin_rights"
    )
    right = models.CharField("Право", max_length=20, choices=RIGHTS)

    class Meta:
        unique_together = ("user", "right")
        verbose_name = "Право адміна"
        verbose_name_plural = "Права адмінів"

    def __str__(self):
        return f"{self.user} — {self.get_right_display()}"  # pyright: ignore[reportAttributeAccessIssue]


class UserProfile(models.Model):
    """Профіль користувача. Створюється сигналом разом із користувачем."""

    id: int
    user_id: int

    user = models.OneToOneField(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="profile"
    )
    phone = models.CharField("Телефон", max_length=32, blank=True)
    telegram = models.CharField("Telegram", max_length=64, blank=True)
    instagram = models.CharField("Instagram", max_length=64, blank=True)
    viber = models.CharField("Viber", max_length=32, blank=True)
    facebook = models.CharField("Facebook", max_length=128, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=32, blank=True)
    delivery_country = models.CharField("Країна", max_length=64, blank=True)
    delivery_city = models.CharField("Місто", max_length=64, blank=True)
    delivery_street = models.CharField("Вулиця й будинок", max_length=255, blank=True)
    delivery_postcode = models.CharField("Поштовий індекс", max_length=16, blank=True)
    recipient_name = models.CharField("Отримувач", max_length=150, blank=True)
    autofill_delivery = models.BooleanField("Підставляти автоматично", default=True)
    false_complaints_count = models.PositiveIntegerField("Хибних скарг", default=0)
    complaints_blocked_until = models.DateTimeField("Скарги заблоковані до", null=True, blank=True)
    complaints_block_count = models.PositiveIntegerField("Блокувань скарг", default=0)

    class Meta:
        verbose_name = "Профіль"
        verbose_name_plural = "Профілі"

    def __str__(self):
        return f"Профіль {self.user}"


class SellerVerification(models.Model):
    """Одноразова верифікація продавця. Після збереження змінити не можна."""

    id: int
    user_id: int

    user = models.OneToOneField(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="verification"
    )
    photo = models.ImageField(
        "Фото з документом",
        upload_to="verification/",
        storage=private_storage,
        validators=[validate_image],
    )
    age = models.PositiveSmallIntegerField("Вік")
    is_locked = models.BooleanField("Заблоковано від змін", default=True)
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        verbose_name = "Верифікація продавця"
        verbose_name_plural = "Верифікації продавців"

    def __str__(self):
        return f"Верифікація {self.user}"


class BanManager(models.Manager["Ban"]):
    """Активні бани: не знято вручну (lifted_at is NULL) і ще не завершились."""

    def active(self) -> models.QuerySet["Ban"]:
        now = timezone.now()
        return self.filter(
            models.Q(lifted_at__isnull=True)
            & (models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now))
        )


class Ban(models.Model):
    """Бан користувача."""

    id: int
    user_id: int
    issued_by_id: int | None
    complaint_id: int | None

    SOURCE_CHOICES = [
        ("admin", "Адмін"),
        ("complaint", "Скарга"),
        ("verification", "Верифікація"),
    ]
    ISSUER_ROLE_CHOICES = [
        ("admin_2", "Адмін 2-го рівня"),
        ("admin_1", "Адмін 1-го рівня"),
        ("admin_main", "Головний адмін"),
        ("system", "Система"),
    ]

    user = models.ForeignKey("accounts.CustomUser", on_delete=models.CASCADE, related_name="bans")
    issued_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_bans",
    )
    issued_by_role = models.CharField(
        "Рівень того, хто забанив",
        max_length=20,
        choices=ISSUER_ROLE_CHOICES,
        default="system",
    )
    reason = models.TextField("Причина", blank=True)
    source = models.CharField("Джерело", max_length=20, choices=SOURCE_CHOICES, default="admin")
    complaint = models.ForeignKey(
        "complaints.Complaint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bans",
    )
    starts_at = models.DateTimeField("Початок", auto_now_add=True)
    ends_at = models.DateTimeField("Кінець", null=True, blank=True)  # порожнє = назавжди
    lifted_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lifted_bans",
    )
    lifted_at = models.DateTimeField("Знято", null=True, blank=True)
    expired_notified_at = models.DateTimeField("Сповіщено про завершення", null=True, blank=True)

    objects = BanManager()

    class Meta:
        ordering = ["-starts_at"]
        verbose_name = "Бан"
        verbose_name_plural = "Бани"

    def __str__(self):
        return f"Бан {self.user} ({self.get_source_display()})"  # pyright: ignore[reportAttributeAccessIssue]

    @property
    def issued_by_role_display(self) -> str:
        return dict(self.ISSUER_ROLE_CHOICES).get(self.issued_by_role, "")

    def save(self, *args, **kwargs):
        issuer = self.issued_by
        if issuer is not None:
            self.issued_by_role = issuer.role
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"issued_by_role"}
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        """Те саме правило, що й Ban.objects.active(): знято або прострочено — не діє."""
        if self.lifted_at is not None:
            return False
        return self.ends_at is None or self.ends_at > timezone.now()


class AdminLog(models.Model):
    """Журнал дій адмінів."""

    actor = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_logs",
    )
    action = models.CharField("Дія", max_length=255)
    target = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_log_targets",
    )
    created_at = models.DateTimeField("Створено", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["actor", "created_at"], name="adminlog_actor_created_idx"),
        ]
        verbose_name = "Запис журналу"
        verbose_name_plural = "Журнал адмінів"

    def __str__(self):
        return f"{self.actor}: {self.action}"
