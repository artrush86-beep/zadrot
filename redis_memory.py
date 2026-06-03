"""
redis_memory.py — Statham Bot v5.0
Персистентная память через Redis (Railway Add-on)
"""
from __future__ import annotations
import json, os, time
from typing import Optional

try:
    import redis as _redis_lib
    _REDIS_OK = True
except ImportError:
    _REDIS_OK = False

_client = None

def _get():
    global _client
    if not _REDIS_OK:
        return None
    if _client is not None:
        try:
            _client.ping()
            return _client
        except Exception:
            _client = None
    url = (os.environ.get("REDIS_PRIVATE_URL")
           or os.environ.get("REDIS_URL", ""))
    if not url:
        return None
    try:
        _client = _redis_lib.from_url(
            url, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3
        )
        _client.ping()
        return _client
    except Exception as e:
        print(f"[REDIS] connect error: {e}")
        return None

def redis_ok():
    return _get() is not None

# ══ 1. ИСТОРИЯ ДИАЛОГА per-user ═══════════════════════════════════════════════
_HIST_TTL = 7 * 86400
_HIST_MAX = 20

def save_chat_history(uid, user_msg, bot_reply):
    r = _get()
    if not r: return False
    key = f"chat:{uid}"
    entry = json.dumps({"user_msg": user_msg[:500], "bot_reply": bot_reply[:500], "ts": int(time.time())})
    try:
        r.rpush(key, entry); r.ltrim(key, -_HIST_MAX, -1); r.expire(key, _HIST_TTL)
        return True
    except Exception: return False

def get_chat_history_r(uid, limit=5):
    r = _get()
    if not r: return []
    try:
        return [json.loads(i) for i in r.lrange(f"chat:{uid}", -limit, -1)]
    except Exception: return []

def get_chat_count_r(uid):
    r = _get()
    if not r: return 0
    try: return r.llen(f"chat:{uid}") or 0
    except Exception: return 0

def clear_chat_history_r(uid):
    r = _get()
    if r:
        try: r.delete(f"chat:{uid}")
        except Exception: pass

# ══ 2. ГЛОБАЛЬНЫЙ КОНТЕКСТ ЧАТА ═══════════════════════════════════════════════
_CTX_MAX = 30; _CTX_TTL = 3600

def add_global_ctx(name, text):
    r = _get()
    if not r: return
    entry = json.dumps({"n": name, "t": text[:200]})
    try:
        r.rpush("ctx", entry); r.ltrim("ctx", -_CTX_MAX, -1); r.expire("ctx", _CTX_TTL)
    except Exception: pass

def get_global_ctx(limit=10):
    r = _get()
    if not r: return ""
    try:
        items = r.lrange("ctx", -limit, -1)
        if not items: return ""
        lines = []
        for i in items:
            d = json.loads(i); lines.append(f"{d['n']}: {d['t']}")
        return "Последние сообщения в чате:\n" + "\n".join(lines)
    except Exception: return ""

# ══ 3. FLOOD TRACKING ══════════════════════════════════════════════════════════
_FL_MAX = 5; _FL_SECS = 10

def check_flood_r(uid):
    r = _get()
    if not r: return False
    key = f"fl:{uid}"; now = time.time()
    try:
        raw = r.get(key); ts = json.loads(raw) if raw else []
        ts = [t for t in ts if now - t < _FL_SECS]; ts.append(now)
        is_flood = len(ts) > _FL_MAX
        r.setex(key, _FL_SECS + 5, json.dumps(ts))
        return is_flood
    except Exception: return False

# ══ 4. КЭШ GROQ ════════════════════════════════════════════════════════════════
_GC_TTL = 3600

def get_groq_cache(key):
    r = _get()
    if not r: return None
    try: return r.get(f"gc:{key}")
    except Exception: return None

def set_groq_cache(key, val):
    r = _get()
    if not r: return
    try: r.setex(f"gc:{key}", _GC_TTL, val)
    except Exception: pass

# ══ 5. ПАМЯТЬ ПОЛЬЗОВАТЕЛЯ ═════════════════════════════════════════════════════
_MEM_TTL = 90 * 86400

def save_user_memory(uid, topic, value):
    r = _get()
    if not r: return False
    try:
        key = f"mem:{uid}"
        r.hset(key, topic.lower()[:50], value[:300]); r.expire(key, _MEM_TTL)
        return True
    except Exception: return False

def get_user_memory(uid):
    r = _get()
    if not r: return {}
    try: return r.hgetall(f"mem:{uid}") or {}
    except Exception: return {}

def get_user_memory_str(uid):
    mem = get_user_memory(uid)
    if not mem: return ""
    lines = [f"  • {k}: {v}" for k, v in list(mem.items())[:10]]
    return "Что я знаю об этом пользователе:\n" + "\n".join(lines)

def delete_user_memory(uid, topic):
    r = _get()
    if not r: return False
    try: r.hdel(f"mem:{uid}", topic.lower()); return True
    except Exception: return False

# ══ 6. КЭШ КРИПТО ══════════════════════════════════════════════════════════════
def set_crypto_prices(data, ttl=300):
    r = _get()
    if not r: return
    try: r.setex("cp", ttl, json.dumps(data))
    except Exception: pass

def get_crypto_prices():
    r = _get()
    if not r: return None
    try:
        raw = r.get("cp"); return json.loads(raw) if raw else None
    except Exception: return None

def set_crypto_news(data, ttl=3600):
    r = _get()
    if not r: return
    try: r.setex("cn", ttl, json.dumps(data))
    except Exception: pass

def get_crypto_news_cache():
    r = _get()
    if not r: return None
    try:
        raw = r.get("cn"); return json.loads(raw) if raw else None
    except Exception: return None

def mark_news_sent(news_id):
    r = _get()
    if not r: return
    try: r.setex(f"ns:{news_id}", 86400, "1")
    except Exception: pass

def is_news_sent(news_id):
    r = _get()
    if not r: return False
    try: return bool(r.exists(f"ns:{news_id}"))
    except Exception: return False

# ══ 7. PRICE ALERTS ════════════════════════════════════════════════════════════
def add_price_alert(uid, coin, target, direction):
    r = _get()
    if not r: return False
    key = f"alert:{uid}"
    try:
        existing = json.loads(r.get(key) or "[]")
        existing = [a for a in existing if a["coin"] != coin.upper()][:4]
        existing.append({"coin": coin.upper(), "target": float(target), "dir": direction, "ts": int(time.time())})
        r.setex(key, 30 * 86400, json.dumps(existing)); return True
    except Exception: return False

def get_all_alerts():
    r = _get()
    if not r: return []
    try:
        keys = r.keys("alert:*"); result = []
        for key in keys:
            uid = int(key.split(":")[1])
            for a in json.loads(r.get(key) or "[]"):
                a["uid"] = uid; result.append(a)
        return result
    except Exception: return []

def remove_alert(uid, coin):
    r = _get()
    if not r: return
    key = f"alert:{uid}"
    try:
        existing = [a for a in json.loads(r.get(key) or "[]") if a["coin"] != coin.upper()]
        if existing: r.setex(key, 30 * 86400, json.dumps(existing))
        else: r.delete(key)
    except Exception: pass

def get_user_alerts(uid):
    r = _get()
    if not r: return []
    try: return json.loads(r.get(f"alert:{uid}") or "[]")
    except Exception: return []

# ══ 8. RATE LIMIT ══════════════════════════════════════════════════════════════
def check_rate_limit(uid, cmd, max_calls=5, window=60):
    r = _get()
    if not r: return True
    key = f"rl:{cmd}:{uid}"
    try:
        n = r.incr(key)
        if n == 1: r.expire(key, window)
        return n <= max_calls
    except Exception: return True

# ══ 9. ТЕМА ЧАТА (2ч памяти) ═══════════════════════════════════════════════════
def get_chat_topic() -> str:
    r = _get()
    if not r: return ""
    try: return r.get("chat_topic") or ""
    except Exception: return ""

def set_chat_topic(topic: str):
    r = _get()
    if not r: return
    try: r.setex("chat_topic", 7200, topic[:50])
    except Exception: pass

def update_topic_keyword(word: str) -> int:
    """Счётчик упоминаний слова за 2ч. Возвращает текущее кол-во."""
    r = _get()
    if not r: return 0
    try:
        key = f"kw:{word.lower()[:20]}"
        n = int(r.incr(key))
        if n == 1: r.expire(key, 7200)
        return n
    except Exception: return 0

# ══ 10. СЧЁТЧИК СООБЩЕНИЙ ПОЛЬЗОВАТЕЛЯ (для обновления профиля) ════════════════
def incr_user_msg_count(uid: int) -> int:
    """Быстрый Redis-счётчик сообщений пользователя (30 дней TTL)."""
    r = _get()
    if not r: return 0
    key = f"umsg:{uid}"
    try:
        n = int(r.incr(key))
        if n == 1: r.expire(key, 30 * 86400)
        return n
    except Exception: return 0

# ══ ДИАГНОСТИКА ════════════════════════════════════════════════════════════════
def redis_stats():
    r = _get()
    if not r: return {"status": "❌ Redis не подключён"}
    try:
        info = r.info("memory")
        return {"status": "✅ Подключён", "keys": r.dbsize(), "memory": info.get("used_memory_human", "?")}
    except Exception as e: return {"status": f"⚠️ Ошибка: {e}"}
