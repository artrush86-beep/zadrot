"""
Statham Moderation Bot v4.0 — Railway
=======================================================
Что нового в v4.0 (Railway):
  🚂 ПЕРЕНОС: PythonAnywhere → Railway (больше никаких ProxyError 503!)
  ✅ УДАЛЕНО: Прокси для Telegram и Groq (на Railway не нужен)
  ✅ ИСПРАВЛЕНО: BASE_DIR теперь через переменную DATA_DIR
  ✅ ИСПРАВЛЕНО: PORT через переменную окружения Railway
  ✅ ИСПРАВЛЕНО: RAILWAY_DOMAIN вместо PA_DOMAIN
  ⏰ НОВОЕ: APScheduler — встроенный cron (не нужно настраивать Railway Cron Jobs)
     - 08:00 МСК — утренний пост
     - 12:00 МСК — факт дня
     - 23:00 МСК — ночной пост

Что было в v3.3 (PythonAnywhere):
  ✅ Groq AI через прокси, модель llama-3.3-70b-versatile
  🤖 Команды /ai, /ask, !ai
  💬 12% шанс случайного AI-ответа
  🎮 Мини-игры: /roll, /coin, /fact, /quiz
  📝 Персонализация: /remember, /myfacts
"""
from __future__ import annotations
import json, os, re, time, threading, datetime, random, sqlite3, hashlib, signal, sys
from flask import Flask, request
import requests
import telebot
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════
TOKEN     = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID",  "")
PA_DOMAIN = os.environ.get("RAILWAY_DOMAIN", os.environ.get("PA_DOMAIN", ""))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")  # 🔑 Получить: https://console.groq.com/keys

if not TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не задан! Укажите его в переменных окружения.")

ADMIN_IDS: set[int] = set(
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
)

RULES_TEXT = os.environ.get("RULES_TEXT", (
    "📋 <b>Правила чата Statham Elite</b>\n\n"
    "1️⃣ Уважайте друг друга\n"
    "2️⃣ Запрещён мат и оскорбления\n"
    "3️⃣ Запрещён спам и флуд\n"
    "4️⃣ Реклама только с разрешения админа\n"
    "5️⃣ Офтоп — в личку\n\n"
    "3 нарушения = мут. Не испытывай судьбу 😏"
))

BASE_DIR   = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
os.makedirs(BASE_DIR, exist_ok=True)
DB_PATH    = os.path.join(BASE_DIR, "bot.db")
LOG_FILE   = os.path.join(BASE_DIR, "mod_log.txt")
PHOTO_PATH = os.path.join(BASE_DIR, "helloboys.png")
CONFIG_FILE = os.path.join(BASE_DIR, "bot_config.json")

# ── Fallback: config file (если нет UI для переменных окружения) ─────────────
def _load_config():
    """Загружает конфиг из файла если env vars недоступны."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

_config = _load_config()

# Переопределяем переменные окружения значениями из конфига если они есть
TOKEN = os.environ.get("BOT_TOKEN") or _config.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID") or _config.get("CHAT_ID", "")
PA_DOMAIN = os.environ.get("RAILWAY_DOMAIN", os.environ.get("PA_DOMAIN")) or _config.get("PA_DOMAIN", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or _config.get("GROQ_API_KEY", "")

# Прогрессивные муты: суммарные варны → длительность мута в минутах
MUTE_STEPS = [(3, 1), (6, 10), (9, 60), (12, 1440)]  # (порог, минуты)

# Антифлуд: макс сообщений за окно времени
FLOOD_MAX   = 5    # сообщений
FLOOD_SECS  = 10   # за N секунд
FLOOD_MUTE  = 5    # мут в минутах

bot = telebot.TeleBot(TOKEN, threaded=False)

# ✅ Railway: прокси не нужен — прямой доступ к Telegram API

# ══════════════════════════════════════════════════════════════════════════════
# RETRY ДЕКОРАТОР ДЛЯ НЕСТАБИЛЬНОГО ПРОКСИ
# ══════════════════════════════════════════════════════════════════════════════
def _with_retry(max_retries=5, base_delay=2, max_delay=30):
    """
    Декоратор с экспоненциальным backoff для функций с сетевыми запросами.
    На PA free tier прокси часто возвращает 503 — нужны повторные попытки.
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()
                    # Ретри только для сетевых ошибок
                    if any(x in error_str for x in ["proxy", "503", "timeout", "connection", "max retries"]):
                        if attempt < max_retries:
                            write_log(f"RETRY | {func.__name__} | attempt={attempt}/{max_retries} | delay={delay}s | error={type(e).__name__}")
                            time.sleep(delay)
                            delay = min(delay * 2, max_delay)  # экспоненциальный backoff
                        continue
                    else:
                        raise  # Другие ошибки не ретраим
            # Все попытки исчерпаны
            write_log(f"RETRY_FAIL | {func.__name__} | all {max_retries} attempts failed | {type(last_exception).__name__}")
            raise last_exception
        return wrapper
    return decorator

# ══════════════════════════════════════════════════════════════════════════════
# GROQ AI CLIENT (Бесплатный API)
# ══════════════════════════════════════════════════════════════════════════════
class GroqAI:
    """Бесплатный AI через Groq API — llama-3.1-70b, быстрый ответ."""

    API_URL = "https://api.groq.com/openai/v1/chat/completions"
    MODEL = "llama-3.3-70b-versatile"  # быстрый и умный

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._cache = {}  # кэш ответов для экономии CPU
        self._cache_ttl = 3600  # кэш 1 час

    def _get_cache_key(self, prompt: str) -> str:
        return hashlib.md5(prompt.lower().strip().encode()).hexdigest()

    def _get_cached(self, prompt: str) -> str | None:
        key = self._get_cache_key(prompt)
        if key in self._cache:
            result, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return result
            del self._cache[key]
        return None

    def _save_cache(self, prompt: str, response: str):
        key = self._get_cache_key(prompt)
        self._cache[key] = (response, time.time())
        # Очищаем старый кэш
        now = time.time()
        self._cache = {k: v for k, v in self._cache.items() if now - v[1] < self._cache_ttl}

    def ask(self, user_message: str, user_name: str = "", context: str = "",
            history: list[dict] = None, user_id: int = None) -> str | None:
        """Отправляет сообщение в Groq API, возвращает ответ.

        Args:
            user_message: Сообщение пользователя
            user_name: Имя пользователя
            context: Дополнительный контекст
            history: История диалога (список словарей с user_msg и bot_reply)
            user_id: ID пользователя для сохранения в историю
        """
        if not self.api_key:
            return None

        # Проверяем кэш (только если нет истории)
        if not history:
            cache_key = f"{user_name}:{user_message}"
            cached = self._get_cached(cache_key)
            if cached:
                return cached

        # Определяем "тесноту" общения
        chat_depth = len(history) if history else 0
        is_close_chat = chat_depth >= 3  # Если 3+ обмена — это плотное общение

        system_prompt = (
            "Ты — дружелюбный бот Statham в Telegram-чате Statham Elite. "
            "Это чат для общения на разные темы: программирование, жизнь, юмор. "
            "Отвечай кратко (1-2 предложения), с юмором, на русском. "
            "Ты модератор, но дружелюбный. Используй эмодзи. "
            "Не пиши длинных текстов — чат, а не эссе. "
            "Если тебя спрашивают про код — помогай с кодом. "
            "Если спрашивают совет — давай полезный совет."
        )

        # Если плотное общение — добавляем это в промпт
        if is_close_chat:
            system_prompt += (
                f" Это твой {chat_depth}-й разговор с этим пользователем — "
                "ты уже знаком, можешь быть более дружелюбным и отходить от роли модератора."
            )

        messages = [{"role": "system", "content": system_prompt}]

        if context:
            messages.append({"role": "user", "content": f"Контекст: {context}"})

        # Добавляем историю диалога
        if history:
            for h in history[-5:]:  # последние 5 обменов
                messages.append({"role": "user", "content": h["user_msg"]})
                messages.append({"role": "assistant", "content": h["bot_reply"]})

        messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

        try:
            # ✅ Railway: прокси не нужен
            proxies = None
            r = requests.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.MODEL,
                    "messages": messages,
                    "temperature": 0.8 if is_close_chat else 0.7,
                    "max_tokens": 200 if is_close_chat else 150,
                    "top_p": 1
                },
                timeout=15,
                proxies=proxies
            )
            data = r.json()

            if "choices" in data and len(data["choices"]) > 0:
                answer = data["choices"][0]["message"]["content"].strip()

                # Сохраняем в историю
                if user_id:
                    save_chat_message(user_id, user_message, answer)

                # Кэшируем только простые ответы
                if not history and not user_id:
                    self._save_cache(f"{user_name}:{user_message}", answer)

                return answer

            return None
        except Exception as e:
            write_log(f"GROQ_ERR | {type(e).__name__} | {e}")
            return None

    def generate_greeting(self, user_name: str, is_morning: bool = False) -> str:
        """Генерирует персональное приветствие."""
        if not self.api_key:
            return None

        prompt = f"Приветствуй пользователя {user_name} в чате. "
        if is_morning:
            prompt += "Утреннее приветствие с пожеланием доброго дня. "
        prompt += "Кратко, с эмодзи, дружелюбно."

        return self.ask(prompt, "")

# Инициализация AI (если ключ задан)
ai = GroqAI(GROQ_API_KEY)

# ══════════════════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ
# ══════════════════════════════════════════════════════════════════════════════
_log_lock = threading.Lock()

def write_log(entry: str):
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        with _log_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {entry}\n")
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > 1000:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines[-1000:])
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════════
# БАЗА ДАННЫХ SQLite
# ══════════════════════════════════════════════════════════════════════════════
_db_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _db_lock:
        conn = get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                first_name  TEXT    DEFAULT '',
                username    TEXT    DEFAULT '',
                msg_count   INTEGER DEFAULT 0,
                first_seen  INTEGER DEFAULT 0,
                last_seen   INTEGER DEFAULT 0,
                first_seen_dt TEXT  DEFAULT '',
                last_seen_dt  TEXT  DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS usernames (
                user_id  INTEGER NOT NULL,
                username TEXT    NOT NULL,
                UNIQUE(user_id, username)
            );

            CREATE TABLE IF NOT EXISTS warns (
                user_id       INTEGER PRIMARY KEY,
                count         INTEGER DEFAULT 0,
                total_warns   INTEGER DEFAULT 0,
                last_warn_ts  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS flood_track (
                user_id   INTEGER PRIMARY KEY,
                timestamps TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS reports (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id    INTEGER,
                target_id  INTEGER,
                msg_text   TEXT,
                ts         INTEGER,
                resolved   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS user_facts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                fact_type  TEXT    DEFAULT 'general',
                fact       TEXT    NOT NULL,
                added_ts   INTEGER DEFAULT 0,
                UNIQUE(user_id, fact_type)
            );

            CREATE TABLE IF NOT EXISTS chat_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                user_msg   TEXT    NOT NULL,
                bot_reply  TEXT    NOT NULL,
                ts         INTEGER DEFAULT 0
            );
        """)
        conn.commit()
        conn.close()

# ── Пользователи ──────────────────────────────────────────────────────────────
def record_user(user) -> None:
    if not user or getattr(user, "is_bot", False):
        return
    uid   = user.id
    now   = int(time.time())
    now_s = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    uname = (getattr(user, "username", "") or "").strip()
    fname = (getattr(user, "first_name", "") or "").strip()

    with _db_lock:
        conn = get_db()
        try:
            row = conn.execute("SELECT user_id, first_seen FROM users WHERE user_id=?", (uid,)).fetchone()
            if row:
                conn.execute("""
                    UPDATE users SET first_name=?, username=?, last_seen=?, last_seen_dt=?,
                                     msg_count = msg_count + 1
                    WHERE user_id=?
                """, (fname, uname, now, now_s, uid))
            else:
                conn.execute("""
                    INSERT INTO users (user_id, first_name, username, msg_count,
                                       first_seen, last_seen, first_seen_dt, last_seen_dt)
                    VALUES (?,?,?,1,?,?,?,?)
                """, (uid, fname, uname, now, now, now_s, now_s))
            if uname:
                conn.execute("INSERT OR IGNORE INTO usernames (user_id, username) VALUES (?,?)",
                             (uid, uname))
            conn.commit()
        finally:
            conn.close()

def get_user(uid: int) -> dict | None:
    with _db_lock:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            if not row:
                return None
            data = dict(row)
            names = conn.execute("SELECT username FROM usernames WHERE user_id=?",
                                 (uid,)).fetchall()
            data["all_usernames"] = [r["username"] for r in names]
            return data
        finally:
            conn.close()

def find_user_by_query(query: str) -> dict | None:
    """Поиск по username или user_id."""
    with _db_lock:
        conn = get_db()
        try:
            q = query.lstrip("@").lower()
            if q.isdigit():
                row = conn.execute("SELECT * FROM users WHERE user_id=?", (int(q),)).fetchone()
            else:
                row = conn.execute("""
                    SELECT u.* FROM users u
                    JOIN usernames un ON u.user_id = un.user_id
                    WHERE LOWER(un.username)=?
                """, (q,)).fetchone()
            if not row:
                return None
            data = dict(row)
            names = conn.execute("SELECT username FROM usernames WHERE user_id=?",
                                 (data["user_id"],)).fetchall()
            data["all_usernames"] = [r["username"] for r in names]
            return data
        finally:
            conn.close()

def get_top_users(limit: int = 10) -> list[dict]:
    """Топ участников по количеству сообщений."""
    with _db_lock:
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT first_name, username, msg_count, user_id
                FROM users ORDER BY msg_count DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

# ── Предупреждения ────────────────────────────────────────────────────────────
def _auto_reset_warns(uid: int) -> int:
    """Сбрасывает варны если прошло >24 часов с последнего нарушения."""
    with _db_lock:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM warns WHERE user_id=?", (uid,)).fetchone()
            if not row:
                return 0
            if row["count"] > 0 and (int(time.time()) - row["last_warn_ts"]) > 86400:
                conn.execute("UPDATE warns SET count=0 WHERE user_id=?", (uid,))
                conn.commit()
                return 0
            return row["count"]
        finally:
            conn.close()

def add_warn(uid: int) -> tuple[int, int]:
    """Добавляет варн. Возвращает (текущие варны, всего варнов)."""
    _auto_reset_warns(uid)
    now = int(time.time())
    with _db_lock:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM warns WHERE user_id=?", (uid,)).fetchone()
            if row:
                new_cnt   = row["count"] + 1
                new_total = row["total_warns"] + 1
                conn.execute("""
                    UPDATE warns SET count=?, total_warns=?, last_warn_ts=?
                    WHERE user_id=?
                """, (new_cnt, new_total, now, uid))
            else:
                new_cnt = new_total = 1
                conn.execute("""
                    INSERT INTO warns (user_id, count, total_warns, last_warn_ts)
                    VALUES (?,1,1,?)
                """, (uid, now))
            conn.commit()
            return new_cnt, new_total
        finally:
            conn.close()

def reset_warns(uid: int):
    with _db_lock:
        conn = get_db()
        try:
            conn.execute("UPDATE warns SET count=0 WHERE user_id=?", (uid,))
            conn.commit()
        finally:
            conn.close()

def get_warns(uid: int) -> int:
    return _auto_reset_warns(uid)

def get_all_warns() -> list[dict]:
    with _db_lock:
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT w.user_id, w.count, w.total_warns, u.first_name, u.username
                FROM warns w
                LEFT JOIN users u ON w.user_id = u.user_id
                WHERE w.count > 0
                ORDER BY w.count DESC
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

def get_mute_duration(current_warns: int) -> int:
    for threshold, minutes in sorted(MUTE_STEPS, reverse=True):
        if current_warns >= threshold:
            return minutes
    return 1

# ── Факты о пользователях ────────────────────────────────────────────────────
def save_user_fact(uid: int, fact: str, fact_type: str = "general") -> bool:
    """Сохраняет факт о пользователе."""
    now = int(time.time())
    with _db_lock:
        conn = get_db()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO user_facts (user_id, fact_type, fact, added_ts)
                VALUES (?, ?, ?, ?)
            """, (uid, fact_type, fact.strip(), now))
            conn.commit()
            return True
        except Exception as e:
            write_log(f"FACT_SAVE_ERR | {e}")
            return False
        finally:
            conn.close()

def get_user_facts(uid: int, fact_type: str = None) -> list[dict]:
    """Получает факты о пользователе."""
    with _db_lock:
        conn = get_db()
        try:
            if fact_type:
                rows = conn.execute("""
                    SELECT fact_type, fact, added_ts FROM user_facts
                    WHERE user_id = ? AND fact_type = ?
                    ORDER BY added_ts DESC
                """, (uid, fact_type)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT fact_type, fact, added_ts FROM user_facts
                    WHERE user_id = ?
                    ORDER BY added_ts DESC
                """, (uid,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

def get_personalized_greeting(uid: int, name: str) -> str | None:
    """Генерирует персональное приветствие с фактами о пользователе."""
    facts = get_user_facts(uid)
    if not facts:
        return None

    # Выбираем случайный факт для упоминания
    fact = random.choice(facts)
    fact_text = fact["fact"]

    greetings_with_facts = [
        f"Привет, <b>{name}</b>! Кстати, я помню про {fact_text} 😊",
        f"Здорово, <b>{name}</b>! Как там {fact_text}?",
        f"Салют, <b>{name}</b>! Надеюсь, всё ещё {fact_text}? 🎉",
        f"Йоу, <b>{name}</b>! Как жизнь? {fact_text}?",
    ]
    return random.choice(greetings_with_facts)

# ── История диалогов (для плотного общения) ─────────────────────────────────
def save_chat_message(uid: int, user_msg: str, bot_reply: str):
    """Сохраняет сообщение в историю диалога."""
    now = int(time.time())
    with _db_lock:
        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO chat_history (user_id, user_msg, bot_reply, ts)
                VALUES (?, ?, ?, ?)
            """, (uid, user_msg[:500], bot_reply[:500], now))
            conn.commit()
        finally:
            conn.close()

def get_chat_history(uid: int, limit: int = 5) -> list[dict]:
    """Получает последние сообщения из истории диалога с пользователем."""
    with _db_lock:
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT user_msg, bot_reply, ts FROM chat_history
                WHERE user_id = ?
                ORDER BY ts DESC
                LIMIT ?
            """, (uid, limit)).fetchall()
            return [dict(r) for r in reversed(rows)]  # старое -> новое
        finally:
            conn.close()

def get_user_chat_count(uid: int) -> int:
    """Сколько сообщений обменяно с пользователем."""
    with _db_lock:
        conn = get_db()
        try:
            row = conn.execute("""
                SELECT COUNT(*) FROM chat_history WHERE user_id = ?
            """, (uid,)).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

# ── Антифлуд ──────────────────────────────────────────────────────────────────
def check_flood(uid: int) -> bool:
    """True если пользователь флудит."""
    now = time.time()
    with _db_lock:
        conn = get_db()
        try:
            row = conn.execute("SELECT timestamps FROM flood_track WHERE user_id=?",
                               (uid,)).fetchone()
            ts_list = json.loads(row["timestamps"]) if row else []
            ts_list = [t for t in ts_list if now - t < FLOOD_SECS]
            ts_list.append(now)
            is_flood = len(ts_list) > FLOOD_MAX
            conn.execute("""
                INSERT OR REPLACE INTO flood_track (user_id, timestamps)
                VALUES (?,?)
            """, (uid, json.dumps(ts_list)))
            conn.commit()
            return is_flood
        finally:
            conn.close()

# ── Репорты ───────────────────────────────────────────────────────────────────
def save_report(from_id: int, target_id: int, msg_text: str):
    with _db_lock:
        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO reports (from_id, target_id, msg_text, ts)
                VALUES (?,?,?,?)
            """, (from_id, target_id, msg_text, int(time.time())))
            conn.commit()
        finally:
            conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ══════════════════════════════════════════════════════════════════════════════
@_with_retry(max_retries=3, base_delay=1)
def _get_chat_admins(chat_id):
    """Обёртка для получения админов с retry."""
    return bot.get_chat_administrators(chat_id)

def is_admin(chat_id, user_id: int) -> bool:
    if ADMIN_IDS and user_id in ADMIN_IDS:
        return True
    try:
        admins = _get_chat_admins(chat_id)
        return user_id in [a.user.id for a in admins]
    except Exception as e:
        write_log(f"IS_ADMIN_ERR | {type(e).__name__} | {e}")
        return False

@_with_retry(max_retries=3, base_delay=1)
def _send_message_with_retry(chat_id, text, **kwargs):
    """Отправка сообщения с retry при ошибках прокси."""
    return bot.send_message(chat_id, text, **kwargs)

def _reply(m, text: str, **kwargs):
    kw = {"parse_mode": "HTML", **kwargs}
    if getattr(m, "message_thread_id", None):
        kw["message_thread_id"] = m.message_thread_id
    try:
        _send_message_with_retry(m.chat.id, text, **kw)
    except Exception as e:
        write_log(f"REPLY_ERR | {type(e).__name__} | {e}")

@_with_retry(max_retries=2, base_delay=1)
def _send_admin_message(admin_id, text):
    """Отправка сообщения админу с retry."""
    bot.send_message(admin_id, text, parse_mode="HTML")

def _notify_admins(chat_id, text: str):
    """Отправить личное сообщение всем администраторам из ADMIN_IDS."""
    for admin_id in ADMIN_IDS:
        try:
            _send_admin_message(admin_id, text)
        except Exception as e:
            write_log(f"NOTIFY_ADMIN_ERR | admin={admin_id} | {type(e).__name__}")

@_with_retry(max_retries=3, base_delay=1)
def _reply_to_with_retry(message, text, **kwargs):
    """Reply с retry при ошибках прокси."""
    return bot.reply_to(message, text, **kwargs)

@_with_retry(max_retries=3, base_delay=1)
def _send_message_simple(chat_id, text, **kwargs):
    """Простая отправка сообщения с retry."""
    return bot.send_message(chat_id, text, **kwargs)

@_with_retry(max_retries=3, base_delay=1)
def _mute(chat_id, user_id: int, minutes: int):
    perms = telebot.types.ChatPermissions(
        can_send_messages=False, can_send_media_messages=False,
        can_send_other_messages=False, can_add_web_page_previews=False,
    )
    bot.restrict_chat_member(chat_id, user_id,
                             until_date=int(time.time() + minutes * 60),
                             permissions=perms)

@_with_retry(max_retries=3, base_delay=1)
def _unmute(chat_id, user_id: int):
    perms = telebot.types.ChatPermissions(
        can_send_messages=True, can_send_media_messages=True,
        can_send_other_messages=True, can_add_web_page_previews=True,
    )
    bot.restrict_chat_member(chat_id, user_id, permissions=perms)

def format_duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} мин"
    if minutes < 1440:
        return f"{minutes // 60} ч"
    return f"{minutes // 1440} дн"

# ══════════════════════════════════════════════════════════════════════════════
# ФИЛЬТРЫ
# ══════════════════════════════════════════════════════════════════════════════
BAD_WORDS = [
    "бля","блять","блядь","хуй","хуйня","охуел","похуй","пизда","пиздец",
    "ебать","ебаный","заебал","мудак","гондон","шлюха","сука","тварь","мразь",
    "урод","дебил","чмо","пидор","гнида","жопа","залупа","падла","хуйло",
    "пиздобол","хуесос","ублюдок",
]

_BAD_PATTERNS = [re.compile(r'(?<![а-яёА-ЯЁa-zA-Z])' + re.escape(w), re.IGNORECASE)
                 for w in BAD_WORDS]

def contains_bad_word(text: str) -> str | None:
    """Возвращает найденное плохое слово или None."""
    for pattern, word in zip(_BAD_PATTERNS, BAD_WORDS):
        if pattern.search(text):
            return word
    return None

GREETINGS = {
    "привет","здравствуй","здравствуйте","добрый","хай",
    "hello","hi","hey","салам","ку","йо","sup","хелло","здарово",
}

GREETING_REPLIES = [
    "Привет, <b>{name}</b>! 👋",
    "Йоу, <b>{name}</b>! 🤙",
    "Здорово, <b>{name}</b>! 💪",
    "Салют, <b>{name}</b>! 🚀",
    "О, <b>{name}</b>! Рад видеть! 😎",
    "Приветик, <b>{name}</b>! ✌️",
    "Ку, <b>{name}</b>! Как жизнь? 😄",
]

# ── Ключевые слова для обращений к боту ───────────────────────────────────────
BOT_TRIGGERS = ["бот,", "@statham", "statham,", "стэтхем,", "стэтхем ", "бот "]

# ── Расширенный словарь ответов ───────────────────────────────────────────────
BOT_ANSWERS = {
    ("кто ты", "что ты", "ты кто"):
        "👮 Я <b>Statham</b> — суровый модератор этого чата. Слежу за порядком, веду статистику и защищаю от флуда. Нарушители — трепещите! 😤",

    ("что умеешь", "что можешь", "твои команды", "помоги", "помогите"):
        "💡 Напиши /help — там всё подробно.\n\nВкратце: модерирую, слежу за флудом и матом, веду статистику участников, сообщаю о нарушителях. Порядок в чате — моя работа! 💪",

    ("правила", "rule"):
        "📋 Правила чата: /rules",

    ("моя статистика", "мои данные", "сколько сообщений"):
        "📊 Посмотри /mystats — там твоя личная статистика.",

    ("кто активнее", "кто топ", "топ чата", "самый активный"):
        "🏆 Посмотри /top — там топ-10 самых активных участников!",

    ("кто админ", "кто администратор"):
        "🛡 Информацию об админах узнай у владельца чата.",

    ("спасибо", "благодарю", "thanks", "thx", "спс"):
        "Всегда пожалуйста, <b>{name}</b>! Обращайся 😊",

    ("как дела", "как ты", "как жизнь", "что нового", "как сам"):
        [
            "Всё отлично, <b>{name}</b>! Слежу за порядком в чате 👮",
            "Работаю без остановки, <b>{name}</b>! Нарушителей пока не было 😎",
            "Бодро! Жду нарушителей, чтобы показать им кто тут главный 💪",
            "Нормально, <b>{name}</b>. Главное — порядок в чате! 🚀",
        ],

    ("скучно", "скучаю", "нечего делать"):
        [
            "Скучаешь, <b>{name}</b>? Напиши что-нибудь интересное, оживи чат! 😄",
            "Скука — враг прогресса! Займись чем-нибудь полезным, <b>{name}</b> 😏",
            "Расскажи что-нибудь интересное, <b>{name}</b>! Чат ждёт 🎉",
        ],

    ("хорошо", "отлично", "супер", "класс", "огонь", "круто"):
        [
            "Вот это настрой, <b>{name}</b>! 🔥",
            "Отлично! Так держать, <b>{name}</b> 💪",
            "Позитив — это сила, <b>{name}</b>! 😎",
        ],

    ("плохо", "грустно", "всё плохо", "устал", "устала"):
        [
            "Не грусти, <b>{name}</b>! Всё пройдёт 💪",
            "Держись, <b>{name}</b>! Лучшее впереди 🚀",
            "Бывает и хуже, <b>{name}</b>! Главное — не сдаваться 😎",
        ],

    ("шутка", "анекдот", "рассмеши", "смешное"):
        [
            "Почему программисты не любят природу? Там слишком много багов! 🐛😄",
            "Заходит SQL-инъекция в бар... Бармен спрашивает: 'Что будете?'\n— DROP TABLE drinks; 😂",
            "Оптимист: стакан наполовину полон.\nПессимист: стакан наполовину пуст.\nМодератор: кто налил — мут на 5 минут! 😤",
            "— Почему бот не спит?\n— Потому что нарушители не дремлют! 👮",
        ],

    ("сколько время", "который час", "время", "сколько времени"):
        None,  # обрабатывается отдельно (динамический ответ)

    ("удачи", "пока", "до свидания", "до встречи", "бывай"):
        "До встречи, <b>{name}</b>! 👋 Возвращайся!",

    ("молодец", "умница", "хороший бот", "хорошая работа"):
        [
            "Спасибо, <b>{name}</b>! Стараюсь 😊",
            "Приятно слышать, <b>{name}</b>! Продолжаю следить за порядком 💪",
        ],
}

def get_bot_answer(text: str, name: str, use_ai: bool = True,
                   user_id: int = None) -> str | None:
    t = text.lower()

    # Особый случай: время
    if any(k in t for k in ("сколько время", "который час", "сколько времени")):
        now_utc = datetime.datetime.utcnow()
        now_msk = now_utc + datetime.timedelta(hours=3)
        return f"🕐 Сейчас <b>{now_msk.strftime('%H:%M')}</b> по Москве (UTC+3), <b>{name}</b>."

    # Проверяем шаблонные ответы
    for keys, answer in BOT_ANSWERS.items():
        if any(k in t for k in keys):
            if answer is None:
                return None
            if isinstance(answer, list):
                return random.choice(answer).format(name=name)
            return answer.format(name=name)

    # 🤖 Если нет шаблонного ответа — спрашиваем AI (Groq) с историей
    if use_ai and ai.api_key:
        # Получаем историю диалога
        history = get_chat_history(user_id, limit=5) if user_id else None
        ai_response = ai.ask(text, name, history=history, user_id=user_id)
        if ai_response:
            return ai_response

    return None

# ══════════════════════════════════════════════════════════════════════════════
# МИНИ-ИГРЫ И АКТИВНОСТЬ
# ══════════════════════════════════════════════════════════════════════════════
RANDOM_REACTION_CHANCE = 0.12  # 12% шанс ответить на любое сообщение

FUN_FACTS = [
    "🐙 Осьминоги имеют три сердца и синюю кровь!",
    "🍌 Бананы — ягоды, а клубника — нет!",
    "🐝 Пчёлы могут распознавать человеческие лица.",
    "🦒 У жирафа язык длиной до 45 см — оно синее!",
    "🐘 Слоны — единственные животные, которые не могут прыгать.",
    "🦘 Кенгуру не могут ходить назад.",
    "🐧 Пингвины предлагают камешки своей второй половинке как подарок.",
    "🦉 Совы не могут двигать глазами — только головой.",
    "🦎 Хамелеоны меняют цвет не для маскировки, а для выражения эмоций!",
    "🐬 Дельфины дают друг другу имена!",
]

QUIZ_QUESTIONS = [
    {
        "q": "🧠 Вопрос: Сколько планет в Солнечной системе?",
        "options": ["7", "8", "9", "10"],
        "correct": 1,  # индекс правильного ответа
        "answer": "8 (Меркурий, Венера, Земля, Марс, Юпитер, Сатурн, Уран, Нептун)"
    },
    {
        "q": "🌍 Вопрос: Какая самая большая страна по площади?",
        "options": ["Китай", "США", "Россия", "Канада"],
        "correct": 2,
        "answer": "Россия — 17 млн км²"
    },
    {
        "q": "🚀 Вопрос: Кто первым полетел в космос?",
        "options": ["Нил Армстронг", "Юрий Гагарин", "Базз Олдрин", "Алан Шепард"],
        "correct": 1,
        "answer": "Юрий Гагарин — 12 апреля 1961 года"
    },
    {
        "q": "💻 Вопрос: Кто создал Python?",
        "options": ["Билл Гейтс", "Стив Джобс", "Гвидо ван Россум", "Линус Торвальдс"],
        "correct": 2,
        "answer": "Гвидо ван Россум в 1991 году"
    },
    {
        "q": "🌊 Вопрос: Какой океан самый глубокий?",
        "options": ["Атлантический", "Индийский", "Северный Ледовитый", "Тихий"],
        "correct": 3,
        "answer": "Тихий океан — Марианская впадина 11,022 м"
    },
]

# Расширенные триггеры для случайных реакций
RANDOM_REACTIONS = {
    ("питон", "python", "код", "программирование"): [
        "🐍 Python — это сила! {name}, ты кодер? 💪",
        "Кодишь, {name}? Красава! 🚀",
        "Программирование — это искусство, {name}! ✨",
    ],
    ("кофе", "чай", "пить"): [
        "☕ Кофе — топливо для мозга, {name}!",
        "Чай или кофе, {name}? Я за кофе! 😄",
        "Перерыв на кофе — святое дело! {name} 🙌",
    ],
    ("еда", "есть", "кушать", "обед", "ужин"): [
        "🍽 Время перекусить, {name}!",
        "Еда — это энергия, {name}! Не забудь поесть 💪",
        "Что на обед, {name}? 😋",
    ],
    ("работа", "учеба", "дела"): [
        "Работа не волк, но тоже опасна, {name}! 😄",
        "Учёба — свет, {name}! Продолжай в том же духе 📚",
        "Дела делаются, {name}! Ты молодец 💪",
    ],
    ("спорт", "тренировка", "фитнес", "зал"): [
        "💪 Спорт — это жизнь, {name}!",
        "Качаемся, {name}? Огонь! 🔥",
        "Фитнес — лучшее лекарство, {name}! 💯",
    ],
    ("музыка", "песня", "трек", "слушать"): [
        "🎵 Музыка двигает мир, {name}!",
        "Что слушаешь, {name}? Поделись! 🎧",
        "Музыка — душа чата, {name}! 🎶",
    ],
    ("фильм", "кино", "сериал", "смотреть"): [
        "🎬 Фильмец посмотреть — отличная идея, {name}!",
        "Кино — это магия, {name}! 🍿",
        "Сериал или фильм, {name}? 🤔",
    ],
    ("игра", "играть", "гейминг"): [
        "🎮 Геймеры, объединяйтесь! {name}, во что играешь?",
        "Игры — это тоже искусство, {name}! 🕹",
        "Покатали катку, {name}? Как итог? 🏆",
    ],
}

def check_random_reactions(text: str, name: str) -> str | None:
    """Проверяет текст на случайные реакции."""
    t = text.lower()
    for keys, answers in RANDOM_REACTIONS.items():
        if any(k in t for k in keys):
            return random.choice(answers).format(name=name)
    return None

def should_random_reply() -> bool:
    """5% шанс ответить на любое сообщение."""
    return random.random() < RANDOM_REACTION_CHANCE

# ══════════════════════════════════════════════════════════════════════════════
# КОМАНДЫ
# ══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(m):
    _reply(m, (
        "👮 <b>Statham Moderation Bot v3.1</b>\n\n"
        "Слежу за порядком в чате <b>Statham Elite</b>.\n\n"
        "📋 /rules — правила чата\n"
        "❓ /help — все команды\n"
        "📊 /mystats — твоя статистика\n"
        "🏆 /top — топ активных участников\n"
        "🤖 /ai [вопрос] — спросить AI"
    ))

@bot.message_handler(commands=["help"])
def cmd_help(m):
    admin_section = ""
    if is_admin(m.chat.id, m.from_user.id):
        admin_section = (
            "\n\n<b>👑 Для Admin (ответом на сообщение):</b>\n"
            "/ban — забанить навсегда\n"
            "/unban — разбанить\n"
            "/kick — выгнать (может вернуться)\n"
            "/mute [мин] — замутить (по умолч. 60)\n"
            "/unmute — размутить\n"
            "/warn — выдать предупреждение\n"
            "/clear_warns — сбросить предупреждения\n"
            "/warns — активные предупреждения\n"
            "/whois — досье на участника\n"
            "/users_stats — статистика участников"
        )
    _reply(m, (
        "👮 <b>Команды Statham Bot v3.2</b>\n\n"
        "<b>📋 Основные:</b>\n"
        "/start — приветствие\n"
        "/help — эта справка\n"
        "/rules — правила чата\n"
        "/mystats — твоя статистика\n"
        "/top — топ-10 активных участников\n"
        "/report — пожаловаться на сообщение (ответом)\n\n"
        "<b>🎮 Мини-игры:</b>\n"
        "/roll — кинуть кубик (1-100)\n"
        "/coin — подбросить монетку\n"
        "/fact — случайный факт\n"
        "/quiz — викторина (ответ числом 1-4)\n\n"
        "<b>🤖 AI:</b>\n"
        "/ai [вопрос] — спросить AI\n"
        "/ask [вопрос] — тоже спросить AI\n"
        "!ai [вопрос] — вызвать AI прямо в чате\n\n"
        "<b>📝 Персонализация:</b>\n"
        "/remember [факт] — запомнить факт о себе\n"
        "/myfacts — показать мои факты\n"
        "/dialogstats — статистика нашего общения"
        + admin_section
    ))

@bot.message_handler(commands=["rules"])
def cmd_rules(m):
    _reply(m, RULES_TEXT)

@bot.message_handler(commands=["mystats"])
def cmd_mystats(m):
    uid  = m.from_user.id
    user = get_user(uid)
    cnt  = get_warns(uid)
    if not user:
        _reply(m, "📊 Ещё нет данных о тебе. Напиши что-нибудь в чат!"); return
    unames = "@" + ", @".join(user["all_usernames"]) if user.get("all_usernames") else "—"
    _reply(m, (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"👤 Имя: <b>{user['first_name']}</b>\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📎 Никнеймы: {unames}\n"
        f"💬 Сообщений: <b>{user['msg_count']}</b>\n"
        f"📅 В чате с: {user['first_seen_dt']}\n"
        f"🕐 Последняя активность: {user['last_seen_dt']}\n"
        f"⚠️ Предупреждений: {cnt}/3"
    ))

@bot.message_handler(commands=["top"])
def cmd_top(m):
    """Топ-10 самых активных участников чата."""
    top = get_top_users(10)
    if not top:
        _reply(m, "📊 Пока нет данных об участниках."); return
    medals = ["🥇", "🥈", "🥉"] + ["4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lines = ["🏆 <b>Топ-10 активных участников</b>\n"]
    for i, u in enumerate(top):
        uname = f" @{u['username']}" if u.get("username") else ""
        medal = medals[i] if i < len(medals) else f"{i+1}."
        lines.append(f"{medal} <b>{u['first_name']}</b>{uname} — {u['msg_count']} сообщений")
    _reply(m, "\n".join(lines))

# ── Мини-игры ────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["roll"])
def cmd_roll(m):
    """🎲 Кинуть кубик (1-100)."""
    result = random.randint(1, 100)
    name = m.from_user.first_name

    if result == 100:
        msg = f"🎲 <b>{name}</b> кинул кубик и выбил <b>{result}</b>! КРИТИЧЕСКИЙ УСПЕХ! 🎉🔥"
    elif result >= 90:
        msg = f"🎲 <b>{name}</b> кинул кубик и выбил <b>{result}</b>! Отличный результат! 🎯"
    elif result >= 70:
        msg = f"🎲 <b>{name}</b> кинул кубик и выбил <b>{result}</b>! Хорошо! 👍"
    elif result >= 50:
        msg = f"🎲 <b>{name}</b> кинул кубик и выбил <b>{result}</b>. Средний результат. 🤔"
    elif result >= 30:
        msg = f"🎲 <b>{name}</b> кинул кубик и выбил <b>{result}</b>. Не очень... 😅"
    elif result >= 10:
        msg = f"🎲 <b>{name}</b> кинул кубик и выбил <b>{result}</b>. Фиаско! 😬"
    else:
        msg = f"🎲 <b>{name}</b> кинул кубик и выбил <b>{result}</b>... КРИТИЧЕСКИЙ ПРОВАЛ! 💀"

    _reply(m, msg)

@bot.message_handler(commands=["coin"])
def cmd_coin(m):
    """🪣 Подбросить монетку."""
    result = random.choice(["🦅 Орёл", "🪙 Решка"])
    name = m.from_user.first_name
    _reply(m, f"🪣 <b>{name}</b> подбрасывает монетку... <b>{result}</b>!")

@bot.message_handler(commands=["fact"])
def cmd_fact(m):
    """🎓 Случайный факт."""
    fact = random.choice(FUN_FACTS)
    _reply(m, f"🎓 <b>Знаете ли вы?</b>\n\n{fact}")

_active_quiz = None  # текущая викторина

@bot.message_handler(commands=["quiz"])
def cmd_quiz(m):
    """❓ Запустить викторину."""
    global _active_quiz

    if _active_quiz and (time.time() - _active_quiz["ts"]) < 60:
        # Активная викторина уже есть
        q = _active_quiz["question"]
        opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q["options"])])
        _reply(m, f"⏳ Викторина уже идёт!\n\n{q['q']}\n\n{opts}\n\nОтвечайте числом (1-4)!")
        return

    q = random.choice(QUIZ_QUESTIONS)
    _active_quiz = {"question": q, "ts": time.time(), "answered": set()}

    opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q["options"])])
    _reply(m, f"{q['q']}\n\n{opts}\n\nОтвечайте числом (1-4)!")

@bot.message_handler(func=lambda m: _active_quiz and m.text and m.text.strip().isdigit(), content_types=["text"])
def check_quiz_answer(m):
    """Проверка ответа на викторину."""
    global _active_quiz

    if not _active_quiz:
        return

    # Проверяем, не устарела ли викторина
    if time.time() - _active_quiz["ts"] > 60:
        _active_quiz = None
        return

    # Проверяем, не отвечал ли уже этот пользователь
    if m.from_user.id in _active_quiz["answered"]:
        return

    answer = int(m.text.strip()) - 1  # переводим в 0-based индекс
    q = _active_quiz["question"]

    if 0 <= answer < len(q["options"]):
        _active_quiz["answered"].add(m.from_user.id)

        if answer == q["correct"]:
            try:
                _reply_to_with_retry(m, f"✅ <b>{m.from_user.first_name}</b> правильно! {q['answer']}")
            except Exception as e:
                write_log(f"QUIZ_OK_ERR | {type(e).__name__}")
            _active_quiz = None  # викторина завершена
        else:
            wrong_answers = len(_active_quiz["answered"])
            if wrong_answers >= 3:  # после 3 неправильных — показываем ответ
                try:
                    _reply_to_with_retry(m, f"❌ Неправильно! Правильный ответ: <b>{q['options'][q['correct']]}</b>. {q['answer']}")
                except Exception as e:
                    write_log(f"QUIZ_FAIL_ERR | {type(e).__name__}")
                _active_quiz = None
            else:
                try:
                    _reply_to_with_retry(m, f"❌ <b>{m.from_user.first_name}</b>, неправильно! Попробуйте ещё!")
                except Exception as e:
                    write_log(f"QUIZ_RETRY_ERR | {type(e).__name__}")

@bot.message_handler(commands=["report"])
def cmd_report(m):
    if not m.reply_to_message:
        _reply(m, "❌ Ответьте /report на сообщение нарушителя."); return
    target   = m.reply_to_message.from_user
    reporter = m.from_user
    msg_text = m.reply_to_message.text or "[не текст]"
    save_report(reporter.id, target.id, msg_text[:200])
    write_log(f"REPORT | from={reporter.id} target={target.id} | {msg_text[:50]}")
    notify_text = (
        f"🚨 <b>Жалоба в чате!</b>\n\n"
        f"От: <b>{reporter.first_name}</b> (@{getattr(reporter,'username','')})\n"
        f"На: <b>{target.first_name}</b> (@{getattr(target,'username','')} | ID: <code>{target.id}</code>)\n"
        f"Сообщение: <i>{msg_text[:150]}</i>"
    )
    _notify_admins(m.chat.id, notify_text)
    _reply(m, f"✅ Жалоба отправлена администраторам. Спасибо, <b>{reporter.first_name}</b>!")

# ── Команды для ADMIN ─────────────────────────────────────────────────────────
@bot.message_handler(commands=["ban"])
def cmd_ban(m):
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    if not m.reply_to_message:
        _reply(m, "❌ Ответьте /ban на сообщение пользователя."); return
    target = m.reply_to_message.from_user
    if is_admin(m.chat.id, target.id):
        _reply(m, "❌ Нельзя забанить администратора."); return
    try:
        bot.ban_chat_member(m.chat.id, target.id)
        write_log(f"BAN | {target.id} @{getattr(target,'username','')} | by {m.from_user.id}")
        _reply(m, f"🚫 <b>{target.first_name}</b> навсегда забанен.")
    except Exception as e:
        write_log(f"BAN_ERR | {e}"); _reply(m, f"❌ {e}")

@bot.message_handler(commands=["unban"])
def cmd_unban(m):
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    if not m.reply_to_message:
        _reply(m, "❌ Ответьте /unban на сообщение пользователя."); return
    target = m.reply_to_message.from_user
    try:
        bot.unban_chat_member(m.chat.id, target.id, only_if_banned=True)
        write_log(f"UNBAN | {target.id} | by {m.from_user.id}")
        _reply(m, f"✅ <b>{target.first_name}</b> разбанен.")
    except Exception as e:
        write_log(f"UNBAN_ERR | {e}"); _reply(m, f"❌ {e}")

@bot.message_handler(commands=["kick"])
def cmd_kick(m):
    """Выгнать участника (может вернуться по ссылке приглашения)."""
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    if not m.reply_to_message:
        _reply(m, "❌ Ответьте /kick на сообщение пользователя."); return
    target = m.reply_to_message.from_user
    if is_admin(m.chat.id, target.id):
        _reply(m, "❌ Нельзя выгнать администратора."); return
    try:
        # Кик = бан + сразу разбан (может вернуться)
        bot.ban_chat_member(m.chat.id, target.id)
        time.sleep(0.3)
        bot.unban_chat_member(m.chat.id, target.id)
        write_log(f"KICK | {target.id} @{getattr(target,'username','')} | by {m.from_user.id}")
        _reply(m, f"👢 <b>{target.first_name}</b> выгнан из чата (может вернуться по ссылке).")
    except Exception as e:
        write_log(f"KICK_ERR | {e}"); _reply(m, f"❌ {e}")

@bot.message_handler(commands=["mute"])
def cmd_mute(m):
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    if not m.reply_to_message:
        _reply(m, "❌ Ответьте /mute [минуты] на сообщение пользователя."); return
    target = m.reply_to_message.from_user
    if is_admin(m.chat.id, target.id):
        _reply(m, "❌ Нельзя замутить администратора."); return
    args = m.text.split()
    mins = 60
    if len(args) > 1:
        try: mins = max(1, int(args[1]))
        except ValueError: pass
    try:
        _mute(m.chat.id, target.id, mins)
        write_log(f"MUTE | {target.id} | {mins}мин | by {m.from_user.id}")
        _reply(m, f"🔇 <b>{target.first_name}</b> замучен на {format_duration(mins)}.")
    except Exception as e:
        write_log(f"MUTE_ERR | {e}"); _reply(m, f"❌ {e}")

@bot.message_handler(commands=["unmute"])
def cmd_unmute(m):
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    if not m.reply_to_message:
        _reply(m, "❌ Ответьте /unmute на сообщение пользователя."); return
    target = m.reply_to_message.from_user
    try:
        _unmute(m.chat.id, target.id)
        write_log(f"UNMUTE | {target.id} | by {m.from_user.id}")
        _reply(m, f"🔊 <b>{target.first_name}</b> размучен.")
    except Exception as e:
        write_log(f"UNMUTE_ERR | {e}"); _reply(m, f"❌ {e}")

@bot.message_handler(commands=["warn"])
def cmd_warn(m):
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    if not m.reply_to_message:
        _reply(m, "❌ Ответьте /warn на сообщение пользователя."); return
    target = m.reply_to_message.from_user
    if is_admin(m.chat.id, target.id):
        _reply(m, "❌ Нельзя выдать предупреждение администратору."); return
    _apply_warn(m.chat.id, target, reason="ручное предупреждение от админа")

def _apply_warn(chat_id, user, reason: str = ""):
    """Единая логика выдачи варна с прогрессивным мутом."""
    cnt, total = add_warn(user.id)
    write_log(f"WARN | {user.id} @{getattr(user,'username','')} | cnt={cnt} total={total} | {reason}")

    mute_mins = get_mute_duration(cnt)
    should_mute = cnt in [t for t, _ in MUTE_STEPS]

    if should_mute:
        try:
            _mute(chat_id, user.id, mute_mins)
            reset_warns(user.id)
            duration_str = format_duration(mute_mins)
            _send_message_simple(chat_id,
                f"🔇 <b>{user.first_name}</b> замучен на <b>{duration_str}</b> "
                f"({cnt}/3 предупреждений).",
                parse_mode="HTML")
        except Exception as e:
            write_log(f"MUTE_AUTO_ERR | {type(e).__name__} | {e}")
            try:
                _send_message_simple(chat_id, f"⚠️ Нет прав на мут: {e}")
            except Exception:
                pass
    else:
        try:
            _send_message_simple(chat_id,
                f"⚠️ <b>{user.first_name}</b>, нарушение зафиксировано! "
                f"Предупреждение {cnt}/3.\n"
                f"<i>При 3 предупреждениях — мут.</i>",
                parse_mode="HTML")
        except Exception as e:
            write_log(f"WARN_MSG_ERR | {type(e).__name__} | {e}")

@bot.message_handler(commands=["clear_warns"])
def cmd_clear_warns(m):
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    if not m.reply_to_message:
        _reply(m, "❌ Ответьте /clear_warns на сообщение пользователя."); return
    target = m.reply_to_message.from_user
    reset_warns(target.id)
    write_log(f"CLEAR_WARNS | {target.id} | by {m.from_user.id}")
    _reply(m, f"✅ Предупреждения <b>{target.first_name}</b> сброшены.")

@bot.message_handler(commands=["warns"])
def cmd_warns(m):
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    if m.reply_to_message:
        uid = m.reply_to_message.from_user.id
        cnt = get_warns(uid)
        _reply(m, f"⚠️ {m.reply_to_message.from_user.first_name}: {cnt}/3 предупреждений."); return
    active = get_all_warns()
    if not active:
        _reply(m, "✅ Активных предупреждений нет."); return
    lines = ["⚠️ <b>Активные предупреждения:</b>\n"]
    for row in active:
        name  = row.get("first_name") or f"ID {row['user_id']}"
        uname = f"@{row['username']}" if row.get("username") else ""
        lines.append(f"• <b>{name}</b> {uname}: {row['count']}/3 (всего: {row['total_warns']})")
    _reply(m, "\n".join(lines))

@bot.message_handler(commands=["users_stats"])
def cmd_users_stats(m):
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    with _db_lock:
        conn = get_db()
        try:
            total  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            recent = conn.execute("""
                SELECT first_name, username, first_seen_dt, msg_count
                FROM users ORDER BY first_seen DESC LIMIT 10
            """).fetchall()
        finally:
            conn.close()
    lines = [f"👥 <b>Участников в базе: {total}</b>\n\n<b>Последние вошедшие:</b>"]
    for u in recent:
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"• <b>{u['first_name']}</b>  {uname}  💬{u['msg_count']}  ({u['first_seen_dt']})")
    _reply(m, "\n".join(lines))

@bot.message_handler(commands=["whois"])
def cmd_whois(m):
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    u = None
    if m.reply_to_message:
        u = get_user(m.reply_to_message.from_user.id)
    else:
        args = m.text.split()
        if len(args) < 2:
            _reply(m, "❌ Использование:\n• Ответьте /whois на сообщение\n• /whois @username\n• /whois 123456789"); return
        u = find_user_by_query(args[1])
    if not u:
        _reply(m, "❌ Пользователь не найден в базе."); return
    uid    = u["user_id"]
    cnt    = get_warns(uid)
    unames = "@" + ", @".join(u["all_usernames"]) if u.get("all_usernames") else "—"
    _reply(m, (
        f"👤 <b>{u['first_name']}</b>\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📎 Никнеймы: {unames}\n"
        f"💬 Сообщений: <b>{u['msg_count']}</b>\n"
        f"📅 Вошёл: {u['first_seen_dt']}\n"
        f"🕐 Последняя активность: {u['last_seen_dt']}\n"
        f"⚠️ Предупреждений: {cnt}/3"
    ))

@bot.message_handler(commands=["remember"])
def cmd_remember(m):
    """Запомнить факт о себе. Использование: /remember [факт]"""
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        _reply(m, (
            "📝 <b>Запомнить факт о себе</b>\n\n"
            "Использование: <code>/remember [что запомнить]</code>\n\n"
            "Примеры:\n"
            "• /remember у меня кот Барсик\n"
            "• /remember я из Москвы\n"
            "• /remember люблю пиццу\n\n"
            "Бот будет упоминать это в приветствиях! 😊"
        ))
        return

    fact = args[1].strip()
    if len(fact) > 200:
        _reply(m, "❌ Слишком длинный факт (макс 200 символов)")
        return

    if save_user_fact(m.from_user.id, fact):
        _reply(m, f"✅ Запомнил: <i>{fact}</i>\n\nБуду упоминать это в наших разговорах! 😊")
    else:
        _reply(m, "❌ Не удалось сохранить. Попробуй позже.")

@bot.message_handler(commands=["myfacts"])
def cmd_myfacts(m):
    """Показать мои факты."""
    facts = get_user_facts(m.from_user.id)
    if not facts:
        _reply(m, (
            "📝 У тебя пока нет сохранённых фактов.\n\n"
            "Добавь: <code>/remember [факт]</code>\n"
            "Например: /remember у меня есть собака Рекс"
        ))
        return

    lines = ["📝 <b>Твои факты:</b>\n"]
    for i, f in enumerate(facts[:10], 1):
        lines.append(f"{i}. {f['fact']}")

    if len(facts) > 10:
        lines.append(f"\n...и ещё {len(facts) - 10} фактов")

    lines.append("\n💡 Добавить: <code>/remember [факт]</code>")
    _reply(m, "\n".join(lines))

@bot.message_handler(commands=["ai", "ask"])
def cmd_ai(m):
    """🤖 Прямой вопрос AI. Использование: /ai [вопрос] или /ask [вопрос]"""
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        _reply(m, (
            "🤖 <b>Вопрос AI</b>\n\n"
            "Использование:\n"
            "• <code>/ai [вопрос]</code>\n"
            "• <code>/ask [вопрос]</code>\n"
            "• <code>!ai [вопрос]</code> — прямо в чате\n\n"
            "Примеры:\n"
            "• /ai как дела?\n"
            "• /ask напиши код на Python\n"
            "• !ai что такое API?"
        ))
        return

    if not ai.api_key:
        _reply(m, "❌ AI сейчас недоступен. Попробуй позже.")
        return

    question = args[1].strip()
    if len(question) > 500:
        _reply(m, "❌ Слишком длинный вопрос (макс 500 символов)")
        return

    # Показываем что бот печатает
    try:
        bot.send_chat_action(m.chat.id, "typing")
    except Exception:
        pass

    # Получаем историю и генерируем ответ
    history = get_chat_history(m.from_user.id, limit=5)
    response = ai.ask(question, m.from_user.first_name,
                       context="Прямой вопрос через /ai команду",
                       history=history, user_id=m.from_user.id)

    if response:
        _reply(m, f"🤖 <b>AI отвечает:</b>\n\n{response}")
    else:
        _reply(m, "❌ AI не смог ответить. Попробуй другой вопрос или повтори позже.")


@bot.message_handler(commands=["dialogstats"])
def cmd_dialogstats(m):
    """📊 Статистика общения с ботом."""
    uid = m.from_user.id
    count = get_user_chat_count(uid)
    history = get_chat_history(uid, limit=3)

    # Определяем уровень "дружбы"
    if count == 0:
        level = "🆕 Новый знакомый"
    elif count < 5:
        level = "👋 Знакомые"
    elif count < 15:
        level = "🤝 Друзья"
    elif count < 30:
        level = "👯 Близкие друзья"
    else:
        level = "🔥 Лучшие друзья навсегда!"

    lines = [
        f"📊 <b>Статистика общения</b>",
        f"",
        f"💬 Сообщений: <b>{count}</b>",
        f"🎯 Уровень: {level}",
    ]

    if history:
        lines.append(f"\n📜 Последние обмены:")
        for i, h in enumerate(history, 1):
            msg = h['user_msg'][:30] + "..." if len(h['user_msg']) > 30 else h['user_msg']
            lines.append(f"{i}. Ты: {msg}")

    if count >= 5:
        lines.append(f"\n✨ Я запоминаю нашу беседу и учусь лучше понимать тебя!")

    _reply(m, "\n".join(lines))

# ══════════════════════════════════════════════════════════════════════════════
# НОВЫЕ УЧАСТНИКИ / УХОД
# ══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(content_types=["new_chat_members"])
def welcome(message):
    for user in message.new_chat_members:
        if getattr(user, "is_bot", False): continue
        record_user(user)
        write_log(f"JOIN | {user.id} @{getattr(user,'username','')} {user.first_name}")

        # Проверяем, есть ли факты о пользователе (если он раньше был)
        personalized = get_personalized_greeting(user.id, user.first_name)

        if personalized:
            text = (
                f"👋 {personalized}\n\n"
                f"С возвращением в <b>Statham Elite</b>! 🚀\n\n"
                f"📋 /rules — правила\n"
                f"📊 /mystats — статистика\n"
                f"🏆 /top — топ участников"
            )
        else:
            text = (
                f"👋 Привет, <b>{user.first_name}</b>! Добро пожаловать в <b>Statham Elite</b> 🚀\n\n"
                f"📋 Ознакомься с правилами: /rules\n"
                f"📊 Твоя статистика: /mystats\n"
                f"🏆 Топ участников: /top\n\n"
                f"💡 Добавь факт о себе: /remember [факт]"
            )

        try:
            if os.path.exists(PHOTO_PATH):
                with open(PHOTO_PATH, "rb") as p:
                    bot.send_photo(message.chat.id, p, caption=text, parse_mode="HTML")
            else:
                _send_message_simple(message.chat.id, text, parse_mode="HTML")
        except Exception as e:
            write_log(f"WELCOME_ERR | {type(e).__name__} | {e}")

@bot.message_handler(content_types=["left_chat_member"])
def farewell(message):
    user = message.left_chat_member
    if getattr(user, "is_bot", False): return
    write_log(f"LEFT | {user.id} @{getattr(user,'username','')} {user.first_name}")
    try:
        _send_message_with_retry(message.chat.id,
            f"👋 <b>{user.first_name}</b> покинул(а) чат. До встречи!",
            parse_mode="HTML")
    except Exception as e:
        write_log(f"FAREWELL_ERR | {type(e).__name__} | {e}")

# ══════════════════════════════════════════════════════════════════════════════
# ОБРАБОТЧИК ПРЕФИКСА !ai ДЛЯ ЯВНОГО ВЫЗОВА AI
# ══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith("!ai"), content_types=["text"])
def handle_ai_prefix(m):
    """Обработчик префикса !ai для явного вызова AI."""
    text = m.text.strip()

    # Убираем префикс и получаем вопрос
    question = text[3:].strip() if len(text) > 3 else ""

    if not question:
        try:
            _reply_to_with_retry(m, (
                "🤖 <b>Использование:</b> <code>!ai [вопрос]</code>\n\n"
                "Примеры:\n"
                "• !ai как дела?\n"
                "• !ai объясни Python\n"
                "• !ai напиши шутку"
            ), parse_mode="HTML")
        except Exception as e:
            write_log(f"AI_PREFIX_HELP_ERR | {type(e).__name__}")
        return

    if not ai.api_key:
        try:
            _reply_to_with_retry(m, "❌ AI сейчас недоступен. Попробуй позже.", parse_mode="HTML")
        except Exception as e:
            write_log(f"AI_PREFIX_NOKEY_ERR | {type(e).__name__}")
        return

    # Показываем что бот печатает
    try:
        bot.send_chat_action(m.chat.id, "typing")
    except Exception:
        pass

    # Записываем пользователя
    record_user(m.from_user)

    # Получаем историю и генерируем ответ
    history = get_chat_history(m.from_user.id, limit=5)
    response = ai.ask(question, m.from_user.first_name,
                       context="Явный вызов через !ai префикс",
                       history=history, user_id=m.from_user.id)

    if response:
        try:
            _reply_to_with_retry(m, f"🤖 {response}", parse_mode="HTML")
        except Exception as e:
            write_log(f"AI_PREFIX_OK_ERR | {type(e).__name__}")
    else:
        try:
            _reply_to_with_retry(m, "❌ AI не смог ответить. Попробуй другой вопрос или повтори позже.", parse_mode="HTML")
        except Exception as e:
            write_log(f"AI_PREFIX_FAIL_ERR | {type(e).__name__}")


# ══════════════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ
# ══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message):
    # Игнорируем старые сообщения
    if time.time() - message.date > 60:
        return

    user = message.from_user
    record_user(user)

    admin = is_admin(message.chat.id, user.id)
    t     = message.text.lower().strip()

    # ── Ответы на прямые обращения к боту ────────────────────────────────────
    is_direct = any(trigger in t for trigger in BOT_TRIGGERS)
    if is_direct:
        answer = get_bot_answer(t, user.first_name, user_id=user.id)
        if answer:
            try:
                _reply_to_with_retry(message, answer, parse_mode="HTML")
            except Exception as e:
                write_log(f"DIRECT_REPLY_ERR | {type(e).__name__} | {e}")
            return

    # ── Приветствие ───────────────────────────────────────────────────────────
    words = set(re.sub(r'[^\w\s]', '', t).split())
    if words & GREETINGS:
        # Пробуем персонализированное приветствие
        personalized = get_personalized_greeting(user.id, user.first_name)
        if personalized and random.random() < 0.3:  # 30% шанс использовать факт
            greeting = personalized
        else:
            greeting = random.choice(GREETING_REPLIES).format(name=user.first_name)

        try:
            _reply_to_with_retry(message, greeting, parse_mode="HTML")
        except Exception as e:
            write_log(f"GREETING_REPLY_ERR | {type(e).__name__} | {e}")
        if admin: return

    # ── Случайные реакции на ключевые слова ──────────────────────────────────
    random_reaction = check_random_reactions(t, user.first_name)
    if random_reaction:
        try:
            _reply_to_with_retry(message, random_reaction, parse_mode="HTML")
        except Exception as e:
            write_log(f"REACTION_REPLY_ERR | {type(e).__name__} | {e}")
        if admin: return

    # ── 12% шанс ответить AI на любое сообщение (для активности) ─────────────
    if should_random_reply() and ai.api_key:
        history = get_chat_history(user.id, limit=5)
        ai_response = ai.ask(message.text[:200], user.first_name,
                            context="Случайный ответ в чате",
                            history=history, user_id=user.id)
        if ai_response:
            try:
                _reply_to_with_retry(message, ai_response, parse_mode="HTML")
            except Exception as e:
                write_log(f"AI_REPLY_ERR | {type(e).__name__} | {e}")
            return

    # Администраторы не модерируются дальше
    if admin:
        return

    uid = user.id

    # ── Антифлуд ──────────────────────────────────────────────────────────────
    if check_flood(uid):
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception: pass
        try:
            _mute(message.chat.id, uid, FLOOD_MUTE)
            _send_message_simple(message.chat.id,
                f"🚫 <b>{user.first_name}</b>, флуд запрещён! "
                f"Мут на {format_duration(FLOOD_MUTE)}.",
                parse_mode="HTML")
            write_log(f"FLOOD | {uid} @{getattr(user,'username','')}")
        except Exception as e:
            write_log(f"FLOOD_MUTE_ERR | {type(e).__name__} | {e}")
        return

    # ── Антимат ───────────────────────────────────────────────────────────────
    bad_word = contains_bad_word(message.text)
    if bad_word:
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception: pass
        write_log(f"BADWORD | {uid} @{getattr(user,'username','')} | word={bad_word}")
        _apply_warn(message.chat.id, user, reason=f"мат: {bad_word}")
        return

# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK + FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════
_webhook_ok = False

def _get_current_webhook() -> str:
    """Узнать текущий установленный webhook URL."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo",
            timeout=10,
        )
        data = r.json()
        return data.get("result", {}).get("url", "")
    except Exception:
        return ""

def _do_register_webhook():
    """
    Устанавливает webhook с повторными попытками.
    ✅ ИСПРАВЛЕНО: Сначала проверяет — нужна ли переустановка.
    ✅ ИСПРАВЛЕНО: Retry при ошибке прокси (503 ProxyError).
    """
    global _webhook_ok
    if not TOKEN or not PA_DOMAIN:
        write_log("WEBHOOK_SKIP | TOKEN или PA_DOMAIN не заданы"); return

    wh_url = f"https://{PA_DOMAIN}/{TOKEN}"

    # Проверяем текущий webhook — если уже стоит правильный, не трогаем
    current = _get_current_webhook()
    if current == wh_url:
        write_log("WEBHOOK_SKIP | Webhook уже установлен, переустановка не нужна")
        _webhook_ok = True
        return

    # Retry-логика: до 5 попыток с паузой 5 секунд
    max_retries = 5
    retry_delay = 5

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/setWebhook",
                json={
                    "url": wh_url,
                    "drop_pending_updates": True,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=10,
            )
            result = r.json()
            write_log(f"WEBHOOK_SETUP | attempt={attempt} | url={wh_url} | result={result}")
            if result.get("ok"):
                _webhook_ok = True
                return
            # Telegram вернул ok=False — нет смысла повторять немедленно
            write_log(f"WEBHOOK_FAIL | attempt={attempt} | {result}")
        except Exception as e:
            write_log(f"WEBHOOK_SETUP_ERR | attempt={attempt}/{max_retries} | {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)

    write_log("WEBHOOK_SETUP | Все попытки исчерпаны. Попробуйте /setup вручную.")

# Инициализация БД и вебхука при старте
init_db()
threading.Thread(target=lambda: (time.sleep(5), _do_register_webhook()),
                 daemon=True).start()


@app.route("/" + TOKEN, methods=["POST"])
def tg_webhook():
    try:
        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([update])
    except Exception as e:
        write_log(f"TG_WEBHOOK_ERR | {e}")
    return "!", 200


@app.route("/setup")
def setup():
    _do_register_webhook()
    return f"Webhook {'OK ✅' if _webhook_ok else 'FAIL ❌'}"


@app.route("/debug")
def debug():
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return "<pre>" + "".join(lines[-100:]) + "</pre>", 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        return f"<pre>Error: {e}</pre>", 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/stats")
def stats_page():
    """Страница статистики для быстрого просмотра."""
    try:
        with _db_lock:
            conn = get_db()
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            top_active  = conn.execute("""
                SELECT first_name, username, msg_count
                FROM users ORDER BY msg_count DESC LIMIT 10
            """).fetchall()
            active_warns = conn.execute("""
                SELECT w.user_id, w.count, u.first_name
                FROM warns w LEFT JOIN users u ON w.user_id=u.user_id
                WHERE w.count > 0
            """).fetchall()
            conn.close()
        html = f"<h2>📊 Statham Bot Stats</h2><p>Всего участников: <b>{total_users}</b></p>"
        html += "<h3>🏆 Топ активных</h3><ol>"
        for u in top_active:
            uname = f"@{u['username']}" if u["username"] else ""
            html += f"<li>{u['first_name']} {uname} — {u['msg_count']} сообщений</li>"
        html += "</ol><h3>⚠️ Активные варны</h3><ul>"
        for w in active_warns:
            html += f"<li>{w['first_name']} (ID {w['user_id']}): {w['count']}/3</li>"
        if not active_warns:
            html += "<li>Нет</li>"
        html += "</ul>"
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        return f"<pre>Error: {e}</pre>", 200


@app.route("/health")
def health():
    return "OK", 200


# ══════════════════════════════════════════════════════════════════════════════
# ⏰ APSCHEDULER — встроенный cron (не нужны внешние Railway Cron Jobs)
# ══════════════════════════════════════════════════════════════════════════════

def _job_morning():
    """Утренний пост — 08:00 МСК."""
    write_log("SCHEDULER | morning job triggered")
    text = random.choice(MORNING_MESSAGES)
    if ai.api_key:
        ai_greeting = ai.generate_greeting("чат", is_morning=True)
        if ai_greeting:
            text = ai_greeting
    _send_scheduled_message(text, MORNING_PHOTO_PATH)

def _job_night():
    """Ночной пост — 23:00 МСК."""
    write_log("SCHEDULER | night job triggered")
    text = random.choice(NIGHT_MESSAGES)
    _send_scheduled_message(text, NIGHT_PHOTO_PATH)

def _job_factofday():
    """Факт дня — 12:00 МСК."""
    write_log("SCHEDULER | factofday job triggered")
    text = random.choice(DAILY_FACTS)
    _send_scheduled_message(text)

# Инициализация планировщика
_scheduler = BackgroundScheduler(timezone="Europe/Moscow", daemon=True)
_scheduler.add_job(_job_morning,   "cron", hour=8,  minute=0,  id="morning")
_scheduler.add_job(_job_factofday, "cron", hour=12, minute=0,  id="factofday")
_scheduler.add_job(_job_night,     "cron", hour=23, minute=0,  id="night")
_scheduler.start()
write_log("SCHEDULER | APScheduler started (morning=08:00, fact=12:00, night=23:00 MSK)")

# Graceful shutdown при SIGTERM (Railway останавливает контейнер через SIGTERM)
def _handle_shutdown(sig, frame):
    write_log("SHUTDOWN | SIGTERM received, stopping scheduler...")
    _scheduler.shutdown(wait=False)
    sys.exit(0)

signal.signal(signal.SIGTERM, _handle_shutdown)

MORNING_PHOTO_PATH = os.path.join(BASE_DIR, "morning.png")
NIGHT_PHOTO_PATH = os.path.join(BASE_DIR, "night.png")

MORNING_MESSAGES = [
    "☀️ Доброе утро, чат! Пусть этот день будет продуктивным и позитивным! 💪",
    "🌅 Всем привет! Новый день — новые возможности! Удачи всем! 🚀",
    "☕ Утро! Время кофе и крутых дел! Как у вас дела? 😊",
    "🌞 Проснись и пой! Доброе утро, Statham Elite! 💪",
]

NIGHT_MESSAGES = [
    "🌙 Всем спокойной ночи! Отдыхайте и набирайтесь сил! 💤",
    "⭐ Доброй ночи, чат! До встречи завтра! 😴",
    "🌜 Время отдыхать! Спите крепко, друзья! 🛌",
    "💫 Ночь — время мечтать! Спокойной ночи всем! ✨",
]

DAILY_FACTS = [
    "🎯 Факт дня: Самое быстрое животное — сапсан, он разгоняется до 390 км/ч!",
    "🎯 Факт дня: Медузы состоят на 95% из воды и не имеют мозга!",
    "🎯 Факт дня: Осьминоги могут менять цвет за 0,3 секунды!",
    "🎯 Факт дня: У жирафов столько же шейных позвонков, сколько у человека — 7!",
    "🎯 Факт дня: Банан — ягода, а клубника — нет! 🍌",
    "🎯 Факт дня: Пчёлы могут различать лица людей! 🐝",
    "🎯 Факт дня: Слоны — единственные животные, которые не умеют прыгать! 🐘",
]

def _send_scheduled_message(text: str, photo_path: str = None):
    """Отправляет сообщение в чат (используется cron)."""
    if not CHAT_ID:
        write_log("CRON_ERR | CHAT_ID не задан")
        return "NO_CHAT_ID", 500

    try:
        chat_id = int(CHAT_ID)

        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as p:
                bot.send_photo(chat_id, p, caption=text, parse_mode="HTML")
        else:
            _send_message_simple(chat_id, text, parse_mode="HTML")

        write_log(f"CRON_OK | sent to {chat_id}")
        return "OK", 200
    except Exception as e:
        write_log(f"CRON_ERR | {e}")
        return f"ERROR: {e}", 500


@app.route("/morning")
def morning_post():
    """Ручной триггер утреннего поста (APScheduler запускает автоматически в 08:00 МСК)."""
    text = random.choice(MORNING_MESSAGES)
    # Если есть AI — генерируем уникальное приветствие
    if ai.api_key:
        ai_greeting = ai.generate_greeting("чат", is_morning=True)
        if ai_greeting:
            text = ai_greeting
    return _send_scheduled_message(text, MORNING_PHOTO_PATH)


@app.route("/night")
def night_post():
    """Ручной триггер ночного поста (APScheduler запускает автоматически в 23:00 МСК)."""
    text = random.choice(NIGHT_MESSAGES)
    return _send_scheduled_message(text, NIGHT_PHOTO_PATH)


@app.route("/factofday")
def fact_of_day():
    """Ручной триггер факта дня (APScheduler запускает автоматически в 12:00 МСК)."""
    text = random.choice(DAILY_FACTS)
    return _send_scheduled_message(text)


# WSGI entrypoint (для совместимости с gunicorn)
application = app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
