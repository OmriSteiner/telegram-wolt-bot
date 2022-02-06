FROM python:3

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/telegram-wolt-bot/ /telegram-wolt-bot

ENTRYPOINT [ "python", "/telegram-wolt-bot/bot.py", "/telegram-wolt-bot/token.json", "-i", "postgresql"]