"""
crypto_module.py — Statham Bot v5.0
Крипто-функции: цены, Fear&Greed, новости, алерты
Бесплатные API: CoinGecko, alternative.me, CryptoPanic, CoinDesk RSS
"""
from __future__ import annotations
import os, re, time, requests
from typing import Optional

CRYPTOPANIC_KEY = os.environ.get("CRYPTOPANIC_KEY", "")
COINGECKO_BASE  = "https://api.coingecko.com/api/v3"
FEAR_GREED_URL  = "https://api.alternative.me/fng/"

COIN_ALIASES = {
    "btc":"bitcoin","биток":"bitcoin","биткоин":"bitcoin","bitcoin":"bitcoin",
    "eth":"ethereum","эфир":"ethereum","эфириум":"ethereum","ethereum":"ethereum",
    "sol":"solana","солана":"solana",
    "bnb":"binancecoin","бнб":"binancecoin",
    "xrp":"ripple","рипл":"ripple",
    "ada":"cardano","кардано":"cardano",
    "doge":"dogecoin","додж":"dogecoin",
    "ton":"the-open-network","тон":"the-open-network",
    "usdt":"tether","usdc":"usd-coin",
    "ltc":"litecoin","avax":"avalanche-2",
    "dot":"polkadot","link":"chainlink",
    "shib":"shiba-inu","trx":"tron",
    "matic":"matic-network","pol":"matic-network",
    "near":"near","arb":"arbitrum","op":"optimism",
    "sui":"sui","apt":"aptos","not":"notcoin",
}

IMPORTANT_KEYWORDS = [
    "fed","federal reserve","фрс","interest rate","powell","пауэлл",
    "inflation","инфляция","recession","рецессия",
    "etf","sec","regulation","регулирование","запрет","ban",
    "hack","хак","exploit","взлом","liquidation","ликвидация",
    "halving","халвинг","all-time high","ath","crash","crashing",
    "blackrock","fidelity","binance","coinbase","tether",
    "cpi","gdp","nonfarm","jobs report",
]

# ══ ЦЕНЫ ═══════════════════════════════════════════════════════════════════════
def get_prices(coins: list) -> dict:
    ids = []; alias_map = {}
    for c in coins:
        cg = COIN_ALIASES.get(c.lower(), c.lower())
        ids.append(cg); alias_map[cg] = c.upper()
    if not ids: return {}
    try:
        r = requests.get(f"{COINGECKO_BASE}/coins/markets", params={
            "vs_currency": "usd", "ids": ",".join(ids),
            "price_change_percentage": "1h,24h,7d", "locale": "en"
        }, timeout=12, headers={"Accept": "application/json"})
        if r.status_code == 429: return {"_error": "rate_limit"}
        r.raise_for_status()
        result = {}
        for coin in r.json():
            result[coin["id"]] = {
                "symbol": coin["symbol"].upper(), "name": coin["name"],
                "price": coin["current_price"],
                "change_1h": coin.get("price_change_percentage_1h_in_currency"),
                "change_24h": coin.get("price_change_percentage_24h"),
                "change_7d": coin.get("price_change_percentage_7d_in_currency"),
                "market_cap": coin.get("market_cap"),
                "volume_24h": coin.get("total_volume"),
                "rank": coin.get("market_cap_rank"),
            }
        return result
    except Exception as e:
        return {"_error": str(e)}

def _fmt_price(p):
    if p is None: return "?"
    if p >= 1000: return f"${p:,.0f}"
    if p >= 1: return f"${p:,.2f}"
    if p >= 0.01: return f"${p:.4f}"
    return f"${p:.8f}"

def _fmt_pct(v):
    if v is None: return "—"
    return f"{'🟢' if v >= 0 else '🔴'} {v:+.2f}%"

def format_price_message(coins_input: list) -> str:
    data = get_prices(coins_input)
    if "_error" in data:
        if data["_error"] == "rate_limit":
            return "⏳ CoinGecko перегружен, подожди ~1 минуту."
        return f"❌ Ошибка: {data['_error']}"
    if not data:
        return "❌ Монеты не найдены. Попробуй: btc eth sol bnb ton"

    msk = time.strftime('%H:%M', time.gmtime(time.time() + 3*3600))
    lines = [f"📊 <b>Крипто-цены</b> • {msk} МСК\n"]
    for d in data.values():
        lines.append(
            f"<b>{d['symbol']}</b> — {_fmt_price(d['price'])}\n"
            f"  24ч: {_fmt_pct(d.get('change_24h'))}  "
            f"7д: {_fmt_pct(d.get('change_7d'))}  "
            f"#{d.get('rank','?')}"
        )
    lines.append("\n<i>via CoinGecko (бесплатно)</i>")
    return "\n".join(lines)

# ══ РЫНОЧНАЯ СВОДКА ════════════════════════════════════════════════════════════
def get_market_overview() -> dict:
    try:
        r = requests.get(f"{COINGECKO_BASE}/global", timeout=12)
        r.raise_for_status(); d = r.json().get("data", {})
        pct = d.get("market_cap_percentage", {})
        return {
            "total_cap": d.get("total_market_cap", {}).get("usd"),
            "total_vol": d.get("total_volume", {}).get("usd"),
            "btc_dom": pct.get("btc"), "eth_dom": pct.get("eth"),
            "cap_change": d.get("market_cap_change_percentage_24h_usd"),
            "coins": d.get("active_cryptocurrencies"),
        }
    except Exception: return {}

def format_market_message() -> str:
    d = get_market_overview()
    if not d: return "❌ Не удалось получить данные рынка."
    cap = d.get("total_cap", 0) or 0
    vol = d.get("total_vol", 0) or 0
    cap_str = f"${cap/1e12:.2f}T" if cap > 1e12 else f"${cap/1e9:.0f}B"
    vol_str = f"${vol/1e9:.0f}B"
    chg = d.get("cap_change") or 0
    return (
        f"🌍 <b>Крипто-рынок</b>\n\n"
        f"💰 Капитализация: <b>{cap_str}</b> {'🟢' if chg>=0 else '🔴'} {chg:+.1f}%\n"
        f"📊 Объём 24ч: <b>{vol_str}</b>\n"
        f"🔶 BTC доминация: <b>{(d.get('btc_dom') or 0):.1f}%</b>\n"
        f"🔷 ETH доминация: <b>{(d.get('eth_dom') or 0):.1f}%</b>\n"
        f"🔢 Монет в листинге: {d.get('coins','?')}\n\n"
        f"<i>via CoinGecko</i>"
    )

# ══ FEAR & GREED ════════════════════════════════════════════════════════════════
_FG_EMOJI = {"Extreme Fear":"😱","Fear":"😨","Neutral":"😐","Greed":"😏","Extreme Greed":"🤑"}

def get_fear_greed() -> dict:
    try:
        r = requests.get(FEAR_GREED_URL, params={"limit": 1}, timeout=10)
        r.raise_for_status(); d = r.json()["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except Exception: return {}

def format_fear_greed() -> str:
    d = get_fear_greed()
    if not d: return "❌ Не удалось получить F&G индекс."
    val = d["value"]; label = d["label"]
    emoji = _FG_EMOJI.get(label, "📊")
    bar = "█" * (val // 10) + "░" * (10 - val // 10)
    extra = ""
    if val < 25: extra = "\n🩸 Рынок в страхе — исторически хорошая точка входа"
    elif val > 75: extra = "\n🤑 Рынок жадный — будь осторожен!"
    return f"{emoji} <b>Fear & Greed Index</b>\n\n<b>{val}/100</b> — {label}\n[{bar}]{extra}\n\n<i>via alternative.me</i>"

# ══ НОВОСТИ ════════════════════════════════════════════════════════════════════
def get_crypto_news(limit=5, filter_="hot") -> list:
    if CRYPTOPANIC_KEY:
        try:
            r = requests.get(f"https://cryptopanic.com/api/v1/posts/", params={
                "auth_token": CRYPTOPANIC_KEY, "filter": filter_,
                "currencies": "BTC,ETH,SOL,BNB,TON", "public": "true",
            }, timeout=12)
            r.raise_for_status()
            result = []
            for item in r.json().get("results", [])[:limit]:
                result.append({
                    "id": str(item.get("id")), "title": item.get("title", ""),
                    "url": item.get("url", ""), "source": item.get("source", {}).get("title", ""),
                })
            if result: return result
        except Exception: pass
    return _news_rss(limit)

def _news_rss(limit=5) -> list:
    """Fallback: CoinDesk RSS — бесплатно, без ключа."""
    try:
        r = requests.get("https://feeds.feedburner.com/CoinDesk", timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        items = re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL)
        result = []
        for item in items[:limit]:
            tm = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item)
            lm = re.search(r"<link>(.*?)</link>", item)
            title = tm.group(1).strip() if tm else ""
            link  = lm.group(1).strip() if lm else ""
            if title:
                result.append({"id": link, "title": title, "url": link, "source": "CoinDesk"})
        return result
    except Exception: return []

def format_news_message(news: list) -> str:
    if not news: return "📰 Новости временно недоступны."
    msk = time.strftime('%H:%M', time.gmtime(time.time() + 3*3600))
    lines = [f"📰 <b>Крипто-новости</b> • {msk} МСК\n"]
    for i, n in enumerate(news, 1):
        src = f" [{n.get('source','')}]" if n.get("source") else ""
        lines.append(f"{i}. {n['title']}{src}")
        if n.get("url"): lines.append(f"   🔗 {n['url']}")
    return "\n".join(lines)

def check_important_news(sent_checker=None) -> list:
    """Возвращает важные ещё не отправленные новости."""
    news = get_crypto_news(limit=20, filter_="important")
    result = []
    for n in news:
        tl = n["title"].lower()
        important = any(kw in tl for kw in IMPORTANT_KEYWORDS)
        sent = sent_checker(n["id"]) if sent_checker else False
        if important and not sent:
            result.append(n)
    return result[:3]

def format_breaking_news(news: list) -> str:
    if not news: return ""
    msk = time.strftime('%H:%M', time.gmtime(time.time() + 3*3600))
    lines = [f"🚨 <b>ВАЖНЫЕ НОВОСТИ</b> • {msk} МСК\n"]
    for n in news:
        lines.append(f"• {n['title']}")
        if n.get("url"): lines.append(f"  🔗 {n['url']}")
    return "\n".join(lines)

# ══ КОНТЕКСТ ДЛЯ AI ════════════════════════════════════════════════════════════
def get_crypto_ai_context() -> str:
    """Строка для system-prompt: актуальные цены + F&G."""
    try:
        prices = get_prices(["btc", "eth", "sol"])
        fg = get_fear_greed()
        parts = []
        for d in prices.values():
            if not isinstance(d, dict) or "_error" in d: continue
            parts.append(f"{d['symbol']}: ${d['price']:,.0f} ({(d.get('change_24h') or 0):+.1f}% 24ч)")
        if fg: parts.append(f"Fear&Greed: {fg.get('value')}/100 ({fg.get('label')})")
        if parts: return "Актуальные данные крипторынка: " + ", ".join(parts)
    except Exception: pass
    return ""

# ══ ПРОВЕРКА АЛЕРТОВ ════════════════════════════════════════════════════════════
def check_price_alerts(all_alerts: list) -> list:
    """
    Принимает все алерты, возвращает список сработавших.
    Каждый элемент: {"uid", "coin", "target", "dir", "current_price"}
    """
    if not all_alerts: return []
    coins = list({a["coin"].lower() for a in all_alerts})
    prices = get_prices(coins)
    triggered = []
    for a in all_alerts:
        cid = COIN_ALIASES.get(a["coin"].lower(), a["coin"].lower())
        d = prices.get(cid)
        if not d or "_error" in str(d): continue
        cur = d["price"]
        if a["dir"] == "above" and cur >= a["target"]:
            triggered.append({**a, "current_price": cur})
        elif a["dir"] == "below" and cur <= a["target"]:
            triggered.append({**a, "current_price": cur})
    return triggered
