# Skin Deals — поиск скинов CS2 / Dota 2 для перепродажи

Бот сравнивает цены на Lis-Skins, CS.Trade, Market.CSGO и Skinport, проверяет их по реальным продажам
и присылает в Telegram сделки: где купить дешевле и где продать дороже (с учётом комиссии).

## Как работает
GitHub Actions сам запускает бота примерно раз в 15 минут (`.github/workflows/bot.yml`).
Токен бота хранится в секрете репозитория `TELEGRAM_TOKEN`.

## Команды в Telegram
`/scan` · `/top 30` · `/settings` · `/set max_price 50` · `/set min_profit 15` · `/pause` · `/resume`
На GitHub команды выполняются при следующем запуске (до ~15 минут).

## Запуск на своём компьютере
```
pip install -r requirements.txt
python skins.py --game cs2          # разовый поиск, таблица + CSV
python bot.py                       # бот (нужен файл .env с TELEGRAM_TOKEN=...)
python test_bot.py                  # проверка
```
