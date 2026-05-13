"""
game_module.py — Statham Bot v6.0
Уровень 4: Геймификация
  - /predict btc up/down — ставки на направление
  - /dailyvote — ежедневный вопрос (кнопки)
  - Достижения (ачивки) в Redis
  - Крипто-ранги (Hodler→Whale→Satoshi)
  - /tournament — еженедельный топ предсказателей
"""
from __future__ import annotations
import json, time, random
from redis_memory import _get

# ══ КРИПТО-РАНГИ (заменяют стандартные) ══════════════════════════════════════
CRYPTO_LEVEL_NAMES = {
    1: "🌱 Hodler",
    2: "📊 Trader",
    3: "🐳 Whale",
    4: "🔱 Satoshi",
    5: "👑 Nakamoto",
}

CRYPTO_LEVEL_EMOJIS = {
    1: "🌱", 2: "📊", 3: "🐳", 4: "🔱", 5: "👑"
}

# ══ ДОСТИЖЕНИЯ ════════════════════════════════════════════════════════════════
ACHIEVEMENTS = {
    "first_price":    {"emoji": "📊", "name": "Первый запрос цены",    "xp": 5},
    "first_alert":    {"emoji": "🔔", "name": "Первый ценовой алерт",  "xp": 10},
    "alert_fired":    {"emoji": "🦈", "name": "Алерт сработал",        "xp": 20},
    "first_predict":  {"emoji": "🔮", "name": "Первое предсказание",   "xp": 10},
    "predict_win":    {"emoji": "🎯", "name": "Угадал направление",    "xp": 25},
    "predict_5":      {"emoji": "🌟", "name": "5 угаданных подряд",    "xp": 100},
    "first_portfolio":{"emoji": "💼", "name": "Создал портфель",       "xp": 15},
    "hodler":         {"emoji": "💎", "name": "Diamond Hands (30 дней)", "xp": 50},
    "first_fear":     {"emoji": "😱", "name": "Первый F&G запрос",     "xp": 5},
    "degen":          {"emoji": "🎲", "name": "Сделал 10 предсказаний", "xp": 30},
    "whale":          {"emoji": "🐳", "name": "Достиг уровня Whale",   "xp": 0},
}

def give_achievement(uid: int, achievement_id: str) -> bool:
    """Выдаёт ачивку если ещё не выдана. Возвращает True если новая."""
    r = _get()
    if not r: return False
    key = f"ach:{uid}"
    try:
        if r.hexists(key, achievement_id):
            return False  # уже есть
        r.hset(key, achievement_id, int(time.time()))
        r.expire(key, 365 * 86400)
        return True
    except Exception: return False

def get_achievements(uid: int) -> dict:
    r = _get()
    if not r: return {}
    try: return r.hgetall(f"ach:{uid}") or {}
    except Exception: return {}

def format_achievements(uid: int) -> str:
    owned_keys = set(get_achievements(uid).keys())
    if not owned_keys:
        return "🏅 Пока нет достижений. Начни с /price, /predict или /portfolio!"
    lines = ["🏅 <b>Твои достижения:</b>\n"]
    for aid, info in ACHIEVEMENTS.items():
        if aid in owned_keys:
            lines.append(f"  {info['emoji']} {info['name']} (+{info['xp']} XP)")
    missing = len(ACHIEVEMENTS) - len(owned_keys)
    if missing > 0:
        lines.append(f"\n🔒 Ещё {missing} не открыто")
    return "\n".join(lines)


# ══ ПРЕДСКАЗАНИЯ /predict ══════════════════════════════════════════════════════
_PRED_TTL = 4 * 3600  # 4 часа на результат

def make_prediction(uid: int, name: str, coin: str, direction: str,
                    current_price: float) -> bool:
    """direction: 'up' | 'down'"""
    r = _get()
    if not r: return False
    key = f"pred:{uid}:{coin}"
    # Только одна ставка на монету за раз
    if r.exists(key): return False
    data = {
        "uid": uid, "name": name, "coin": coin,
        "direction": direction, "price_at": current_price,
        "ts": int(time.time()),
    }
    try:
        r.setex(key, _PRED_TTL, json.dumps(data))
        # Добавляем в общий список ставок
        r.rpush("pred:all", json.dumps(data))
        r.expire("pred:all", _PRED_TTL + 3600)
        return True
    except Exception: return False

def get_active_predictions() -> list[dict]:
    r = _get()
    if not r: return []
    try:
        items = r.lrange("pred:all", 0, -1)
        result = []
        for raw in items:
            try:
                d = json.loads(raw)
                # Проверяем что ставка ещё активна
                if r.exists(f"pred:{d['uid']}:{d['coin']}"):
                    result.append(d)
            except Exception: pass
        return result
    except Exception: return []

def resolve_predictions(coin: str, new_price: float) -> list[dict]:
    """Определяет результаты ставок на монету. Возвращает список результатов."""
    r = _get()
    if not r: return []
    preds = get_active_predictions()
    results = []
    for p in preds:
        if p["coin"].lower() != coin.lower(): continue
        old_price = p["price_at"]
        if new_price > old_price:
            actual = "up"
        elif new_price < old_price:
            actual = "down"
        else:
            actual = "flat"
        won = (p["direction"] == actual) or (actual == "flat")
        results.append({**p, "new_price": new_price, "actual": actual, "won": won})
        # Удаляем ставку
        try: r.delete(f"pred:{p['uid']}:{coin.lower()}")
        except Exception: pass
    return results

def save_predict_stats(uid: int, won: bool):
    """Обновляет счётчик побед/поражений."""
    r = _get()
    if not r: return
    key = f"predstat:{uid}"
    try:
        r.hincrby(key, "total", 1)
        if won: r.hincrby(key, "wins", 1)
        else:   r.hincrby(key, "streak_loss", 1); r.hset(key, "streak", 0)
        if won:
            streak = int(r.hget(key, "streak") or 0) + 1
            r.hset(key, "streak", streak)
            r.hset(key, "streak_loss", 0)
        r.expire(key, 365 * 86400)
    except Exception: pass

def get_predict_stats(uid: int) -> dict:
    r = _get()
    if not r: return {}
    try:
        raw = r.hgetall(f"predstat:{uid}") or {}
        total = int(raw.get("total", 0))
        wins  = int(raw.get("wins", 0))
        return {
            "total": total, "wins": wins,
            "losses": total - wins,
            "winrate": round(wins / total * 100) if total > 0 else 0,
            "streak": int(raw.get("streak", 0)),
        }
    except Exception: return {}

def format_predict_stats(uid: int, name: str) -> str:
    s = get_predict_stats(uid)
    if not s or s.get("total", 0) == 0:
        return f"🔮 {name}, ты ещё не делал предсказаний.\n\nПопробуй: /predict btc up"
    wr = s["winrate"]
    medal = "🥇" if wr >= 70 else "🥈" if wr >= 50 else "🥉"
    return (
        f"🔮 <b>Статистика предсказаний — {name}</b>\n\n"
        f"Всего ставок: <b>{s['total']}</b>\n"
        f"Побед: <b>{s['wins']}</b> / Поражений: <b>{s['losses']}</b>\n"
        f"{medal} Точность: <b>{wr}%</b>\n"
        f"🔥 Серия побед: <b>{s['streak']}</b>"
    )

def get_prediction_leaderboard(top_n: int = 10) -> list[dict]:
    """Топ предсказателей (нужно отдельно хранить список uid'ов)."""
    r = _get()
    if not r: return []
    try:
        # Берём всех кто когда-либо делал ставки
        keys = r.keys("predstat:*")
        board = []
        for key in keys:
            uid = int(key.split(":")[1])
            raw = r.hgetall(key) or {}
            total = int(raw.get("total", 0))
            wins  = int(raw.get("wins", 0))
            if total >= 3:  # минимум 3 ставки для рейтинга
                board.append({"uid": uid, "total": total, "wins": wins,
                              "winrate": round(wins / total * 100)})
        return sorted(board, key=lambda x: (x["winrate"], x["total"]), reverse=True)[:top_n]
    except Exception: return []


# ══ ЕЖЕДНЕВНОЕ ГОЛОСОВАНИЕ /dailyvote ══════════════════════════════════════════
_DAILY_QUESTIONS = [
    {"text": "₿ BTC достигнет $100,000 до конца 2025?", "options": ["Да 🚀", "Нет 📉", "Уже было 😏"]},
    {"text": "Что вырастет больше в этом месяце?",       "options": ["BTC 🟠", "ETH 🔷", "SOL 🟣", "Альты 🎰"]},
    {"text": "Как думаешь, когда следующий ATH BTC?",     "options": ["В 2025 🔥", "В 2026 📅", "После 2026 😴"]},
    {"text": "Какой альт самый перспективный сейчас?",    "options": ["ETH 🔷", "SOL 🟣", "TON 💎", "BNB 🟡"]},
    {"text": "ФРС снизит ставку в следующий раз?",        "options": ["Да 📉", "Нет ✋", "Без изменений 😐"]},
    {"text": "Индекс страха/жадности через неделю?",      "options": ["Страх 😨", "Нейтрально 😐", "Жадность 🤑"]},
    {"text": "Биткоин — цифровое золото или спекулятив?", "options": ["Золото 🥇", "Спекуляция 🎲", "И то и другое 🤷"]},
    {"text": "DeFi или CEX — что победит?",               "options": ["DeFi 🏗", "CEX 🏦", "Оба выживут 🤝"]},
]

def get_daily_question() -> dict:
    """Возвращает вопрос дня (меняется каждый день)."""
    day_of_year = int(time.strftime("%j"))
    return _DAILY_QUESTIONS[day_of_year % len(_DAILY_QUESTIONS)]

def vote(uid: int, question_id: str, option: int) -> tuple[bool, str]:
    """
    Записывает голос. Возвращает (успех, сообщение).
    question_id: дата "2025-05-14"
    """
    r = _get()
    if not r: return False, "Redis недоступен."
    vote_key  = f"vote:{question_id}:{uid}"
    count_key = f"votecnt:{question_id}"
    if r.exists(vote_key):
        return False, "Ты уже голосовал сегодня!"
    try:
        r.setex(vote_key, 25 * 3600, str(option))  # 25ч — чуть больше суток
        r.hincrby(count_key, str(option), 1)
        r.expire(count_key, 25 * 3600)
        return True, "✅ Голос принят!"
    except Exception: return False, "Ошибка сохранения."

def get_vote_results(question_id: str, options: list) -> dict:
    """Возвращает {option_idx: count}"""
    r = _get()
    if not r: return {}
    try:
        raw = r.hgetall(f"votecnt:{question_id}") or {}
        total = sum(int(v) for v in raw.values())
        result = {"total": total, "options": {}}
        for i, opt in enumerate(options):
            cnt = int(raw.get(str(i), 0))
            pct = round(cnt / total * 100) if total > 0 else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            result["options"][i] = {"text": opt, "count": cnt, "pct": pct, "bar": bar}
        return result
    except Exception: return {}

def format_vote_results(question_id: str, question_text: str, options: list) -> str:
    data = get_vote_results(question_id, options)
    if not data or data.get("total", 0) == 0:
        return "📊 Пока никто не голосовал."
    lines = [f"📊 <b>Результаты голосования</b>\n{question_text}\n"]
    for i, info in data["options"].items():
        lines.append(
            f"{info['text']}\n"
            f"<code>[{info['bar']}]</code> {info['pct']}% ({info['count']})"
        )
    lines.append(f"\n👥 Всего голосов: <b>{data['total']}</b>")
    return "\n".join(lines)
