"""
chart_module.py — Statham Bot v6.1
ASCII-графики, Gainers/Losers, Альтсезон, Funding Rate
Primary source: Binance (free, no key, no rate limits)
Fallback: CoinGecko (rate-limited, OHLC requires Pro key)
"""
from __future__ import annotations
import time, requests
from typing import Optional

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
BINANCE_BASE   = "https://api.binance.com/api/v3"

# ══ ASCII-ГРАФИК /chart ════════════════════════════════════════════════════════
_DAYS_MAP = {
    "1d": 1,  "1": 1,
    "7d": 7,  "7": 7,  "неделя": 7,
    "30d": 30, "30": 30, "месяц": 30,
    "90d": 90, "90": 90,
}

# Маппинг дней → интервал и лимит для Binance klines
_BINANCE_INTERVAL = {
    1:  ("1h",  24),   # 24 свечи по 1ч
    7:  ("4h",  42),   # 42 свечи по 4ч (~7 дней)
    30: ("1d",  30),   # 30 свечей по 1д
    90: ("3d",  30),   # 30 свечей по 3д
}

def _get_klines_binance(symbol: str, interval: str, limit: int) -> list:
    """Binance klines (candlestick) — без ключа, без rate-limit."""
    try:
        r = requests.get(f"{BINANCE_BASE}/klines",
                         params={"symbol": symbol, "interval": interval, "limit": limit},
                         timeout=10)
        if r.status_code != 200:
            return []
        # Формат: [[openTime, open, high, low, close, volume, ...], ...]
        return [[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4])] for k in r.json()]
    except Exception:
        return []

def get_ohlc(coin_id: str, days: int = 7) -> list:
    """
    OHLC свечи. PRIMARY: Binance klines (бесплатно).
    FALLBACK: CoinGecko (OHLC endpoint требует Pro ключ — может не работать).
    """
    from crypto_module import _BINANCE_SYMBOLS
    # 1. Binance klines — основной источник
    binance_sym = _BINANCE_SYMBOLS.get(coin_id)
    if binance_sym:
        interval, limit = _BINANCE_INTERVAL.get(days, ("4h", 42))
        klines = _get_klines_binance(binance_sym, interval, limit)
        if klines:
            return klines
    # 2. CoinGecko OHLC — fallback (требует Pro, может быть 403)
    try:
        r = requests.get(f"{COINGECKO_BASE}/coins/{coin_id}/ohlc",
                         params={"vs_currency": "usd", "days": days},
                         timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []

def _sparkline(prices: list[float], width: int = 20, height: int = 6) -> str:
    """Строит ASCII-график из списка цен."""
    if not prices or len(prices) < 2:
        return ""
    mn, mx = min(prices), max(prices)
    if mx == mn:
        return "─" * width
    # Сжимаем до width точек
    step = max(1, len(prices) // width)
    sampled = [prices[i] for i in range(0, len(prices), step)][:width]
    # Нормализуем в height уровней (0 = низ, height-1 = верх)
    rows = []
    for row in range(height - 1, -1, -1):
        line = ""
        for p in sampled:
            level = round((p - mn) / (mx - mn) * (height - 1))
            if level == row:
                line += "●"
            elif level > row:
                line += "│"
            else:
                line += " "
        rows.append(line)
    return "\n".join(rows)

def format_chart_message(coin_input: str, period_input: str = "7d") -> str:
    from crypto_module import COIN_ALIASES, _fmt_price
    coin_id = COIN_ALIASES.get(coin_input.lower(), coin_input.lower())
    days = _DAYS_MAP.get(period_input.lower(), 7)

    ohlc = get_ohlc(coin_id, days)
    if not ohlc:
        return "❌ Не удалось получить данные графика. CoinGecko может быть перегружен."

    closes = [c[4] for c in ohlc]  # close prices
    first, last = closes[0], closes[-1]
    change = (last - first) / first * 100
    arrow = "🟢" if change >= 0 else "🔴"
    hi = max(c[2] for c in ohlc)  # max high
    lo = min(c[3] for c in ohlc)  # min low

    chart = _sparkline(closes, width=24, height=8)
    period_str = {1: "24ч", 7: "7 дней", 30: "30 дней", 90: "90 дней"}.get(days, f"{days}д")

    symbol = coin_input.upper()
    return (
        f"📈 <b>{symbol}</b> — {period_str}\n\n"
        f"<pre>{chart}</pre>\n\n"
        f"Открытие: {_fmt_price(first)}\n"
        f"Закрытие: {_fmt_price(last)}  {arrow} {change:+.2f}%\n"
        f"Макс:     {_fmt_price(hi)}\n"
        f"Мин:      {_fmt_price(lo)}\n\n"
    )


# ══ GAINERS / LOSERS /movers ════════════════════════════════════════════════════
# Стейблы и деривативы — исключаем из movers
_SKIP_SUFFIXES = {"DOWN", "UP", "BULL", "BEAR", "3L", "3S"}
_SKIP_COINS    = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USDD", "FRAX"}

def get_movers(top_n: int = 5) -> dict:
    """
    Топ гейнеры и лузеры за 24ч.
    PRIMARY: Binance 24h ticker (все USDT пары, мин объём $10M).
    FALLBACK: CoinGecko top-100.
    """
    from crypto_module import _cache_get, _cache_set
    cached = _cache_get("movers")
    if cached is not None:
        return cached

    # 1. Binance — primary
    try:
        r = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=10)
        if r.status_code == 200:
            tickers = [
                t for t in r.json()
                if t["symbol"].endswith("USDT")
                and float(t.get("quoteVolume", 0)) >= 10_000_000   # мин $10M объём
                and not any(t["symbol"].startswith(s) for s in _SKIP_COINS)
                and not any(sfx in t["symbol"] for sfx in _SKIP_SUFFIXES)
                and float(t.get("lastPrice", 0)) > 0
            ]
            by_chg = sorted(tickers, key=lambda t: float(t.get("priceChangePercent", 0)))
            result = {
                "gainers": by_chg[-top_n:][::-1],
                "losers":  by_chg[:top_n],
                "_source": "binance",
            }
            _cache_set("movers", result, ttl=180)  # кэш 3 мин
            return result
    except Exception:
        pass

    # 2. CoinGecko — fallback
    try:
        r = requests.get(f"{COINGECKO_BASE}/coins/markets", params={
            "vs_currency": "usd", "order": "market_cap_desc",
            "per_page": 100, "page": 1, "price_change_percentage": "24h",
        }, timeout=12)
        if r.status_code == 200:
            stables = {"tether","usd-coin","dai","binance-usd","true-usd",
                       "first-digital-usd","usdd","frax","usdp-stablecoin"}
            coins = [c for c in r.json() if c["id"] not in stables]
            by_chg = sorted(coins, key=lambda c: c.get("price_change_percentage_24h") or 0)
            result = {
                "gainers": by_chg[-top_n:][::-1],
                "losers":  by_chg[:top_n],
                "_source": "coingecko",
            }
            _cache_set("movers", result, ttl=180)
            return result
    except Exception:
        pass
    return {}

def format_movers_message() -> str:
    data = get_movers()
    if not data:
        return "❌ Не удалось получить данные рынка. Попробуй через минуту."
    from crypto_module import _fmt_price
    src = data.get("_source", "")
    msk = time.strftime("%H:%M", time.gmtime(time.time() + 3*3600))
    lines = [f"🚀 <b>Топ движения за 24ч</b> • {msk} МСК\n"]
    lines.append("📈 <b>Лидеры роста:</b>")

    if src == "binance":
        for t in data.get("gainers", []):
            sym = t["symbol"].replace("USDT", "")
            chg = float(t.get("priceChangePercent", 0))
            price = float(t.get("lastPrice", 0))
            lines.append(f"  🟢 <b>{sym}</b> {chg:+.1f}% — {_fmt_price(price)}")
        lines.append("\n📉 <b>Лидеры падения:</b>")
        for t in data.get("losers", []):
            sym = t["symbol"].replace("USDT", "")
            chg = float(t.get("priceChangePercent", 0))
            price = float(t.get("lastPrice", 0))
            lines.append(f"  🔴 <b>{sym}</b> {chg:+.1f}% — {_fmt_price(price)}")
        lines.append("\n<i>Binance USDT пары · объём >$10M · стейблы исключены</i>")
    else:
        for c in data.get("gainers", []):
            chg = c.get("price_change_percentage_24h") or 0
            lines.append(f"  🟢 <b>{c['symbol'].upper()}</b> {chg:+.1f}% — {_fmt_price(c['current_price'])}")
        lines.append("\n📉 <b>Лидеры падения:</b>")
        for c in data.get("losers", []):
            chg = c.get("price_change_percentage_24h") or 0
            lines.append(f"  🔴 <b>{c['symbol'].upper()}</b> {chg:+.1f}% — {_fmt_price(c['current_price'])}")
        lines.append("\n<i>Из топ-100 по капитализации (стейблы исключены)</i>")
    return "\n".join(lines)


# ══ АЛЬТСЕЗОН /alts ═════════════════════════════════════════════════════════════
def _get_btc_dominance_binance() -> Optional[float]:
    """Оценка BTC доминации через объём торгов Binance (приблизительно)."""
    try:
        from crypto_module import _cache_get, _cache_set
        cached = _cache_get("btc_dom_est")
        if cached is not None:
            return cached
        r = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=10)
        if r.status_code != 200:
            return None
        tickers = [t for t in r.json() if t["symbol"].endswith("USDT")]
        btc_vol = float(next((t["quoteVolume"] for t in tickers if t["symbol"] == "BTCUSDT"), 0))
        total_vol = sum(float(t["quoteVolume"]) for t in tickers)
        if total_vol <= 0:
            return None
        dom = btc_vol / total_vol * 100
        _cache_set("btc_dom_est", dom, ttl=600)
        return dom
    except Exception:
        return None

def format_altseason_message() -> str:
    """BTC доминация — CoinGecko primary, Binance volume estimate fallback."""
    from crypto_module import _cache_get, _cache_set
    cached = _cache_get("altseason")
    if cached:
        return cached

    btc_dom = eth_dom = others = None
    source_note = ""

    # 1. CoinGecko global (точные данные по капитализации)
    try:
        r = requests.get(f"{COINGECKO_BASE}/global", timeout=8)
        if r.status_code == 200:
            d = r.json().get("data", {})
            pct = d.get("market_cap_percentage", {})
            btc_dom = pct.get("btc", 0)
            eth_dom = pct.get("eth", 0)
            others  = 100 - btc_dom - eth_dom
            source_note = "<i>Данные по капитализации: CoinGecko</i>"
    except Exception:
        pass

    # 2. Binance volume estimate (менее точно, но всегда доступно)
    if btc_dom is None:
        btc_dom = _get_btc_dominance_binance()
        if btc_dom is not None:
            eth_dom = 0  # нет данных
            others  = 100 - btc_dom
            source_note = "<i>⚠️ Оценка по объёму Binance (менее точно)</i>"

    if btc_dom is None:
        return "❌ Не удалось получить данные доминации. Попробуй /market или /price btc"

    # Определяем сезон
    if btc_dom >= 55:
        season = "🟠 <b>БИТКОИН-СЕЗОН</b>"
        comment = "BTC доминирует. Альткоины чаще падают относительно BTC.\nСтратегия: держать BTC, осторожно с альтами."
        emoji = "₿"
    elif btc_dom <= 40:
        season = "🌈 <b>АЛЬТСЕЗОН</b>"
        comment = "Альткоины опережают BTC по росту.\nСтратегия: диверсификация в качественные альты."
        emoji = "🚀"
    else:
        season = "⚖️ <b>ПЕРЕХОДНЫЙ ПЕРИОД</b>"
        comment = "Рынок в нейтральной зоне. Возможен переход в любую сторону.\nСтратегия: наблюдение, осторожные позиции."
        emoji = "👀"

    bar_btc = "█" * round(btc_dom / 5) + "░" * (20 - round(btc_dom / 5))
    eth_line = f"🔷 ETH: <b>{eth_dom:.1f}%</b>\n" if eth_dom else ""
    others_line = f"🎰 Альты: <b>{others:.1f}%</b>\n\n" if others else "\n"

    result = (
        f"{emoji} {season}\n\n"
        f"🔶 BTC: <b>{btc_dom:.1f}%</b>\n"
        f"<code>[{bar_btc}]</code>\n"
        f"{eth_line}{others_line}"
        f"{comment}\n\n"
        f"<i>BTC.D > 55% = биткоин-сезон | < 40% = альтсезон</i>\n"
        f"{source_note}"
    )
    _cache_set("altseason", result, ttl=600)
    return result


# ══ FUNDING RATE /funding ═══════════════════════════════════════════════════════
_FUNDING_SYMBOLS = {
    "btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT",
    "bnb": "BNBUSDT", "xrp": "XRPUSDT", "doge": "DOGEUSDT",
    "ton": "TONUSDT", "avax": "AVAXUSDT", "link": "LINKUSDT",
    "arb": "ARBUSDT", "op": "OPUSDT",   "ada": "ADAUSDT",
}

def get_funding_rate(symbol: str = "BTCUSDT") -> Optional[dict]:
    """Funding rate с Binance Futures (бесплатно, без ключа)."""
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                         params={"symbol": symbol, "limit": 1}, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data: return None
        d = data[0]
        rate = float(d["fundingRate"]) * 100  # в процентах
        next_ts = int(d.get("fundingTime", 0)) / 1000
        return {"rate": rate, "next_ts": next_ts, "symbol": symbol}
    except Exception:
        return None

def format_funding_message(coin_input: str = "btc") -> str:
    sym = _FUNDING_SYMBOLS.get(coin_input.lower(), coin_input.upper() + "USDT")
    data = get_funding_rate(sym)
    if not data:
        return f"❌ Нет данных funding rate для {coin_input.upper()}.\nДоступны: btc, eth, sol, bnb, xrp, doge, arb, op"

    rate = data["rate"]
    # Каждые 8 часов = 3 раза в день = ~1095 раз в год → annualized
    annual = rate * 3 * 365

    if rate > 0.1:
        sentiment = "🟢 Лонги доминируют — рынок перегрет вверх"
        risk = "⚠️ Высокий риск шорт-сквиза"
    elif rate > 0:
        sentiment = "🟡 Небольшой перевес лонгов — нейтрально"
        risk = ""
    elif rate > -0.1:
        sentiment = "🟡 Небольшой перевес шортов — нейтрально"
        risk = ""
    else:
        sentiment = "🔴 Шорты доминируют — рынок перегрет вниз"
        risk = "⚠️ Высокий риск лонг-сквиза"

    next_str = ""
    if data["next_ts"] > time.time():
        mins = int((data["next_ts"] - time.time()) / 60)
        next_str = f"\n⏰ Следующая выплата через: <b>{mins} мин</b>"

    return (
        f"💸 <b>Funding Rate {data['symbol']}</b>\n\n"
        f"Ставка: <b>{rate:+.4f}%</b> (каждые 8ч)\n"
        f"Годовых: <b>{annual:+.1f}%</b>\n\n"
        f"{sentiment}\n"
        f"{risk}{next_str}\n\n"
        f"<i>Положительный = лонги платят шортам\n"
        f"Отрицательный = шорты платят лонгам</i>"
    )


# ══ DeFi TVL /tvl ═══════════════════════════════════════════════════════════════
def format_tvl_message() -> str:
    """Total Value Locked в DeFi — через DeFiLlama (бесплатно, без ключа)."""
    try:
        r = requests.get("https://api.llama.fi/v2/chains", timeout=12)
        r.raise_for_status()
        chains = sorted(r.json(), key=lambda x: x.get("tvl", 0), reverse=True)[:8]

        r2 = requests.get("https://api.llama.fi/v2/globalCharts", timeout=12)
        total_tvl = 0
        if r2.status_code == 200:
            data = r2.json()
            if data: total_tvl = data[-1].get("totalLiquidityUSD", 0)

        def fmt_tvl(v):
            if v >= 1e9: return f"${v/1e9:.2f}B"
            if v >= 1e6: return f"${v/1e6:.0f}M"
            return f"${v:,.0f}"

        msk = time.strftime("%H:%M", time.gmtime(time.time() + 3*3600))
        lines = [f"🏦 <b>DeFi TVL</b> • {msk} МСК\n"]
        if total_tvl:
            lines.append(f"Всего в DeFi: <b>{fmt_tvl(total_tvl)}</b>\n")
        lines.append("<b>Топ сетей:</b>")
        for c in chains:
            tvl = c.get("tvl", 0)
            lines.append(f"  • <b>{c.get('name','?')}</b>: {fmt_tvl(tvl)}")
    
        return "\n".join(lines)
    except Exception:
        return "❌ Не удалось получить данные DeFiLlama."
