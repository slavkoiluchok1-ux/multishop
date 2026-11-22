import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...bot import TelegramBot
from ...client import TelegramAPIClient, TelegramAPIError
from ...models import TelegramBotState
from apps.notifications.signals import notify_expired_bans


class Command(BaseCommand):
    help = "Run the Telegram bot using long polling."

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            self.stdout.write(
                self.style.WARNING(
                    "Telegram-бот вимкнено: TELEGRAM_BOT_TOKEN не налаштовано."
                )
            )
            return
        if not settings.TELEGRAM_BOT_USERNAME:
            raise CommandError("Set TELEGRAM_BOT_USERNAME in the environment.")

        client = TelegramAPIClient(token)
        bot = TelegramBot(client)
        try:
            bot_info = client.request("getMe", {})
            client.request("deleteWebhook", {"drop_pending_updates": False})
        except TelegramAPIError as exc:
            error_name = str(exc.error_code) if exc.error_code else "network"
            raise CommandError(f"Telegram bot initialization failed ({error_name}).") from exc
        actual_username = str((bot_info or {}).get("username") or "").lstrip("@")
        if actual_username.casefold() != settings.TELEGRAM_BOT_USERNAME.casefold():
            raise CommandError("TELEGRAM_BOT_USERNAME does not match the configured bot token.")

        state, _ = TelegramBotState.objects.get_or_create(pk=1)
        offset = state.last_update_id + 1 if state.last_update_id else None
        last_ban_check = 0.0
        self.stdout.write("Telegram bot polling started.")

        while True:
            try:
                if time.monotonic() - last_ban_check >= 60:
                    notify_expired_bans()
                    last_ban_check = time.monotonic()
                bot.send_pending_notifications()
                request_data = {
                    "timeout": 2,
                    "allowed_updates": ["message", "callback_query"],
                }
                if offset is not None:
                    request_data["offset"] = offset
                updates = client.request("getUpdates", request_data)
            except TelegramAPIError as exc:
                error_name = str(exc.error_code) if exc.error_code else "network"
                self.stderr.write(
                    self.style.WARNING(
                        f"Telegram API request failed ({error_name}: {exc}); retrying."
                    )
                )
                time.sleep(exc.retry_after or 3)
                continue

            if not isinstance(updates, list):
                raise CommandError("Telegram API returned an invalid updates list.")
            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if not isinstance(update_id, int):
                    continue
                try:
                    bot.handle_update(update)
                except TelegramAPIError as exc:
                    error_name = str(exc.error_code) if exc.error_code else "network"
                    self.stderr.write(
                        self.style.WARNING(
                            f"Telegram update failed ({error_name}: {exc}); skipping the update."
                        )
                    )
                offset = update_id + 1
                state.last_update_id = update_id
                state.save(update_fields=["last_update_id", "updated_at"])
