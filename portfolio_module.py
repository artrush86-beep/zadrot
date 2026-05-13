"""
portfolio_module.py — Statham Bot v6.0
Уровень 2: Личный крипто-портфель
Хранение: Redis  key=port:{uid}  type=Hash {coin: amount}
"""
from __future__ import annotations
import json, time
from redis_memory import _get

_PORT_TTL = 365 * 86400  # 1 год

# ── CRUD ───────────────────────────────────────────────────────────────────────
def port_add(uid: int, coin: str, amount: float) -> bool:
    r = _get()
    if not r: return False
    key = f"port:{uid}"
    try:
        existing = float(r.hget(key, coin) or 0)
        r.hset(key, coin, round(existing + amount, 8))
        r.expire(key, _PORT_TTL)
        return True
    except Exception: return False

def port_set(uid: int, coin: str, amount: float) -> bool:
    r = _get()
    if not r: return False
    key = f"port:{uid}"
    try:
        if amount <= 0:
            r.hdel(key, coin)
        else:
            r.hset(key, coin, round(amount, 8))
            r.expire(key, _PORT_TTL)
        return True
    except Exception: return False

def port_remove(uid: int, coin: str) -> bool:
    r = _get()
    if not r: return False
    try:
        r.hdel(f"port:{uid}", coin)
        return True
    except Exception: return False

def port_get(uid: int) -> dict:
    """Возвращает {coin_id: amount}"""
    r = _get()
    if not r: return {}
    try:
        raw = r.hgetall(f"port:{uid}") or {}
        return {k: float(v) for k, v in raw.items() if float(v) > 0}
    except Exception: return {}

def port_clear(uid: int):
    r = _get()
    if r:
        try: r.delete(f"port:{uid}")
        except Exception: pass

# ── ОЦЕНКА ПОРТФЕЛЯ ────────────────────────────────────────────────────────────
def format_portfolio(uid: int) -> str:
    from crypto_module import get_prices, COIN_ALIASES, _fmt_price
    holdings = port_get(uid)
    if not holdings:
        return (
            "💼 <b>Портфель пуст.</b>\n\n"
            "Добавь монеты:\n"
            "<code>/portfolio add btc 0.5</code>\n"
            "<code>/portfolio add eth 2</code>\n"
            "<code>/portfolio add sol 10</code>"
        )

    coins = list(holdings.keys())
    prices = get_prices(coins)
    if "_error" in prices:
        return "❌ Не удалось получить цены. Попробуй позже."

    total_usd = 0.0
    rows = []
    for coin_id, amount in holdings.items():
        d = prices.get(coin_id)
        if not d or "_error" in str(d):
            rows.append((coin_id.upper(), amount, None, None, None))
            continue
        value = amount * d["price"]
        total_usd += value
        rows.append((d["symbol"], amount, d["price"], value, d.get("change_24h")))

    # Сортируем по стоимости
    rows.sort(key=lambda x: x[3] or 0, reverse=True)

    msk = time.strftime("%H:%M", time.gmtime(time.time() + 3*3600))
    lines = [f"💼 <b>Мой портфель</b> • {msk} МСК\n"]

    for sym, amount, price, value, chg in rows:
        if value is None:
            lines.append(f"  • <b>{sym}</b>: {amount} (цена недоступна)")
            continue
        pct = f" {chg:+.1f}%" if chg is not None else ""
        # Процент от портфеля
        share = value / total_usd * 100 if total_usd > 0 else 0
        lines.append(
            f"  <b>{sym}</b>: {amount:g} × {_fmt_price(price)} = "
            f"<b>${value:,.2f}</b> ({share:.1f}%){pct}"
        )

    lines.append(f"\n💰 <b>Итого: ${total_usd:,.2f}</b>")
    lines.append("\n<i>Команды: /portfolio add btc 0.5 | /portfolio remove btc | /portfolio clear</i>")
    return "\n".join(lines)

# ── ПАРСЕР КОМАНДЫ ─────────────────────────────────────────────────────────────
def handle_portfolio_command(uid: int, args: list) -> str:
    """
    /portfolio                    → показать
    /portfolio add btc 0.5        → добавить
    /portfolio set btc 1.2        → установить точное количество
    /portfolio remove btc         → удалить монету
    /portfolio clear              → очистить всё
    """
    from crypto_module import COIN_ALIASES
    if not args or args[0] in ("show", "мой", ""):
        return format_portfolio(uid)

    cmd = args[0].lower()

    if cmd == "clear":
        port_clear(uid)
        return "🗑 Портфель очищен."

    if cmd in ("add", "set", "добавить", "установить"):
        if len(args) < 3:
            return "Использование: /portfolio add btc 0.5"
        coin = COIN_ALIASES.get(args[1].lower(), args[1].lower())
        try:
            amount = float(args[2].replace(",", "."))
        except ValueError:
            return "❌ Неверное количество. Пример: /portfolio add btc 0.5"
        if amount <= 0:
            return "❌ Количество должно быть больше 0."
        ok = port_set(uid, coin, amount) if cmd in ("set", "установить") else port_add(uid, coin, amount)
        action = "Установлено" if cmd in ("set", "установить") else "Добавлено"
        return f"✅ {action}: {amount:g} {coin.upper()}" if ok else "❌ Redis недоступен."

    if cmd in ("remove", "del", "удалить", "rm"):
        if len(args) < 2:
            return "Использование: /portfolio remove btc"
        coin = COIN_ALIASES.get(args[1].lower(), args[1].lower())
        port_remove(uid, coin)
        return f"✅ {coin.upper()} удалён из портфеля."

    return (
        "💼 <b>Команды портфеля:</b>\n"
        "/portfolio — показать\n"
        "/portfolio add btc 0.5 — добавить\n"
        "/portfolio set eth 2 — установить точно\n"
        "/portfolio remove sol — удалить\n"
        "/portfolio clear — очистить всё"
    )
