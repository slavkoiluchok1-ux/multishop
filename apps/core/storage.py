"""Сховище для приватних файлів (media/private). Не має публічного URL."""
from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateMediaStorage(FileSystemStorage):
    """Файли, які віддаються лише через API з перевіркою прав."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        kwargs.setdefault("base_url", None)
        super().__init__(*args, **kwargs)


private_storage = PrivateMediaStorage()


def validate_upload(value):
    """Перевірка розміру й розширення (білий список розділу 9 PROJECT_STRUCTURE)."""
    from django.core.exceptions import ValidationError

    allowed = (
        settings.ALLOWED_IMAGE_EXTENSIONS
        + settings.ALLOWED_VIDEO_EXTENSIONS
        + settings.ALLOWED_DOCUMENT_EXTENSIONS
        + settings.ALLOWED_ARCHIVE_EXTENSIONS
        + settings.ALLOWED_DIGITAL_EXTENSIONS
    )
    name = getattr(value, "name", "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in allowed:
        raise ValidationError(f"Розширення .{ext} заборонене.")
    size = getattr(value, "size", 0) or 0
    if size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"Файл завеликий. Максимум {settings.MAX_UPLOAD_MB} МБ.")


def validate_image(value):
    """Додаткова перевірка зображень через Pillow (розділ 9 PROJECT_STRUCTURE)."""
    from django.core.exceptions import ValidationError
    from PIL import Image, UnidentifiedImageError

    validate_upload(value)
    try:
        value.seek(0)
        Image.open(value).verify()
        value.seek(0)
    except (UnidentifiedImageError, OSError):
        raise ValidationError("Файл не є коректним зображенням.")
