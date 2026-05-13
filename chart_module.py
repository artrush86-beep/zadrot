"""
chart_module.py — Statham Bot v6.0
Уровень 2: ASCII-графики, Gainers/Losers, Альтсезон, Funding Rate
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

def get_ohlc(coin_id: str, days: int = 7) -> list:
    """OHLC свечи с CoinGecko."""
    try:
        r = requests.get(f"{COINGECKO_BASE}/coins/{coin_id}/ohlc",
                         params={"vs_currency": "usd", "days": days},
                         timeout=12)
        if r.status_code == 429: return []
        r.raise_for_status()
        return r.json()  # [[timestamp, open, high, low, close], ...]
    except Exception:
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
        f"<i>via CoinGecko</i>"
    )


# ══ GAINERS / LOSERS /movers ════════════════════════════════════════════════════
def get_movers(top_n: int = 5) -> dict:
    """Топ гейнеры и лузеры из топ-100 по капитализации."""
    try:
        r = requests.get(f"{COINGECKO_BASE}/coins/markets", params={
            "vs_currency": "usd", "order": "market_cap_desc",
            "per_page": 100, "page": 1,
            "price_change_percentage": "24h",
        }, timeout=12)
        if r.status_code == 429: return {}
        r.raise_for_status()
        coins = r.json()
        # Фильтруем стейблы
        stables = {"tether", "usd-coin", "dai", "binance-usd", "true-usd",
                   "first-digital-usd", "usdd", "frax", "usdp-stablecoin"}
        coins = [c for c in coins if c["id"] not in stables]
        by_change = sorted(coins, key=lambda c: c.get("price_change_percentage_24h") or 0)
        losers  = by_change[:top_n]
        gainers = by_change[-top_n:][::-1]
        return {"gainers": gainers, "losers": losers}
    except Exception:
        return {}

def format_movers_message() -> str:
    data = get_movers()
    if not data:
        return "❌ Не удалось получить данные рынка."
    from crypto_module import _fmt_price
    msk = time.strftime("%H:%M", time.gmtime(time.time() + 3*3600))
    lines = [f"🚀 <b>Топ движения за 24ч</b> • {msk} МСК\n"]

    lines.append("📈 <b>Лидеры роста:</b>")
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
def format_altseason_message() -> str:
    """BTC доминация + вывод: биткоин-сезон или альтсезон."""
    try:
        r = requests.get(f"{COINGECKO_BASE}/global", timeout=12)
        r.raise_for_status()
        d = r.json().get("data", {})
        pct = d.get("market_cap_percentage", {})
        btc_dom = pct.get("btc", 0)
        eth_dom = pct.get("eth", 0)
        others  = 100 - btc_dom - eth_dom

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

        return (
            f"{emoji} {season}\n\n"
            f"🔶 BTC: <b>{btc_dom:.1f}%</b>\n"
            f"<code>[{bar_btc}]</code>\n"
            f"🔷 ETH: <b>{eth_dom:.1f}%</b>\n"
            f"🎰 Альты: <b>{others:.1f}%</b>\n\n"
            f"{comment}\n\n"
            f"<i>BTC.D > 55% = биткоин-сезон | < 40% = альтсезон</i>"
        )
    except Exception:
        return "❌ Не удалось получить данные доминации."


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
        f"Отрицательный = шорты платят лонгам\n"
        f"via Binance Futures</i>"
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
        lines.append("\n<i>via DeFiLlama</i>")
        return "\n".join(lines)
    except Exception:
        return "❌ Не удалось получить данные DeFiLlama."
