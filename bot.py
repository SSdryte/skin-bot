"""Telegram-бот: каждые N минут сканирует площадки и присылает новые выгодные сделки.

1. Создайте бота у @BotFather.
2. python bot.py (или start_bot.bat) — при первом запуске бот попросит вставить токен и сохранит его в .env
3. Напишите боту /start — он запомнит вас как владельца и будет слать сделки только вам.

python bot.py --once — один проход (команды + проверка) и выход: так бот работает в GitHub Actions.
"""
import html, json, os, sys, time, urllib.error, urllib.request

import skins

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
DEFAULTS = dict(games=["cs2", "dota2"], min_price=1.0, max_price=0.0, min_profit=10.0, max_profit=100.0,
                min_sales=5, min_sell_qty=5, phases=0, interval=10, paused=0)
HELP = """<b>Команды</b>
/scan — проверить сейчас и показать топ
/top [N] — топ сделок из последней проверки
/settings — текущие фильтры
/set &lt;параметр&gt; &lt;значение&gt; — изменить фильтр, например:
  <code>/set max_price 50</code> — бюджет на один скин, $ (0 = без лимита)
  <code>/set min_profit 15</code> — мин. прибыль после комиссии, %
  <code>/set min_sales 10</code> — мин. реальных продаж за 30 дней
  <code>/set games cs2</code> — игры: cs2, dota2 или cs2,dota2
  <code>/set interval 15</code> — как часто проверять, мин (от 5)
  <code>/set phases 1</code> — показывать Doppler/Case Hardened (риск)
/pause, /resume — выключить/включить авто-уведомления

Новые сделки (или ставшие выгоднее на 10%+) бот присылает сам."""
NOTE = ""  # в режиме --once сюда пишется, что команды выполняются с задержкой


def load_env():
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH, encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k and v.strip() and not k.startswith("#"):  # пустое "TELEGRAM_TOKEN=" не считается токеном
                os.environ.setdefault(k.strip(), v.strip())


def load_state():
    st = {"owner": None, "settings": dict(DEFAULTS), "seen": {}}
    if os.path.exists(STATE_FILE):
        st.update(json.load(open(STATE_FILE, encoding="utf-8")))
        st["settings"] = {**DEFAULTS, **st["settings"]}
    if os.environ.get("OWNER_ID"):  # жёстко заданный владелец: никто другой не перехватит бота
        st["owner"] = int(os.environ["OWNER_ID"])
    return st


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)  # атомарно: файл не побьётся, если бот упадёт посреди записи


def tg(method, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/{method}",
                                 data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)["result"]


def send(chat, text):
    # Telegram режет сообщения длиннее 4096 символов — шлём кусками по блокам
    chunk = ""
    for block in text.split("\n\n"):
        if chunk and len(chunk) + len(block) > 3800:
            tg("sendMessage", chat_id=chat, text=chunk.strip(), parse_mode="HTML", disable_web_page_preview=True)
            chunk = ""
        chunk += block + "\n\n"
    if chunk.strip():
        tg("sendMessage", chat_id=chat, text=chunk.strip(), parse_mode="HTML", disable_web_page_preview=True)


def fmt(d):
    e = html.escape
    line = (f"<b>{e(d['name'])}</b> · {d['game']}\n"
            f"🛒 Купить: <a href=\"{e(d['buy_url'], True)}\">{d['buy_on']}</a> — ${d['buy']}\n"
            f"💰 Продать: <a href=\"{e(d['sell_url'], True)}\">{d['sell_on']}</a> — ${d['sell']} → на руки ${d['net']}\n"
            f"📈 <b>+${d['profit']} ({d['pct']}%)</b> · продаж: {d['sales7']} за 7д / {d['sales30']} за 30д")
    if d["ask"] > d["sell"]:
        line += f"\n⚠️ сейчас там выставлен за ${d['ask']}, но по истории продаж реально ~${d['sell']} — считаю по ней"
    return line


def scan(st):
    s = st["settings"]
    deals, errors = [], []
    st["last_scan"] = time.time()
    for game in s["games"]:
        prices, history, errs = skins.load_game(game)
        errors += [f"{game} {x}" for x in errs]
        for d in skins.find_deals(prices, history, s["min_price"], s["max_price"] or 1e9, s["min_profit"],
                                  s["max_profit"], s["min_sell_qty"], s["min_sales"], not s["phases"]):
            d["game"] = game
            deals.append(d)
    deals.sort(key=lambda d: d["profit"], reverse=True)
    return deals, errors


def pick_new(st, deals, now=None):
    """Сделки, о которых ещё не писали (или прибыль выросла на 10%+). Напоминание о старой — раз в сутки."""
    now = now or time.time()
    seen = {k: v for k, v in st["seen"].items() if now - v[1] < 86400}
    new = []
    for d in deals:
        key = f"{d['game']}|{d['name']}|{d['buy_on']}|{d['sell_on']}"
        if key not in seen or d["profit"] > seen[key][0] * 1.1:
            seen[key] = [d["profit"], now]
            new.append(d)
    st["seen"] = seen
    return new


def show_top(chat, deals, n, title):
    if not deals:
        send(chat, f"{title}\nВыгодных сделок по текущим фильтрам нет. /settings")
        return
    text = f"{title}\n\n" + "\n\n".join(fmt(d) for d in deals[:n])
    if len(deals) > n:
        text += f"\n\n…и ещё {len(deals) - n}. /top 30 — показать больше"
    send(chat, text)


def set_param(s, key, val):
    if key == "games":
        games = [g.strip() for g in val.split(",")]
        if not games or any(g not in skins.URLS for g in games):
            raise ValueError("игры: cs2, dota2 или cs2,dota2")
        s["games"] = games
    elif key in ("min_sales", "min_sell_qty", "phases", "interval"):
        v = int(val)
        if v < 0 or (key == "interval" and v < 5):  # Skinport пускает ~8 запросов за 5 минут
            raise ValueError("interval — от 5 минут, остальное — не меньше 0")
        s[key] = v
    elif key in ("min_price", "max_price", "min_profit", "max_profit"):
        s[key] = float(val)
    else:
        raise ValueError(f"нет параметра {key}")


def handle(st, msg, last):
    chat, text = msg["chat"]["id"], (msg.get("text") or "").strip()
    if st["owner"] is None and text.startswith("/start"):
        st["owner"] = chat
        save_state(st)
    if chat != st["owner"]:
        send(chat, "Это приватный бот.")
        return
    cmd, *args = text.split() or [""]
    cmd = cmd.split("@")[0].lower()
    s = st["settings"]
    if cmd in ("/start", "/help"):
        send(chat, f"Ищу скины, которые можно купить дешевле и продать дороже.\nВаш Telegram ID: <code>{chat}</code>"
                   f"\n\n{HELP}{NOTE}")
    elif cmd == "/scan":
        send(chat, "🔎 Проверяю площадки, ~1 минута…")
        deals, errors = scan(st)
        last[:] = deals
        pick_new(st, deals)
        save_state(st)
        show_top(chat, deals, 10, f"<b>Найдено сделок: {len(deals)}</b>")
        if errors:
            send(chat, "⚠️ Не загрузилось: " + html.escape("; ".join(errors)))
    elif cmd == "/top":
        show_top(chat, last, min(int(args[0]) if args else 10, 50), "<b>Топ из последней проверки</b>")
    elif cmd == "/settings":
        send(chat, "<b>Фильтры</b>\n" + "\n".join(f"{k}: {v}" for k, v in s.items()))
    elif cmd == "/set" and len(args) == 2:
        try:
            set_param(s, args[0], args[1])
            save_state(st)
            send(chat, f"✅ {args[0]} = {s[args[0]]}")
        except ValueError as e:
            send(chat, f"❌ {html.escape(str(e))}")
    elif cmd in ("/pause", "/resume"):
        s["paused"] = int(cmd == "/pause")
        save_state(st)
        send(chat, "⏸ Авто-уведомления выключены" if s["paused"] else "▶️ Авто-уведомления включены")
    else:
        send(chat, HELP)


def auto_scan(st, last):
    deals, errors = scan(st)
    last[:] = deals
    new = pick_new(st, deals)
    save_state(st)
    print(time.strftime("%H:%M"), f"сделок {len(deals)}, новых {len(new)}", *errors)
    if new:
        show_top(st["owner"], new, 10, f"🔔 <b>Новых сделок: {len(new)}</b>")


def run_once():
    """Один проход для GitHub Actions: выполнить накопившиеся команды, при необходимости проверить цены."""
    global NOTE
    NOTE = "\n\n⏱ Бот работает на GitHub: команды выполняются при следующей проверке, раз в ~15 мин."
    st, last = load_state(), []
    for u in tg("getUpdates", offset=st.get("offset", 0), timeout=0, allowed_updates=["message"]):
        st["offset"] = u["update_id"] + 1
        if "message" in u:
            try:
                handle(st, u["message"], last)
            except Exception as e:
                print("ошибка команды:", e)
                send(u["message"]["chat"]["id"], f"❌ Ошибка: {html.escape(str(e))}")
    save_state(st)
    if st.get("offset"):
        tg("getUpdates", offset=st["offset"], timeout=0, limit=1)  # подтвердить Telegram, что команды выполнены
    s = st["settings"]
    # -120 с: расписание GitHub плавает, иначе interval 15 срабатывал бы через раз
    if st["owner"] and not s["paused"] and time.time() - st.get("last_scan", 0) >= s["interval"] * 60 - 120:
        auto_scan(st, last)
    elif not st["owner"]:
        print("Владелец ещё не привязан — напишите боту /start")


def ask_token():
    """Первый запуск на компьютере: спросить токен, проверить его у Telegram и сохранить в .env."""
    while True:
        os.environ["TELEGRAM_TOKEN"] = input(
            "Вставьте токен бота из чата с @BotFather (правый клик = вставить) и нажмите Enter:\n> ").strip()
        try:
            name = tg("getMe")["username"]
            break
        except urllib.error.HTTPError:  # 401/404 — Telegram не знает такой токен
            print("Telegram не принял этот токен. Скопируйте его целиком и попробуйте ещё раз.")
    with open(ENV_PATH, "a", encoding="utf-8") as f:
        f.write(f"\nTELEGRAM_TOKEN={os.environ['TELEGRAM_TOKEN']}\n")
    print(f"Токен сохранён в .env, бот @{name}")


def main():
    load_env()
    sys.stdout.reconfigure(errors="replace")
    if not os.environ.get("TELEGRAM_TOKEN"):
        if "--once" in sys.argv or not sys.stdin.isatty():
            sys.exit("Нет TELEGRAM_TOKEN. Создайте файл .env рядом с bot.py: TELEGRAM_TOKEN=токен_от_BotFather")
        ask_token()
    skins.self_check()
    if "--once" in sys.argv:
        return run_once()
    print("Бот запущен как @" + tg("getMe")["username"], "— напишите ему /start")
    st, last, offset, next_scan = load_state(), [], 0, 0
    while True:
        try:
            active = st["owner"] and not st["settings"]["paused"]
            if active and time.time() >= next_scan:
                next_scan = time.time() + st["settings"]["interval"] * 60
                auto_scan(st, last)
            wait = int(max(1, min(25, next_scan - time.time()))) if active else 25
            for u in tg("getUpdates", offset=offset, timeout=wait, allowed_updates=["message"]):
                offset = u["update_id"] + 1
                if "message" in u:
                    try:
                        handle(st, u["message"], last)
                    except Exception as e:
                        print("ошибка команды:", e)
                        send(u["message"]["chat"]["id"], f"❌ Ошибка: {html.escape(str(e))}")
        except KeyboardInterrupt:
            break
        except Exception as e:  # сеть/Telegram моргнули — не падаем, пробуем снова
            print("ошибка:", e)
            time.sleep(10)


if __name__ == "__main__":
    main()
