"""
crypto_module.py — Statham Bot v6.1
Крипто-функции: цены, Fear&Greed, новости, алерты
Primary: Binance (no key, no rate limits)
Fallback: CoinGecko (free tier, rate-limited)
"""
from __future__ import annotations
import os, re, time, requests, json as _json
from typing import Optional

COINGECKO_BASE  = "https://api.coingecko.com/api/v3"
BINANCE_BASE    = "https://api.binance.com/api/v3"
FEAR_GREED_URL  = "https://api.alternative.me/fng/"
CRYPTOPANIC_KEY = os.environ.get("CRYPTOPANIC_KEY", "")
CG_API_KEY      = os.environ.get("COINGECKO_API_KEY", "")  # опционально Demo key

# ══ RAM-КЭШ (in-process, сбрасывается при рестарте) ════════════════════════════
_CACHE: dict = {}  # {key: (data, expire_ts)}

def _cache_get(key: str):
    item = _CACHE.get(key)
    if item and time.time() < item[1]:
        return item[0]
    if key in _CACHE:
        del _CACHE[key]
    return None

def _cache_set(key: str, data, ttl: int = 300):
    _CACHE[key] = (data, time.time() + ttl)
    # Чистим просроченные записи если кэш вырос
    if len(_CACHE) > 50:
        now = time.time()
        expired = [k for k, v in list(_CACHE.items()) if now >= v[1]]
        for k in expired:
            _CACHE.pop(k, None)

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


# ══ ЦЕНЫ ═══════════════════════════════════════════════════════════════════════
# Binance символы для fallback
_BINANCE_SYMBOLS = {
    "bitcoin":"BTCUSDT","ethereum":"ETHUSDT","solana":"SOLUSDT",
    "binancecoin":"BNBUSDT","ripple":"XRPUSDT","cardano":"ADAUSDT",
    "dogecoin":"DOGEUSDT","the-open-network":"TONUSDT",
    "avalanche-2":"AVAXUSDT","polkadot":"DOTUSDT","chainlink":"LINKUSDT",
    "near":"NEARUSDT","arbitrum":"ARBUSDT","optimism":"OPUSDT",
    "tron":"TRXUSDT","shiba-inu":"SHIBUSDT",
}

def _get_prices_binance(coin_ids: list) -> dict:
    """PRIMARY: цены с Binance (без ключа, без rate-limit, работает всегда)."""
    result = {}
    cache_key = "binance_24hr"
    tickers_raw = _cache_get(cache_key)
    if tickers_raw is None:
        try:
            r = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=10)
            if r.status_code != 200:
                return {}
            tickers_raw = r.json()
            _cache_set(cache_key, tickers_raw, ttl=30)  # кэш 30 сек для all-tickers
        except Exception:
            return {}
    tickers = {t["symbol"]: t for t in tickers_raw}
    for coin_id in coin_ids:
        sym = _BINANCE_SYMBOLS.get(coin_id)
        if not sym:
            continue
        t = tickers.get(sym)
        if not t:
            continue
        price = float(t["lastPrice"])
        change_24h = float(t.get("priceChangePercent", 0))
        result[coin_id] = {
            "symbol": sym.replace("USDT", ""),
            "name": coin_id.replace("-", " ").title(),
            "price": price,
            "change_1h": None,
            "change_24h": change_24h,
            "change_7d": None,
            "market_cap": None,
            "volume_24h": float(t.get("quoteVolume", 0)),
            "rank": None,
            "_source": "binance",
        }
    return result


def _enrich_with_coingecko(result: dict, ids: list) -> dict:
    """Обогащает данные Binance рангом и капитализацией из CoinGecko (необязательно)."""
    try:
        headers = {"Accept": "application/json"}
        if CG_API_KEY:
            headers["x-cg-demo-api-key"] = CG_API_KEY
        r = requests.get(f"{COINGECKO_BASE}/coins/markets", params={
            "vs_currency": "usd", "ids": ",".join(ids),
            "price_change_percentage": "1h,24h,7d",
        }, timeout=6, headers=headers)
        if r.status_code == 200:
            for coin in r.json():
                cid = coin["id"]
                if cid in result:
                    result[cid].update({
                        "change_1h":   coin.get("price_change_percentage_1h_in_currency"),
                        "change_7d":   coin.get("price_change_percentage_7d_in_currency"),
                        "market_cap":  coin.get("market_cap"),
                        "rank":        coin.get("market_cap_rank"),
                        "_source":     "coingecko",
                        # CoinGecko price is more accurate (more decimal places)
                        "price":       coin.get("current_price") or result[cid]["price"],
                        "change_24h":  coin.get("price_change_percentage_24h") or result[cid]["change_24h"],
                    })
    except Exception:
        pass  # CoinGecko enrichment is optional
    return result


def get_prices(coins: list) -> dict:
    """
    Получает цены монет.
    Порядок: RAM-cache → Binance (primary) → CoinGecko enrichment → CoinGecko direct fallback
    """
    ids = []
    for c in coins:
        ids.append(COIN_ALIASES.get(c.lower(), c.lower()))
    if not ids:
        return {}

    # 1. Проверяем RAM-кэш (5 минут)
    cache_key = "prices:" + ",".join(sorted(ids))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # 2. Binance — PRIMARY (быстро, надёжно, без ключа)
    result = _get_prices_binance(ids)

    if result:
        # 3. CoinGecko — опциональное обогащение (rank, market_cap, 7d change)
        _enrich_with_coingecko(result, ids)
        _cache_set(cache_key, result, ttl=120)  # кэш 2 мин
        return result

    # 4. Если Binance недоступен (редко) — CoinGecko напрямую
    try:
        headers = {"Accept": "application/json"}
        if CG_API_KEY:
            headers["x-cg-demo-api-key"] = CG_API_KEY
        r = requests.get(f"{COINGECKO_BASE}/coins/markets", params={
            "vs_currency": "usd", "ids": ",".join(ids),
            "price_change_percentage": "1h,24h,7d",
        }, timeout=12, headers=headers)
        if r.status_code in (401, 403, 429):
            return {"_error": "rate_limit"}
        r.raise_for_status()
        for coin in r.json():
            result[coin["id"]] = {
                "symbol": coin["symbol"].upper(), "name": coin["name"],
                "price": coin["current_price"],
                "change_1h":  coin.get("price_change_percentage_1h_in_currency"),
                "change_24h": coin.get("price_change_percentage_24h"),
                "change_7d":  coin.get("price_change_percentage_7d_in_currency"),
                "market_cap": coin.get("market_cap"),
                "volume_24h": coin.get("total_volume"),
                "rank":       coin.get("market_cap_rank"),
                "_source":    "coingecko",
            }
        if result:
            _cache_set(cache_key, result, ttl=120)
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

    return "\n".join(lines)

# ══ РЫНОЧНАЯ СВОДКА ════════════════════════════════════════════════════════════
def get_market_overview() -> dict:
    """Обзор рынка с кэшем 10 минут. Источник: CoinGecko global."""
    cached = _cache_get("market_overview")
    if cached is not None:
        return cached
    try:
        headers = {}
        if CG_API_KEY:
            headers["x-cg-demo-api-key"] = CG_API_KEY
        r = requests.get(f"{COINGECKO_BASE}/global", timeout=10, headers=headers)
        r.raise_for_status()
        d = r.json().get("data", {})
        pct = d.get("market_cap_percentage", {})
        result = {
            "total_cap": d.get("total_market_cap", {}).get("usd"),
            "total_vol": d.get("total_volume", {}).get("usd"),
            "btc_dom":   pct.get("btc"),
            "eth_dom":   pct.get("eth"),
            "cap_change": d.get("market_cap_change_percentage_24h_usd"),
            "coins":     d.get("active_cryptocurrencies"),
        }
        _cache_set("market_overview", result, ttl=600)  # кэш 10 мин
        return result
    except Exception:
        return {}

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
        f"🔢 Монет в листинге: {d.get('coins','?')}"
    )

# ══ FEAR & GREED ════════════════════════════════════════════════════════════════
_FG_EMOJI = {
    "Extreme Fear": "😱", "Fear": "😨",
    "Neutral": "😐",
    "Greed": "😏", "Extreme Greed": "🤑",
}
_FG_RU = {
    "Extreme Fear": "Экстремальный страх",
    "Fear":         "Страх",
    "Neutral":      "Нейтрально",
    "Greed":        "Жадность",
    "Extreme Greed":"Экстремальная жадность",
}
_FG_ZONES = [
    (0,  24,  "Extreme Fear"),
    (25, 44,  "Fear"),
    (45, 55,  "Neutral"),
    (56, 74,  "Greed"),
    (75, 100, "Extreme Greed"),
]

def _fg_label_by_value(v: int) -> str:
    """Определяем зону по числу — не зависим от строки API."""
    for lo, hi, label in _FG_ZONES:
        if lo <= v <= hi:
            return label
    return "Neutral"

def get_fear_greed() -> dict:
    """Fear & Greed с кэшем 15 минут (alternative.me)."""
    cached = _cache_get("fear_greed")
    if cached is not None:
        return cached

    urls = [
        "https://api.alternative.me/fng/?limit=1",
        "https://api.alternative.me/fng/1",
        "https://api.alternative.me/fng/?limit=1&format=json",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            d = r.json()["data"][0]
            val = int(d["value"])
            raw_label = d.get("value_classification", "")
            label = raw_label if raw_label in _FG_RU else _fg_label_by_value(val)
            result = {"value": val, "label": label}
            _cache_set("fear_greed", result, ttl=900)  # кэш 15 мин
            return result
        except Exception:
            continue
    return {}

def format_fear_greed() -> str:
    d = get_fear_greed()
    if not d:
        return "❌ Не удалось получить индекс страха и жадности."
    val   = d["value"]
    label = d["label"]
    ru    = _FG_RU.get(label, label)
    emoji = _FG_EMOJI.get(label, "📊")
    # Бар из 20 символов
    filled = round(val / 100 * 20)
    bar = "█" * filled + "░" * (20 - filled)
    # Комментарий по зоне
    if val <= 24:
        comment = "\n🩸 <i>Рынок в панике — исторически хорошая точка входа</i>"
    elif val <= 44:
        comment = "\n📉 <i>Преобладает страх — возможны покупки на откатах</i>"
    elif val <= 55:
        comment = "\n😐 <i>Рынок спокоен — нет явного направления</i>"
    elif val <= 74:
        comment = "\n📈 <i>Рынок оптимистичен — следи за перегревом</i>"
    else:
        comment = "\n🤑 <i>Рынок жадный — осторожно, возможна коррекция</i>"
    msk = time.strftime("%H:%M", time.gmtime(time.time() + 3*3600))
    return (
        f"{emoji} <b>Индекс Страха и Жадности</b> • {msk} МСК\n\n"
        f"<b>{val} / 100</b> — {ru}\n"
        f"<code>[{bar}]</code>"
        f"{comment}"
    )

# ══ НОВОСТИ ════════════════════════════════════════════════════════════════════
def get_crypto_news(limit=5, filter_="hot") -> list:
    """Новости: CryptoPanic (если ключ есть) → иначе мультиисточниковый RSS."""
    if CRYPTOPANIC_KEY:
        try:
            r = requests.get("https://cryptopanic.com/api/v1/posts/", params={
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
            if result:
                return result
        except Exception:
            pass
    # Без CryptoPanic — читаем русские + английские RSS
    return _news_rss(limit)

# RSS-источники: сначала русские, потом английские
_RSS_SOURCES = [
    # ── Русские (приоритет) ──────────────────────────────────────────────────
    ("Forklog",       "https://forklog.com/feed/"),
    ("BeInCrypto RU", "https://ru.beincrypto.com/feed/"),
    ("Bits.Media",    "https://bits.media/rss/news/"),
    # ── Английские (fallback) ────────────────────────────────────────────────
    ("CoinDesk",      "https://feeds.feedburner.com/CoinDesk"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt",       "https://decrypt.co/feed"),
]

def _parse_rss(url: str, source: str, limit: int) -> list:
    """Парсит один RSS-фид, возвращает список новостей."""
    try:
        r = requests.get(url, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        items = re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL)
        result = []
        for item in items[:limit]:
            # title: CDATA или обычный тег
            tm = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item)               or re.search(r"<title>(.*?)</title>", item)
            lm = re.search(r"<link>(.*?)</link>", item)
            title = tm.group(1).strip() if tm else ""
            link  = lm.group(1).strip() if lm else ""
            if title and len(title) > 5:
                result.append({"id": link or title, "title": title,
                               "url": link, "source": source})
        return result
    except Exception:
        return []

def _news_rss(limit=5) -> list:
    """Собирает новости из нескольких RSS — русские первыми."""
    result = []
    for source, url in _RSS_SOURCES:
        if len(result) >= limit:
            break
        need = limit - len(result)
        items = _parse_rss(url, source, need + 2)
        result.extend(items[:need])
    return result[:limit]

# Русские источники — для метки языка
_RU_SOURCES = {"Forklog", "BeInCrypto RU", "Bits.Media"}

def format_news_message(news: list) -> str:
    if not news:
        return "📰 Новости временно недоступны."
    msk = time.strftime("%H:%M", time.gmtime(time.time() + 3*3600))
    lines = [f"📰 <b>Крипто-новости</b> • {msk} МСК\n"]
    for i, n in enumerate(news, 1):
        source = n.get("source", "")
        lang   = "" if source in _RU_SOURCES else " 🇬🇧" if source else ""
        src_str = f" <i>[{source}{lang}]</i>" if source else ""
        lines.append(f"{i}. {n['title']}{src_str}")
        if n.get("url"):
            lines.append(f"   🔗 {n['url']}")
    return "\n".join(lines)


def _translate_title(title: str) -> str:
    """Переводит заголовок через Groq. Возвращает оригинал если не удалось."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        return title
    # Быстрая проверка — если уже кириллица, не переводим
    cyrillic_ratio = sum(1 for c in title if "\u0400" <= c <= "\u04ff") / max(len(title), 1)
    if cyrillic_ratio > 0.3:
        return title
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{
                    "role": "user",
                    "content": (
                        f"Переведи этот заголовок крипто-новости на русский язык. "
                        f"Только перевод, без пояснений, без кавычек:\n{title}"
                    )
                }],
                "max_tokens": 80,
                "temperature": 0.1,
            },
            timeout=8,
        )
        if r.status_code == 200:
            translated = r.json()["choices"][0]["message"]["content"].strip()
            # Защита: если ответ слишком длинный или странный — оригинал
            if len(translated) > len(title) * 3 or len(translated) < 5:
                return title
            return translated
    except Exception:
        pass
    return title


def _translate_titles_batch(titles: list[str]) -> list[str]:
    """Переводит список заголовков одним запросом к Groq."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key or not titles:
        return titles
    # Если все уже на русском — пропускаем
    need_translate = []
    for i, t in enumerate(titles):
        cyrillic = sum(1 for c in t if "\u0400" <= c <= "\u04ff") / max(len(t), 1)
        if cyrillic < 0.3:
            need_translate.append((i, t))
    if not need_translate:
        return titles

    numbered = "\n".join(f"{i+1}. {t}" for i, (_, t) in enumerate(need_translate))
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{
                    "role": "user",
                    "content": (
                        "Переведи эти заголовки крипто-новостей на русский язык. "
                        "Отвечай ТОЛЬКО пронумерованным списком переводов, без пояснений:\n\n"
                        + numbered
                    )
                }],
                "max_tokens": 300,
                "temperature": 0.1,
            },
            timeout=12,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()
            translated_lines = [l.strip() for l in content.split("\n") if l.strip()]
            result = list(titles)
            for j, (orig_idx, _) in enumerate(need_translate):
                if j < len(translated_lines):
                    # Убираем нумерацию "1. "
                    line = translated_lines[j]
                    line = line.lstrip("0123456789. ").strip()
                    if line:
                        result[orig_idx] = line
            return result
    except Exception:
        pass
    return titles


def format_breaking_news(news: list) -> str:
    if not news: return ""
    msk = time.strftime("%H:%M", time.gmtime(time.time() + 3*3600))
    # Переводим все заголовки одним батч-запросом
    titles = [n["title"] for n in news]
    translated = _translate_titles_batch(titles)
    lines = [f"🚨 <b>ВАЖНЫЕ НОВОСТИ</b> • {msk} МСК\n"]
    for i, n in enumerate(news):
        title_ru = translated[i] if i < len(translated) else n["title"]
        lines.append(f"• {title_ru}")
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
