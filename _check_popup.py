import django, os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from django.test import Client
from apps.catalog.models import Category

client = Client()

acc = Category.objects.get(name="Аксесуари", parent__name="Одяг, взуття, аксесуари")
resp = client.get(f"/api/catalog/categories/{acc.id}/popup")
data = resp.json()
print("status:", resp.status_code)
print("keys:", list(data.keys()))
print("raw children:", data.get("children"))
print("accessory attrs:", [a["name"] for a in data["attributes"]])
print(
    "active db children:",
    list(
        Category.objects.filter(parent=acc, is_active=True).values_list(
            "name", "is_active"
        )
    ),
)

watch = Category.objects.get(parent=acc, name="Годинники")
wdata = client.get(f"/api/catalog/categories/{watch.id}/popup").json()
print("watch attrs:", [a["name"] for a in wdata["attributes"]])
print("watch children:", wdata.get("children"))
