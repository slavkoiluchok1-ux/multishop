"""Ядро: віддавання фронтенду (frontend/) із того ж домену."""
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse

FRONTEND_DIR = Path(settings.BASE_DIR) / "frontend"
PAGES_DIR = FRONTEND_DIR / "pages"


def health(request):
    """Публічна перевірка готовності для хостингу."""
    return JsonResponse({"status": "ok"})


def _serve(path):
    if not path.is_file():
        raise Http404("Сторінку не знайдено")
    if settings.DEBUG:
        content = path.read_text(encoding="utf-8")
        content = content.replace(
            "</head>",
            "<script>window.MS_DEBUG = true;</script>\n</head>",
            1,
        )
        return HttpResponse(content, content_type="text/html; charset=utf-8")
    return FileResponse(open(path, "rb"), content_type="text/html; charset=utf-8")


def home(request):
    """Головна: frontend/index.html."""
    return _serve(FRONTEND_DIR / "index.html")


def product_hidden(request, token):
    """Прихований товар за посиланням: та сама сторінка товару (токен читає JS)."""
    return _serve(PAGES_DIR / "product.html")


def page(request, name):
    """Сторінка фронтенду: /pages/<name> (розширення .html додається саме).

    Підтримує вкладені сторінки, наприклад pages/admin/bans.html.
    """
    # Захист від виходу за межі папки
    if ".." in name or name.startswith("/") or name.startswith("\\"):
        raise Http404("Сторінку не знайдено")
    if not name.endswith(".html"):
        name = f"{name}.html"
    return _serve(PAGES_DIR / name)
