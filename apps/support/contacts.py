"""Форматування контактів звернень для API."""
import re
from urllib.parse import quote, urlsplit

from .models import SupportTicket


def _social_link(value: str, service: str) -> str | None:
    value = value.strip()
    if not value:
        return None

    if service == "facebook":
        try:
            parsed = urlsplit(value if "://" in value else "https://" + value)
        except ValueError:
            return None
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and re.fullmatch(r"(?:[a-z0-9-]+\.)*facebook\.com", hostname)
        ):
            return parsed.geturl()
        if "://" not in value and re.fullmatch(r"@?[A-Za-z0-9.]+", value):
            return "https://facebook.com/" + quote(value.lstrip("@"), safe=".")
        return None

    username = value.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_.]+", username):
        return None
    domain = "t.me" if service == "telegram" else "instagram.com"
    return f"https://{domain}/{quote(username, safe='._')}"


def ticket_contact_payload(ticket: SupportTicket) -> list[dict[str, str]]:
    contacts: list[dict[str, str]] = []
    if ticket.contact_email:
        contacts.append({"value": ticket.contact_email})

    fields = [
        ("contact_phone", ""),
        ("contact_telegram", "Telegram"),
        ("contact_viber", "Viber"),
        ("contact_whatsapp", "WhatsApp"),
        ("contact_instagram", "Instagram"),
        ("contact_facebook", "Facebook"),
    ]
    for field, label in fields:
        value = getattr(ticket, field).strip()
        if not value:
            continue
        contact = {"value": value}
        if label:
            contact["label"] = label
        if field in ("contact_telegram", "contact_instagram", "contact_facebook"):
            service = "facebook" if field == "contact_facebook" else field.removeprefix("contact_")
            url = _social_link(value, service)
            if url:
                contact["url"] = url
        contacts.append(contact)

    if ticket.contact_info:
        contacts.append({"label": "Інші", "value": ticket.contact_info.strip()})
    return contacts
