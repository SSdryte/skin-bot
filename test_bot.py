"""Проверка бота без Telegram: команды подменяются заглушкой, цены — настоящие.  python test_bot.py"""
import os, sys, tempfile

import bot

sys.stdout.reconfigure(errors="replace")
bot.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")
out = []
bot.tg = lambda m, **p: out.append((p.get("chat_id"), p.get("text", ""))) or {}
st, last = bot.load_state(), []


def say(chat, text):
    out.clear()
    bot.handle(st, {"chat": {"id": chat}, "text": text}, last)
    return [t for _, t in out]


assert "Команды" in say(1, "/start")[0] and st["owner"] == 1
assert say(2, "/scan") == ["Это приватный бот."]
assert "max_price = 50.0" in say(1, "/set max_price 50")[0]
assert "❌" in say(1, "/set interval 2")[0]
assert "❌" in say(1, "/set games csgo")[0]
say(1, "")  # стикер/фото без текста не роняет бота

msgs = say(1, "/scan")
print("\n---\n".join(msgs)[:2500])
assert last and all(d["buy"] <= 50 for d in last), "бюджет не соблюдён"
assert all(len(t) <= 4096 for t in msgs)

out.clear()
bot.auto_scan(st, last)
print("авто-скан сразу после /scan отправил сообщений:", len(out))

d = dict(game="cs2", name="X", buy_on="a", sell_on="b", profit=10)
assert bot.pick_new(st, [d], now=1e10) == [d] and bot.pick_new(st, [d], now=1e10) == []
assert bot.pick_new(st, [dict(d, profit=12)], now=1e10)  # выросла на 20% -> снова шлём
assert bot.pick_new(st, [dict(d, profit=12)], now=1e10 + 90000)  # прошли сутки -> напомнить
# --- режим --once (GitHub Actions): состояние между запусками только в state.json ---
os.remove(bot.STATE_FILE)
calls, inbox = [], [{"update_id": 7, "message": {"chat": {"id": 5}, "text": "/start"}},
                    {"update_id": 8, "message": {"chat": {"id": 5}, "text": "/set games dota2"}}]


def fake_tg(m, **p):
    calls.append((m, p))
    if m == "getUpdates":
        return [u for u in inbox if u["update_id"] >= p["offset"]][: p.get("limit", 100)]
    return {}


bot.tg = fake_tg
bot.run_once()
sent = [p["text"] for m, p in calls if m == "sendMessage"]
st2 = bot.load_state()
assert st2["owner"] == 5 and st2["offset"] == 9 and st2["settings"]["games"] == ["dota2"], st2
assert "раз в ~15 мин" in sent[0] and st2.get("last_scan"), sent  # после команд сразу проверил цены
calls.clear()
bot.run_once()  # 15 минут не прошло, новых команд нет -> ничего не делает
assert [m for m, _ in calls] == ["getUpdates", "getUpdates"], calls
os.environ["OWNER_ID"] = "42"
assert bot.load_state()["owner"] == 42
print("все проверки ок")
