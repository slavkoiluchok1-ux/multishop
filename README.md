# MultiShop

## Telegram-бот

1. Створіть бота через [@BotFather](https://t.me/BotFather) командою `/newbot` і збережіть токен лише у власному `.env`.
2. Заповніть `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` (без `@`) та `SITE_URL`.
   Локальні команди `manage.py` завантажують значення з кореневого `.env` і використовують dev-налаштування, якщо профіль явно не заданий у середовищі процесу. Docker Compose передає production-налаштування й секрети з того самого файла.
3. Після `python manage.py migrate` запустіть окремий процес бота:

   ```powershell
   .\.venv\Scripts\python.exe manage.py run_telegram_bot
   ```

4. Для Docker задайте ті самі змінні у кореневому `.env` і запустіть профіль:

   ```text
   docker compose --env-file .env -f docker/docker-compose.yml --profile bot up -d --build telegram_bot
   ```

Без `TELEGRAM_BOT_TOKEN` сайт працює без Telegram-функцій, а команда бота повідомляє, що інтеграцію вимкнено.
Якщо токен потрапив у вихідний код або чат, відкличте його в BotFather і створіть новий. Не записуйте справжній токен у `.env.example`.
