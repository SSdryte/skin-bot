"""Ищет скины CS2 / Dota 2, которые на одной площадке дешевле, чем можно продать на другой.

Запуск:  python skins.py --game cs2 --min-profit 10 --max-price 100 --top 30
Результат: таблица в консоли + arbitrage_<game>.csv
"""
import argparse, csv, gzip, json, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import brotli  # Skinport отдаёт данные только в brotli

# Комиссия продавца, % (ПРИМЕРНО — сверьте с сайтами, они меняются).
SELL_FEE = {"lis-skins": 5, "market.csgo": 5, "skinport": 12}
# CS.Trade только для покупки: у них нельзя выставить скин за деньги.

# Насколько цена продажи может быть выше медианы реальных продаж Skinport за 30 дней.
# На самом Skinport — не выше медианы; на других площадках цены бывают чуть выше.
# shortcut: одна общая история (Skinport) на все площадки; точнее — история каждой площадки, нужны их API-ключи.
HIST_CAP = {"skinport": 1.0, "default": 1.2}

URLS = {
    "cs2": {
        "lis-skins": "https://lis-skins.com/market_export_json/csgo.json",
        "cs.trade": "https://cdn.cs.trade:2096/api/prices_CSGO",
        "market.csgo": "https://market.csgo.com/api/v2/prices/USD.json",
        "skinport": "https://api.skinport.com/v1/items?app_id=730&currency=USD",
    },
    "dota2": {
        "lis-skins": "https://lis-skins.com/market_export_json/dota2.json",
        "cs.trade": "https://cdn.cs.trade:2096/api/prices_DOTA2",
        "market.csgo": "https://market.dota2.net/api/v2/prices/USD.json",
        "skinport": "https://api.skinport.com/v1/items?app_id=570&currency=USD",
    },
}
# Цена зависит от фазы/паттерна, а в названии его нет -> сравнение цен по имени врёт.
PHASE_WORDS = ("Doppler", "Case Hardened", "Marble Fade", "Fade (", "Crimson Web")

HISTORY_URL = {"cs2": "https://api.skinport.com/v1/sales/history?app_id=730&currency=USD",
               "dota2": "https://api.skinport.com/v1/sales/history?app_id=570&currency=USD"}
TM_SITE = {"cs2": "https://market.csgo.com", "dota2": "https://market.dota2.net"}


def fetch(url, retries=2):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "br, gzip"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw, enc = r.read(), r.headers.get("Content-Encoding", "")
    except urllib.error.HTTPError as e:
        if e.code == 429 and retries:  # Skinport: ~8 запросов за 5 минут с одного IP
            time.sleep(30)
            return fetch(url, retries - 1)
        raise
    if enc == "br":
        raw = brotli.decompress(raw)
    elif enc == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def parse(source, data, game):
    """-> {market_hash_name: (цена $, кол-во в продаже, ссылка)} — только то, что есть в наличии."""
    out = {}
    if source == "lis-skins":
        for i in data:
            # unlocked_price — без трейд-лока, такой скин можно сразу перепродать
            p = i.get("unlocked_price") or i["price"]
            if i.get("count"):
                out[i["name"]] = (float(p), i["count"], i.get("url", ""))
    elif source == "cs.trade":
        for name, i in data.items():
            if i.get("available_stock") and i.get("available_lowest_store_price"):
                out[name] = (float(i["available_lowest_store_price"]), i["available_stock"], "https://cs.trade/")
    elif source == "market.csgo":
        for i in data["items"]:
            if i.get("volume"):
                n = i["market_hash_name"]
                out[n] = (float(i["price"]), i["volume"], f"{TM_SITE[game]}/?search={quote(n)}")
    elif source == "skinport":
        for i in data:
            if i.get("min_price") and i.get("quantity"):
                out[i["market_hash_name"]] = (float(i["min_price"]), i["quantity"], i.get("item_page", ""))
    return out


def parse_history(data):
    """-> {имя: (медиана продаж за 30 дней или None, продаж за 30 дней, продаж за 7 дней)}"""
    return {i["market_hash_name"]: (i["last_30_days"]["median"], i["last_30_days"]["volume"],
                                    i["last_7_days"]["volume"]) for i in data}


def load_game(game):
    """Скачивает все площадки и историю продаж параллельно. -> (prices, history, errors)"""
    errors = []

    def get(job):
        src, url = job
        try:
            d = fetch(url)
            return src, (parse_history(d) if src == "history" else parse(src, d, game))
        except Exception as e:  # одна упавшая площадка не должна ломать остальные
            errors.append(f"{src}: {e}")
            return src, {}

    jobs = list(URLS[game].items()) + [("history", HISTORY_URL[game])]
    with ThreadPoolExecutor() as ex:
        res = dict(ex.map(get, jobs))
    return {k: v for k, v in res.items() if k != "history"}, res["history"], errors


def find_deals(prices, history, min_price=1, max_price=1e9, min_profit=10, max_profit=100,
               min_sell_qty=5, min_sales=5, skip_phases=True):
    """prices: {источник: {имя: (цена, кол-во, ссылка)}}, history: {имя: (медиана30, продаж30, продаж7)}"""
    names = set().union(*prices.values())
    deals = []
    for name in names:
        offers = {s: p[name] for s, p in prices.items() if name in p}
        if len(offers) < 2 or (skip_phases and any(w in name for w in PHASE_WORDS)):
            continue
        buy_src, (buy, _, buy_url) = min(offers.items(), key=lambda kv: kv[1][0])
        if not min_price <= buy <= max_price:
            continue
        median, sales30, sales7 = history.get(name) or (None, 0, 0)
        if sales30 < min_sales:  # неликвид: продать будет долго или нереально
            continue
        best = None
        for sell_src, (ask, qty, sell_url) in offers.items():
            if sell_src == buy_src or sell_src not in SELL_FEE or qty < min_sell_qty:
                continue
            est = ask
            if median:  # не верим цене выше той, за которую реально покупают
                est = min(ask, median * HIST_CAP.get(sell_src, HIST_CAP["default"]))
            net = est * (1 - SELL_FEE[sell_src] / 100)
            if best is None or net > best["net"]:
                best = dict(sell_on=sell_src, ask=round(ask, 2), sell=round(est, 2), net=round(net, 2),
                            sell_qty=qty, sell_url=sell_url)
        if not best:
            continue
        pct = (best["net"] - buy) / buy * 100
        if min_profit <= pct <= max_profit:
            deals.append(dict(name=name, buy_on=buy_src, buy=round(buy, 2), **best,
                              profit=round(best["net"] - buy, 2), pct=round(pct, 1),
                              median30=median, sales30=sales30, sales7=sales7, buy_url=buy_url))
    return sorted(deals, key=lambda d: d["profit"], reverse=True)


def self_check():
    p = {"lis-skins": {"A": (10.0, 3, ""), "B": (5.0, 1, ""), "C": (10.0, 3, "")},
         "skinport": {"A": (14.0, 20, ""), "B": (5.1, 50, ""), "C": (30.0, 9, "")},
         "cs.trade": {"A": (9.0, 1, "")}}
    h = {"A": (15.0, 10, 3), "B": (5.0, 10, 3), "C": (12.0, 10, 2)}
    p["lis-skins"]["★ Karambit | Doppler (Factory New)"] = (100.0, 5, "")
    p["skinport"]["★ Karambit | Doppler (Factory New)"] = (300.0, 9, "")
    d = {x["name"]: x for x in find_deals(p, h)}
    assert set(d) == {"A"}, d  # Doppler отсеян по фазе; B без профита; C: аск 30, но реально продаётся за 12 -> 12*0.88 < 10*1.1
    assert d["A"]["buy_on"] == "cs.trade" and d["A"]["sell_on"] == "skinport" and d["A"]["net"] == 12.32, d
    assert find_deals(p, {"A": (15.0, 1, 0)}) == []  # мало продаж -> неликвид


def main():
    ap = argparse.ArgumentParser(description="Поиск выгодных для перепродажи скинов")
    ap.add_argument("--game", choices=["cs2", "dota2"], default="cs2")
    ap.add_argument("--min-price", type=float, default=1.0, help="минимальная цена покупки, $")
    ap.add_argument("--max-price", type=float, default=1e9, help="максимальная цена покупки (ваш бюджет), $")
    ap.add_argument("--min-profit", type=float, default=10.0, help="минимальная прибыль после комиссии, %%")
    ap.add_argument("--max-profit", type=float, default=100.0,
                    help="выше — почти всегда фейк: паттерн/фаза (Doppler, Case Hardened) или устаревшая цена, %%")
    ap.add_argument("--min-sell-qty", type=int, default=5, help="сколько лотов должно быть на площадке продажи")
    ap.add_argument("--min-sales", type=int, default=5, help="минимум реальных продаж за 30 дней (ликвидность)")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--with-phases", action="store_true", help="не отсеивать Doppler/Case Hardened/Fade")
    a = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")  # консоль Windows не умеет печатать ★ и ™
    self_check()

    prices, history, errors = load_game(a.game)
    for e in errors:
        print(f"[!] не загрузилось: {e}", file=sys.stderr)
    for s, p in prices.items():
        print(f"{s:12} {len(p):6} предметов")
    print(f"{'история':12} {len(history):6} предметов")

    deals = find_deals(prices, history, a.min_price, a.max_price, a.min_profit, a.max_profit,
                       a.min_sell_qty, a.min_sales, not a.with_phases)
    print(f"\nНайдено сделок: {len(deals)}  (показаны топ-{a.top} по прибыли в $)\n")
    print(f"{'Предмет':45} {'Купить':>20} {'Продать':>22} {'Чистыми':>8} {'Прибыль':>14} {'Прод/30д':>8}")
    for d in deals[: a.top]:
        print(f"{d['name'][:45]:45} {d['buy_on']:>12} {d['buy']:>7} {d['sell_on']:>12} {d['sell']:>9} "
              f"{d['net']:>8} {d['profit']:>7} {d['pct']:>5}% {d['sales30']:>8}")

    if deals:
        path = f"arbitrage_{a.game}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=deals[0].keys(), delimiter=";")
            w.writeheader()
            w.writerows(deals)
        print(f"\nВсе сделки сохранены в {path}")


if __name__ == "__main__":
    main()
