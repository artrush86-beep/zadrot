"""
╔══════════════════════════════════════════════════════════╗
║  КРИПТО-МОДУЛЬ для Statham Bot                           ║
║  Бесплатные API: CoinGecko, CryptoPanic, alternative.me  ║
║  Подключить: from crypto_module import *                 ║
╚══════════════════════════════════════════════════════════╝

Команды:
  /price btc — цена BTC
  /price eth sol — несколько монет
  /fear — индекс страха и жадности
  /dominance — доминация BTC
  /news — топ крипто-новостей
  /gas — комиссии Ethereum (Etherscan, нужен бесплатный ключ)

Расписание (APScheduler):
  - каждый час: проверить важные новости по ФРС и крипте
  - каждые 4 часа: сводка рынка
"""

import os
import time
import requests
from typing import Optional

# ─── Настройки ────────────────────────────────────────────────────────────────
CRYPTOPANIC_KEY = os.environ.get("CRYPTOPANIC_KEY", "")   # free на cryptopanic.com
ETHERSCAN_KEY   = os.environ.get("ETHERSCAN_KEY", "")     # free на etherscan.io

COINGECKO_BASE  = "https://api.coingecko.com/api/v3"
CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"
FEAR_GREED_URL  = "https://api.alternative.me/fng/"

# Алиасы монет → CoinGecko ID
COIN_ALIASES = {
    "btc": "bitcoin",    "биток": "bitcoin",     "биткоин": "bitcoin",
    "eth": "ethereum",   "эфир": "ethereum",     "эфириум": "ethereum",
    "sol": "solana",     "солана": "solana",
    "bnb": "binancecoin","бнб": "binancecoin",
    "xrp": "ripple",     "рипл": "ripple",
    "ada": "cardano",    "кардано": "cardano",
    "doge": "dogecoin",  "додж": "dogecoin",
    "ton": "the-open-network", "тон": "the-open-network",
    "usdt": "tether",    "usdc": "usd-coin",
    "ltc": "litecoin",   "avax": "avalanche-2",
    "dot": "polkadot",   "link": "chainlink",
    "shib": "shiba-inu", "not": "notcoin",
    "trx": "tron",       "matic": "matic-network", "pol": "matic-network",
    "atom": "cosmos",    "near": "near",           "arb": "arbitrum",
    "op": "optimism",    "sui": "sui",             "apt": "aptos",
}


# ══════════════════════════════════════════════════════════════════════════════
# ЦЕНЫ — CoinGecko (бесплатно, 30 req/min без ключа)
# ══════════════════════════════════════════════════════════════════════════════

def get_prices(coins: list[str], vs_currency: str = "usd") -> dict:
    """
    Получает цены для списка монет.
    coins: ['btc', 'eth', 'sol'] или CoinGecko IDs
    Возвращает {coin_id: {...данные...}}
    """
    # Резолвим алиасы
    ids = []
    alias_map = {}
    for c in coins:
        cg_id = COIN_ALIASES.get(c.lower(), c.lower())
        ids.append(cg_id)
        alias_map[cg_id] = c.upper()

    if not ids:
        return {}

    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": vs_currency,
                "ids": ",".join(ids),
                "price_change_percentage": "1h,24h,7d",
                "locale": "en"
            },
            timeout=10,
            headers={"Accept": "application/json"}
        )
        if resp.status_code == 429:
            return {"error": "rate_limit"}
        resp.raise_for_status()
        data = resp.json()
        result = {}
        for coin in data:
            result[coin["id"]] = {
                "symbol": coin["symbol"].upper(),
                "name": coin["name"],
                "price": coin["current_price"],
                "change_1h": coin.get("price_change_percentage_1h_in_currency"),
                "change_24h": coin.get("price_change_percentage_24h"),
                "change_7d": coin.get("price_change_percentage_7d_in_currency"),
                "market_cap": coin.get("market_cap"),
                "volume_24h": coin.get("total_volume"),
                "rank": coin.get("market_cap_rank"),
                "ath": coin.get("ath"),
                "ath_change": coin.get("ath_change_percentage"),
            }
        return result
    except Exception as e:
        return {"error": str(e)}


def format_price_message(coins_input: list[str]) -> str:
    """Форматирует ответ с ценами для Telegram."""
    data = get_prices(coins_input)
    if "error" in data:
        if data["error"] == "rate_limit":
            return "⏳ CoinGecko перегружен, подожди минуту и попробуй снова."
        return f"❌ Ошибка получения цен: {data['error']}"
    if not data:
        return "❌ Монеты не найдены. Попробуй: btc, eth, sol, bnb, ton"

    lines = ["📊 <b>Крипто-цены</b>\n"]
    for coin_id, d in data.items():
        change_24h = d.get("change_24h")
        if change_24h is not None:
            arrow = "🟢" if change_24h >= 0 else "🔴"
            change_str = f"{arrow} {change_24h:+.2f}%"
        else:
            change_str = "—"

        price = d["price"]
        if price >= 1:
            price_str = f"${price:,.2f}"
        elif price >= 0.01:
            price_str = f"${price:.4f}"
        else:
            price_str = f"${price:.8f}"

        lines.append(
            f"<b>{d['symbol']}</b> ({d['name']})\n"
            f"  💵 {price_str}  {change_str}\n"
            f"  📅 7д: {(d.get('change_7d') or 0):+.1f}%  "
            f"  🏆 #{d.get('rank', '?')}"
        )

    lines.append(f"\n<i>via CoinGecko • {time.strftime('%H:%M МСК', time.localtime(time.time() + 3*3600))}</i>")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ДОМИНАЦИЯ И РЫНОЧНАЯ СВОДКА
# ══════════════════════════════════════════════════════════════════════════════

def get_market_overview() -> dict:
    """Глобальная статистика рынка."""
    try:
        resp = requests.get(f"{COINGECKO_BASE}/global", timeout=10)
        resp.raise_for_status()
        d = resp.json().get("data", {})
        return {
            "total_market_cap": d.get("total_market_cap", {}).get("usd"),
            "total_volume": d.get("total_volume", {}).get("usd"),
            "btc_dominance": d.get("market_cap_percentage", {}).get("btc"),
            "eth_dominance": d.get("market_cap_percentage", {}).get("eth"),
            "market_cap_change_24h": d.get("market_cap_change_percentage_24h_usd"),
            "active_coins": d.get("active_cryptocurrencies"),
        }
    except Exception:
        return {}


def format_market_message() -> str:
    """Форматирует сводку рынка для Telegram."""
    d = get_market_overview()
    if not d:
        return "❌ Не удалось получить данные рынка."

    total_cap = d.get("total_market_cap", 0)
    cap_str = f"${total_cap/1e12:.2f}T" if total_cap > 1e12 else f"${total_cap/1e9:.0f}B"

    total_vol = d.get("total_volume", 0)
    vol_str = f"${total_vol/1e9:.0f}B"

    cap_change = d.get("market_cap_change_24h", 0)
    cap_arrow = "🟢" if cap_change >= 0 else "🔴"

    return (
        f"🌍 <b>Крипто-рынок</b>\n\n"
        f"💰 Капитализация: <b>{cap_str}</b> {cap_arrow} {cap_change:+.1f}%\n"
        f"📊 Объём 24ч: <b>{vol_str}</b>\n"
        f"🔶 BTC доминация: <b>{d.get('btc_dominance', 0):.1f}%</b>\n"
        f"🔷 ETH доминация: <b>{d.get('eth_dominance', 0):.1f}%</b>\n"
        f"🔢 Монет: {d.get('active_coins', '?')}\n\n"
        f"<i>via CoinGecko • {time.strftime('%H:%M МСК', time.localtime(time.time() + 3*3600))}</i>"
    )


# ══════════════════════════════════════════════════════════════════════════════
# ИНДЕКС СТРАХА И ЖАДНОСТИ — alternative.me (бесплатно, без ключа)
# ══════════════════════════════════════════════════════════════════════════════

FEAR_GREED_EMOJI = {
    "Extreme Fear": "😱",
    "Fear": "😨",
    "Neutral": "😐",
    "Greed": "😏",
    "Extreme Greed": "🤑",
}

def get_fear_greed() -> dict:
    """Возвращает текущий индекс страха/жадности."""
    try:
        resp = requests.get(FEAR_GREED_URL, params={"limit": 1}, timeout=10)
        resp.raise_for_status()
        d = resp.json()["data"][0]
        return {
            "value": int(d["value"]),
            "label": d["value_classification"],
            "ts": d.get("timestamp"),
        }
    except Exception:
        return {}


def format_fear_greed() -> str:
    d = get_fear_greed()
    if not d:
        return "❌ Не удалось получить индекс страха и жадности."
    val = d["value"]
    label = d["label"]
    emoji = FEAR_GREED_EMOJI.get(label, "📊")
    bar = "█" * (val // 10) + "░" * (10 - val // 10)
    return (
        f"{emoji} <b>Индекс Страха и Жадности</b>\n\n"
        f"<b>{val}/100</b> — {label}\n"
        f"[{bar}]\n\n"
        f"{'🩸 Рынок в панике — возможность для покупки?' if val < 25 else ''}"
        f"{'😤 Осторожно — рынок жадный!' if val > 75 else ''}"
        f"<i>via alternative.me</i>"
    )


# ══════════════════════════════════════════════════════════════════════════════
# НОВОСТИ — CryptoPanic (бесплатно, нужен ключ) + RSS fallback
# ══════════════════════════════════════════════════════════════════════════════

def get_crypto_news(limit: int = 5, filter_: str = "hot") -> list[dict]:
    """
    Получает крипто-новости.
    filter_: hot | rising | bullish | bearish | important
    Бесплатный ключ: https://cryptopanic.com/developers/api/
    """
    if not CRYPTOPANIC_KEY:
        return _get_news_rss(limit)

    try:
        resp = requests.get(
            f"{CRYPTOPANIC_BASE}/posts/",
            params={
                "auth_token": CRYPTOPANIC_KEY,
                "filter": filter_,
                "currencies": "BTC,ETH,SOL,BNB,TON",
                "public": "true",
            },
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("results", [])[:limit]:
            results.append({
                "id": str(item.get("id")),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": item.get("source", {}).get("title", ""),
                "published": item.get("published_at", ""),
                "votes_positive": item.get("votes", {}).get("positive", 0),
                "votes_negative": item.get("votes", {}).get("negative", 0),
            })
        return results
    except Exception:
        return _get_news_rss(limit)


def _get_news_rss(limit: int = 5) -> list[dict]:
    """Fallback: RSS CoinDesk (без ключа, полностью бесплатно)."""
    try:
        resp = requests.get(
            "https://feeds.feedburner.com/CoinDesk",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        # Простой парсинг RSS без библиотек
        import re
        items = re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL)
        results = []
        for item in items[:limit]:
            title_m = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item)
            link_m  = re.search(r"<link>(.*?)</link>", item)
            title = title_m.group(1) if title_m else ""
            link  = link_m.group(1).strip() if link_m else ""
            if title:
                results.append({
                    "id": link,
                    "title": title,
                    "url": link,
                    "source": "CoinDesk",
                })
        return results
    except Exception:
        return []


def format_news_message(news: list[dict]) -> str:
    if not news:
        return "📰 Новости временно недоступны."
    lines = ["📰 <b>Крипто-новости</b>\n"]
    for i, n in enumerate(news, 1):
        source = f" [{n.get('source', '')}]" if n.get("source") else ""
        lines.append(f"{i}. {n['title']}{source}")
        if n.get("url"):
            lines.append(f"   🔗 {n['url']}")
    lines.append(f"\n<i>{time.strftime('%H:%M МСК', time.localtime(time.time() + 3*3600))}</i>")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ВАЖНЫЕ НОВОСТИ (ФРС + КРИПТО) — для планировщика
# Запускать каждый час, отправлять только если есть HOT/IMPORTANT
# ══════════════════════════════════════════════════════════════════════════════

# Ключевые слова для фильтрации важных новостей
IMPORTANT_KEYWORDS = [
    "fed", "federal reserve", "фрс", "процентная ставка", "interest rate",
    "powell", "пауэлл", "inflation", "инфляция", "recession", "рецессия",
    "etf", "bitcoin etf", "sec", "regulation", "регулирование",
    "hack", "хак", "exploit", "взлом", "liquidation", "ликвидация",
    "halving", "халвинг", "all-time high", "ath", "all time high",
    "crash", "крах", "pump", "dump", "банкрот", "bankrupt",
    "blackrock", "fidelity", "binance", "coinbase",
]

def check_important_news(sent_ids_checker=None) -> list[dict]:
    """
    Проверяет есть ли важные новости.
    sent_ids_checker: функция(id) -> bool (проверяет отправляли ли)
    """
    news = get_crypto_news(limit=20, filter_="important")
    important = []
    for n in news:
        title_lower = n["title"].lower()
        is_important = any(kw in title_lower for kw in IMPORTANT_KEYWORDS)
        already_sent = sent_ids_checker(n["id"]) if sent_ids_checker else False
        if is_important and not already_sent:
            important.append(n)
    return important[:3]  # не больше 3 за раз


def format_breaking_news(news: list[dict]) -> str:
    """Форматирует срочные новости."""
    if not news:
        return ""
    lines = ["🚨 <b>ВАЖНЫЕ НОВОСТИ</b>\n"]
    for n in news:
        lines.append(f"• {n['title']}")
        if n.get("url"):
            lines.append(f"  🔗 {n['url']}")
    lines.append(f"\n<i>{time.strftime('%H:%M МСК', time.localtime(time.time() + 3*3600))}</i>")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# КОНТЕКСТ ДЛЯ AI — строка для system prompt
# ══════════════════════════════════════════════════════════════════════════════

def get_crypto_context_for_ai() -> str:
    """
    Быстрый контекст для AI-ответов по крипте.
    Включает BTC/ETH цены + Fear&Greed.
    """
    try:
        prices = get_prices(["btc", "eth", "sol"])
        fg = get_fear_greed()

        parts = []
        for coin_id, d in prices.items():
            if "error" not in d:
                parts.append(
                    f"{d['symbol']}: ${d['price']:,.0f} ({(d.get('change_24h') or 0):+.1f}% 24ч)"
                )

        if fg:
            parts.append(f"Fear&Greed: {fg.get('value')}/100 ({fg.get('label')})")

        if parts:
            return "Текущие данные рынка: " + ", ".join(parts)
    except Exception:
        pass
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# КОМАНДЫ ДЛЯ ДОБАВЛЕНИЯ В app.py
# ══════════════════════════════════════════════════════════════════════════════
# Скопируй эти обработчики в app.py:
"""
from crypto_module import (
    format_price_message, format_market_message,
    format_fear_greed, format_news_message,
    get_crypto_news, check_important_news,
    format_breaking_news, set_last_news_sent,
    is_news_sent, get_crypto_context_for_ai,
    COIN_ALIASES
)

@bot.message_handler(commands=["price", "p"])
def cmd_price(m):
    parts = m.text.split()[1:]
    if not parts:
        _reply(m, "📊 Использование: /price btc eth sol\\nПример: /price btc")
        return
    coins = [c.lower() for c in parts[:5]]  # макс 5 монет
    msg = format_price_message(coins)
    _reply(m, msg)

@bot.message_handler(commands=["fear", "fng"])
def cmd_fear(m):
    _reply(m, format_fear_greed())

@bot.message_handler(commands=["market", "cap"])
def cmd_market(m):
    _reply(m, format_market_message())

@bot.message_handler(commands=["news", "cn"])
def cmd_news(m):
    news = get_crypto_news(limit=5)
    _reply(m, format_news_message(news))

# В планировщик добавить:
def _job_crypto_news_check():
    \"""Проверка важных новостей — каждый час.\"""
    important = check_important_news(sent_ids_checker=is_news_sent)
    if important:
        text = format_breaking_news(important)
        for n in important:
            set_last_news_sent(n["id"])
        _send_scheduled_message(text)

def _job_market_summary():
    \"""Сводка рынка — каждые 4 часа.\"""
    prices_msg = format_price_message(["btc", "eth", "sol", "bnb"])
    fg_msg = format_fear_greed()
    full = prices_msg + "\\n\\n" + fg_msg
    _send_scheduled_message(full)

_scheduler.add_job(_job_crypto_news_check, "interval", hours=1, id="crypto_news")
_scheduler.add_job(_job_market_summary, "cron", hour="8,12,16,20", minute=0, id="market_summary")
"""
