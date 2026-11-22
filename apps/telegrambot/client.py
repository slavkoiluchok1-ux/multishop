"""Telegram Bot API client."""
import requests


class TelegramAPIError(Exception):
    def __init__(
        self,
        error_code: int | None,
        description: str,
        retry_after: int | None = None,
    ):
        super().__init__(description)
        self.error_code = error_code
        self.retry_after = retry_after


class TelegramAPIClient:
    def __init__(self, token: str):
        if not token:
            raise ValueError("Telegram bot token is required.")
        self._token = token

    def request(self, method: str, payload: dict):
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            response = requests.post(url, json=payload, timeout=35)
        except requests.RequestException as exc:
            raise TelegramAPIError(None, "Telegram API is unreachable.") from exc

        try:
            result = response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise TelegramAPIError(None, "Telegram API returned invalid data.") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            code = result.get("error_code") if isinstance(result, dict) else response.status_code
            parameters = result.get("parameters", {}) if isinstance(result, dict) else {}
            retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
            description = (
                str(result.get("description") or "Telegram API rejected the request.")
                if isinstance(result, dict)
                else "Telegram API returned invalid data."
            )
            raise TelegramAPIError(
                code if isinstance(code, int) else None,
                description[:255],
                retry_after if isinstance(retry_after, int) else None,
            )
        return result.get("result")

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ):
        payload = {"chat_id": chat_id, "text": text[:4000]}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        return self.request("sendMessage", payload)
