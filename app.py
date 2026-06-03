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
# ── v5.0 modules ─────────────────────────────────────────────────────────────
from redis_memory import (
    redis_ok, redis_stats,
    save_chat_history as _redis_save_hist,
    get_chat_history_r, get_chat_count_r, clear_chat_history_r,
    add_global_ctx, get_global_ctx,
    check_flood_r,
    get_groq_cache, set_groq_cache,
    save_user_memory, get_user_memory, get_user_memory_str, delete_user_memory,
    set_crypto_prices, get_crypto_prices,
    add_price_alert, get_all_alerts, remove_alert, get_user_alerts, check_rate_limit,
    get_chat_topic, set_chat_topic, update_topic_keyword, incr_user_msg_count,
)
from crypto_module import (
    format_price_message, format_market_message, format_fear_greed,
    get_crypto_ai_context, check_price_alerts, COIN_ALIASES, get_prices,
    get_crypto_news, format_news_message, format_breaking_news,
)
from chart_module import (
    format_chart_message, format_movers_message,
    format_altseason_message, format_funding_message, format_tvl_message,
)
from portfolio_module import handle_portfolio_command
from calendar_module import (
    format_calendar_message, check_events_today, check_events_soon, format_event_alert,
    get_upcoming_events, get_crypto_impact, _translate_event,
)
from game_module import (
    give_achievement, get_achievements, format_achievements,
    make_prediction, get_active_predictions, resolve_predictions,
    save_predict_stats, get_predict_stats, format_predict_stats,
    get_prediction_leaderboard, get_daily_question, vote,
    get_vote_results, format_vote_results,
)
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
PHOTO_PATH = os.path.join(os.path.dirname(__file__), "hello.jpg")
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

        # Проверяем кэш Redis (только если нет истории)
        if not history:
            cache_key = self._get_cache_key(f"{user_name}:{user_message}")
            cached = get_groq_cache(cache_key) or self._get_cached(f"{user_name}:{user_message}")
            if cached:
                return cached

        # Определяем "тесноту" общения
        chat_depth = len(history) if history else 0
        is_close_chat = chat_depth >= 3  # Если 3+ обмена — это плотное общение

        system_prompt = (
            "Ты — Statham, бот в крипто-чате Telegram. Характер прямой, с юмором. Торгуешь крипто с 2017. "
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

                # Сохраняем в историю (Redis + SQLite fallback)
                if user_id:
                    if not _redis_save_hist(user_id, user_message, answer):
                        save_chat_message(user_id, user_message, answer)

                # Кэшируем в Redis + RAM
                if not history and not user_id:
                    ck = self._get_cache_key(f"{user_name}:{user_message}")
                    set_groq_cache(ck, answer)
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

# ══════════════════════════════════════════════════════════════════════════════
# GEMINI AI — резервный AI (Google, бесплатно 1500 req/day)
# ══════════════════════════════════════════════════════════════════════════════
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

class GeminiAI:
    """Google Gemini 2.0 Flash — резервный AI если Groq не отвечает."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._model = None
        if api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                self._model = genai.GenerativeModel(
                    "gemini-2.0-flash",
                    system_instruction=(
                        "Ты — дружелюбный бот Statham в Telegram-чате. "
                        "Отвечай кратко (1-2 предложения), на русском, с эмодзи. "
                        "Ты эксперт по криптовалютам и финансам. "
                        "Не пиши длинных эссе — это чат."
                    )
                )
            except Exception as e:
                write_log(f"GEMINI_INIT_ERR | {e}")
                self._model = None

    @property
    def enabled(self):
        return self._model is not None

    def ask(self, prompt: str, user_name: str = "") -> str | None:
        if not self._model:
            return None
        try:
            full = f"{user_name}: {prompt}" if user_name else prompt
            resp = self._model.generate_content(full)
            return resp.text.strip()[:500] if resp.text else None
        except Exception as e:
            write_log(f"GEMINI_ERR | {type(e).__name__} | {e}")
            return None

# Инициализация AI (если ключ задан)
ai = GroqAI(GROQ_API_KEY)
gemini = GeminiAI(GEMINI_API_KEY)

def ask_ai(user_message: str, user_name: str = "", context: str = "",
           history: list = None, user_id: int = None) -> str | None:
    """Единая точка вызова AI: сначала Groq, fallback на Gemini."""
    # Groq (основной — быстрее)
    resp = ai.ask(user_message, user_name, context=context, history=history, user_id=user_id)
    if resp:
        return resp
    # Gemini (резерв — если Groq не ответил или ключ не задан)
    if gemini.enabled:
        return gemini.ask(user_message, user_name)
    return None

# ══════════════════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ
# ══════════════════════════════════════════════════════════════════════════════
_log_lock = threading.Lock()

def write_log(entry: str):
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {entry}"
    # Railway/gunicorn видит stdout в реальном времени → появляется в Deploy Logs
    print(line, flush=True)
    try:
        with _log_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > 2000:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines[-2000:])
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

            CREATE TABLE IF NOT EXISTS daily_stats (
                date       TEXT    NOT NULL,
                user_id    INTEGER NOT NULL,
                msg_count  INTEGER DEFAULT 0,
                PRIMARY KEY (date, user_id)
            );

            CREATE TABLE IF NOT EXISTS hourly_stats (
                date       TEXT    NOT NULL,
                hour       INTEGER NOT NULL,
                msg_count  INTEGER DEFAULT 0,
                PRIMARY KEY (date, hour)
            );

            CREATE TABLE IF NOT EXISTS mod_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                action     TEXT    NOT NULL,
                user_id    INTEGER,
                by_id      INTEGER,
                reason     TEXT    DEFAULT '',
                duration   INTEGER DEFAULT 0,
                ts         INTEGER DEFAULT 0
            );
        """)
        # Добавляем колонки XP/level к существующей таблице (если их нет)
        for col, default in [("xp", 0), ("level", 1), ("last_active_date", "''")]:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {'INTEGER' if col != 'last_active_date' else 'TEXT'} DEFAULT {default}")
            except Exception:
                pass  # колонка уже есть
        conn.commit()
        conn.close()

# ── Пользователи ──────────────────────────────────────────────────────────────
def record_user(user) -> None:
    if not user or getattr(user, "is_bot", False):
        return
    uid   = user.id
    now   = int(time.time())
    now_dt = datetime.datetime.utcnow() + datetime.timedelta(hours=3)  # МСК
    now_s = now_dt.strftime("%Y-%m-%d %H:%M МСК")
    today = now_dt.strftime("%Y-%m-%d")
    hour  = now_dt.hour
    uname = (getattr(user, "username", "") or "").strip()
    fname = (getattr(user, "first_name", "") or "").strip()

    with _db_lock:
        conn = get_db()
        try:
            row = conn.execute("SELECT user_id, first_seen FROM users WHERE user_id=?", (uid,)).fetchone()
            if row:
                conn.execute("""
                    UPDATE users SET first_name=?, username=?, last_seen=?, last_seen_dt=?,
                                     msg_count = msg_count + 1,
                                     xp = xp + 1,
                                     last_active_date = ?
                    WHERE user_id=?
                """, (fname, uname, now, now_s, today, uid))
            else:
                conn.execute("""
                    INSERT INTO users (user_id, first_name, username, msg_count,
                                       first_seen, last_seen, first_seen_dt, last_seen_dt,
                                       xp, level, last_active_date)
                    VALUES (?,?,?,1,?,?,?,?,1,1,?)
                """, (uid, fname, uname, now, now, now_s, now_s, today))
            if uname:
                conn.execute("INSERT OR IGNORE INTO usernames (user_id, username) VALUES (?,?)",
                             (uid, uname))
            # Обновляем уровень на основе XP
            xp_row = conn.execute("SELECT xp FROM users WHERE user_id=?", (uid,)).fetchone()
            if xp_row:
                new_level = _calc_level(xp_row[0])
                conn.execute("UPDATE users SET level=? WHERE user_id=?", (new_level, uid))
            # Дневная статистика
            conn.execute("""
                INSERT INTO daily_stats (date, user_id, msg_count) VALUES (?, ?, 1)
                ON CONFLICT(date, user_id) DO UPDATE SET msg_count = msg_count + 1
            """, (today, uid))
            # Почасовая статистика
            conn.execute("""
                INSERT INTO hourly_stats (date, hour, msg_count) VALUES (?, ?, 1)
                ON CONFLICT(date, hour) DO UPDATE SET msg_count = msg_count + 1
            """, (today, hour))
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
                SELECT first_name, username, msg_count, user_id, xp, level
                FROM users ORDER BY msg_count DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

# ── XP и уровни ───────────────────────────────────────────────────────────────
LEVEL_THRESHOLDS = [(1000, 5), (600, 4), (300, 3), (100, 2), (0, 1)]
# Крипто-ранги v6.0
LEVEL_NAMES = {
    1: "🌱 Hodler",
    2: "📊 Trader",
    3: "🐳 Whale",
    4: "🔱 Satoshi",
    5: "👑 Nakamoto",
}
LEVEL_NEXT_XP = {1: 100, 2: 300, 3: 600, 4: 1000, 5: None}

def _calc_level(xp: int) -> int:
    for threshold, lvl in LEVEL_THRESHOLDS:
        if xp >= threshold:
            return lvl
    return 1

def get_level_name(level: int) -> str:
    return LEVEL_NAMES.get(level, "🌱 Новичок")

def add_xp(uid: int, amount: int):
    """Добавляет или отнимает XP у пользователя."""
    with _db_lock:
        conn = get_db()
        try:
            conn.execute("UPDATE users SET xp = MAX(0, xp + ?) WHERE user_id=?", (amount, uid))
            xp_row = conn.execute("SELECT xp FROM users WHERE user_id=?", (uid,)).fetchone()
            if xp_row:
                new_level = _calc_level(xp_row[0])
                conn.execute("UPDATE users SET level=? WHERE user_id=?", (new_level, uid))
            conn.commit()
        finally:
            conn.close()

def get_daily_stats(date: str = None) -> dict:
    """Статистика за день (по умолчанию — сегодня МСК)."""
    if not date:
        date = (datetime.datetime.utcnow() + datetime.timedelta(hours=3)).strftime("%Y-%m-%d")
    with _db_lock:
        conn = get_db()
        try:
            total_msgs = conn.execute(
                "SELECT SUM(msg_count) FROM daily_stats WHERE date=?", (date,)
            ).fetchone()[0] or 0
            top_user = conn.execute("""
                SELECT u.first_name, u.username, ds.msg_count
                FROM daily_stats ds JOIN users u ON ds.user_id=u.user_id
                WHERE ds.date=? ORDER BY ds.msg_count DESC LIMIT 1
            """, (date,)).fetchone()
            new_users = conn.execute("""
                SELECT COUNT(*) FROM users
                WHERE last_active_date=? AND msg_count=1
            """, (date,)).fetchone()[0] or 0
            hourly = conn.execute("""
                SELECT hour, msg_count FROM hourly_stats WHERE date=? ORDER BY hour
            """, (date,)).fetchall()
            return {
                "date": date,
                "total_msgs": total_msgs,
                "top_user": dict(top_user) if top_user else None,
                "new_users": new_users,
                "hourly": [dict(r) for r in hourly],
            }
        finally:
            conn.close()

def get_inactive_users(days: int = 7) -> list[dict]:
    """Пользователи, не писавшие N+ дней."""
    cutoff = int(time.time()) - days * 86400
    with _db_lock:
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT first_name, username, user_id, last_seen, last_seen_dt
                FROM users WHERE last_seen < ? AND msg_count > 0
                ORDER BY last_seen DESC LIMIT 20
            """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

# ── Журнал модерации ──────────────────────────────────────────────────────────
def log_mod_action(action: str, user_id: int, by_id: int = 0,
                   reason: str = "", duration: int = 0):
    """Записывает действие модератора в журнал."""
    with _db_lock:
        conn = get_db()
        try:
            conn.execute("""
                INSERT INTO mod_log (action, user_id, by_id, reason, duration, ts)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (action, user_id, by_id, reason, duration, int(time.time())))
            conn.commit()
        finally:
            conn.close()

def get_mod_log(user_id: int = None, limit: int = 20) -> list[dict]:
    """Последние записи журнала (по пользователю или все)."""
    with _db_lock:
        conn = get_db()
        try:
            if user_id:
                rows = conn.execute("""
                    SELECT ml.*, u.first_name, u.username
                    FROM mod_log ml LEFT JOIN users u ON ml.user_id=u.user_id
                    WHERE ml.user_id=? ORDER BY ml.ts DESC LIMIT ?
                """, (user_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT ml.*, u.first_name, u.username
                    FROM mod_log ml LEFT JOIN users u ON ml.user_id=u.user_id
                    ORDER BY ml.ts DESC LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


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

    # 🤖 Если нет шаблонного ответа — спрашиваем AI (Groq → Gemini fallback)
    if use_ai and (ai.api_key or gemini.enabled):
        # История: сначала Redis, потом SQLite
        if user_id:
            history = get_chat_history_r(user_id, limit=5) or get_chat_history(user_id, limit=5)
        else:
            history = None
        # Добавляем глобальный контекст чата + память пользователя
        global_ctx = get_global_ctx(limit=20)
        user_mem   = get_user_memory_str(user_id) if user_id else ""
        topic      = get_chat_topic()
        context_parts = [c for c in [global_ctx, user_mem] if c]
        if topic:
            context_parts.insert(0, f"Сейчас в чате активно обсуждают: {topic}")
        context = "\n\n".join(context_parts)
        ai_response = ask_ai(text, name, context=context, history=history, user_id=user_id)
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
    ("биток", "btc", "биткоин", "bitcoin"): [
        "₿ {name}, биткоин — цифровое золото! Держишь? 💎",
        "🚀 BTC к луне, {name}? Или ждёшь просадку? 📉",
        "₿ {name}, видел /price btc? Проверяй прямо здесь! 📊",
    ],
    ("крипта", "крипто", "crypto", "альткоины", "defi", "nft"): [
        "🪙 Крипта — это серьёзно, {name}! Какой держишь портфель? 💼",
        "📊 {name}, используй /price чтобы следить за рынком!",
        "🔥 Крипта не спит, {name}! /fear — смотри индекс страха",
    ],
    ("эфир", "ethereum", "eth"): [
        "💎 ETH — умные контракты, DeFi, NFT. {name}, bullish? 🚀",
        "🔷 {name}, проверь /price eth — всегда актуальные данные!",
    ],
    ("фрс", "fed", "ставка", "пауэлл", "powell", "инфляция"): [
        "🏦 {name}, ФРС и крипта — всегда интрига! /calendar для событий 📅",
        "📊 Макро-данные влияют на BTC, {name}! Смотри /fear после заседаний ФРС",
    ],
    ("памп", "pump", "dump", "дамп", "лонг", "шорт"): [
        "📈 {name}, трейдинг — это наука! Умеешь читать уровни? 🎯",
        "🎲 {name}, рынок любит неожиданности. /fear покажет настроение толпы!",
    ],
}

# ── Ключевые слова для определения активной темы в чате ──────────────────────
_TOPIC_TRIGGERS: dict[str, str] = {
    "bitcoin": "Bitcoin", "биткоин": "Bitcoin", "btc": "Bitcoin", "биток": "Bitcoin",
    "ethereum": "Ethereum", "эфир": "Ethereum", "eth": "Ethereum",
    "solana": "Solana", "солана": "Solana",
    "defi": "DeFi", "дефи": "DeFi",
    "nft": "NFT",
    "шорт": "шортах", "шорты": "шортах", "лонг": "лонгах", "лонги": "лонгах",
    "фрс": "ФРС и ставках", "ставка": "ставках", "пауэлл": "ФРС",
    "памп": "движениях рынка", "дамп": "движениях рынка",
    "альтсезон": "альтсезоне", "альты": "альткоинах",
    "листинг": "листингах", "ton": "TON", "тон": "TON",
    "xrp": "XRP", "рипл": "XRP",
}

def _detect_topic(text: str):
    """Отслеживает повторяющиеся темы. После 5 упоминаний — сохраняет в Redis на 2ч."""
    t = text.lower()
    for kw, topic in _TOPIC_TRIGGERS.items():
        if kw in t:
            count = update_topic_keyword(kw)
            if count >= 5:
                set_chat_topic(topic)
            break

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
    """Приветствие + кнопка miniapp."""
    text = (
        "👮 <b>Statham Bot v5.0</b> — Railway + Redis + Crypto\n\n"
        "Слежу за порядком в <b>Statham Elite</b> 🚀\n\n"
        "📋 /rules — правила чата\n"
        "❓ /help — все команды\n"
        "📊 /price btc eth — цены крипты\n"
        "😱 /fear — Fear & Greed Index\n"
        "🔔 /alert btc 100000 — ценовой алерт\n"
        "📱 /app — открыть крипто-дашборд"
    )
    if PA_DOMAIN:
        url = f"https://{PA_DOMAIN}/miniapp/"
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton(
            text="📊 Открыть Statham App",
            web_app=telebot.types.WebAppInfo(url=url)
        ))
        try:
            bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=markup)
            return
        except Exception:
            pass
    _reply(m, text)

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
            "/users_stats — статистика участников\n"
            "/inactive [дни] — кто молчит N+ дней\n"
            "/modlog — журнал модерации\n"
            "/dailyreport — отчёт за сегодня"
        )
    _reply(m, (
        "👮 <b>Команды Statham Bot v5.0</b>\n\n"
        "<b>📋 Основные:</b>\n"
        "/start — приветствие\n"
        "/help — эта справка\n"
        "/rules — правила чата\n"
        "/mystats — твоя статистика + XP\n"
        "/rank — твой уровень и прогресс\n"
        "/top — топ-10 активных участников\n"
        "/report — пожаловаться (ответом на сообщение)\n\n"
        "<b>📊 Крипта — цены:</b>\n"
        "/price btc eth sol — цены монет\n"
        "/chart btc 7d — ASCII-график (1d/7d/30d)\n"
        "/movers — топ гейнеры/лузеры за 24ч\n"
        "/alts — альтсезон или биткоин-сезон?\n"
        "/funding btc — funding rate фьючерсов\n"
        "/tvl — DeFi TVL по сетям\n\n"
        "<b>📊 Крипта — инструменты:</b>\n"
        "/fear — Fear & Greed Index\n"
        "/market — сводка рынка (капа, доминация)\n"
        "/portfolio — мой крипто-портфель\n"
        "/alert btc 100000 — алерт когда BTC > $100k\n"
        "/alerts — мои алерты\n"
        "/calendar — экономический календарь (ФРС, CPI, NFP)\n\n"
        "<b>🎲 Ставки и игры:</b>\n"
        "/predict btc up — ставка на рост/падение (4ч)\n"
        "/predstats — моя статистика ставок\n"
        "/predtop — топ предсказателей\n"
        "/dailyvote — ежедневное голосование\n"
        "/achievements — мои достижения\n\n"
        "<b>🎮 Мини-игры:</b>\n"
        "/roll — кинуть кубик (1-100)\n"
        "/coin — подбросить монетку\n"
        "/fact — случайный факт\n"
        "/quiz — викторина (ответ числом 1-4)\n\n"
        "<b>🤖 AI (Groq + Gemini):</b>\n"
        "/ai [вопрос] — спросить AI\n"
        "/ask [вопрос] — тоже спросить AI\n"
        "!ai [вопрос] — вызвать AI прямо в чате\n\n"
        "<b>📝 Персонализация:</b>\n"
        "/remember [факт] — запомнить факт о себе\n"
        "/myfacts — показать мои факты\n"
        "/forget — забыть мои данные\n"
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
    xp    = user.get("xp", 0)
    level = user.get("level", 1)
    lvl_name = get_level_name(level)
    next_xp = LEVEL_NEXT_XP.get(level)
    xp_line = f"{xp} XP" + (f" / {next_xp} XP до следующего" if next_xp else " — максимум!")
    # Место в топе
    top = get_top_users(100)
    rank = next((i+1 for i, u in enumerate(top) if u["user_id"] == uid), "?")
    _reply(m, (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"👤 Имя: <b>{user['first_name']}</b>\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📎 Никнеймы: {unames}\n"
        f"💬 Сообщений: <b>{user['msg_count']}</b>\n"
        f"🏆 Место в топе: #{rank}\n"
        f"⭐ Уровень: {level} — {lvl_name}\n"
        f"✨ XP: {xp_line}\n"
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

def _apply_warn(chat_id, user, reason: str = "", by_id: int = 0):
    """Единая логика выдачи варна с прогрессивным мутом."""
    cnt, total = add_warn(user.id)
    add_xp(user.id, -10)  # -10 XP за нарушение
    log_mod_action("warn", user.id, by_id=by_id, reason=reason)
    write_log(f"WARN | {user.id} @{getattr(user,'username','')} | cnt={cnt} total={total} | {reason}")

    mute_mins = get_mute_duration(cnt)
    should_mute = cnt in [t for t, _ in MUTE_STEPS]

    if should_mute:
        try:
            _mute(chat_id, user.id, mute_mins)
            log_mod_action("mute", user.id, by_id=by_id, reason=f"авто-мут за {cnt} варна", duration=mute_mins)
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

    uid_rem = m.from_user.id
    # Сохраняем в Redis (основное) + SQLite (резерв)
    redis_saved = save_user_memory(uid_rem, "general", fact)
    sqlite_saved = save_user_fact(uid_rem, fact)
    if redis_saved or sqlite_saved:
        storage = "Redis 🔴" if redis_saved else "SQLite 🗄"
        _reply(m, f"✅ Запомнил ({storage}): <i>{fact}</i>\n\nБуду упоминать это в наших разговорах! 😊")
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

    if not check_rate_limit(m.from_user.id, "ai", max_calls=5, window=60):
        _reply(m, "⏳ Слишком много вопросов! Подожди минуту (лимит 5 в минуту).")
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

    # Получаем историю (Redis → SQLite fallback)
    uid = m.from_user.id
    history = get_chat_history_r(uid, limit=5) or get_chat_history(uid, limit=5)
    # Добавляем крипто-контекст если вопрос про крипту
    ctx_parts = ["Прямой вопрос через /ai команду"]
    if any(kw in question.lower() for kw in ["btc","eth","биток","крипта","bitcoin","crypto","цена","рынок"]):
        crypto_ctx = get_crypto_ai_context()
        if crypto_ctx: ctx_parts.append(crypto_ctx)
    global_ctx = get_global_ctx(limit=15)
    if global_ctx: ctx_parts.append(global_ctx)
    user_mem = get_user_memory_str(uid)
    if user_mem: ctx_parts.append(user_mem)
    context = "\n".join(ctx_parts)

    response = ask_ai(question, m.from_user.first_name, context=context,
                      history=history, user_id=uid)

    if response:
        label = "🤖 Groq" if ai.api_key else "🌟 Gemini"
        _reply(m, f"{label} <b>отвечает:</b>\n\n{response}")
    else:
        _reply(m, "❌ AI не смог ответить. Попробуй другой вопрос или повтори позже.")


@bot.message_handler(commands=["dialogstats"])
def cmd_dialogstats(m):
    """📊 Статистика общения с ботом."""
    uid = m.from_user.id
    # Redis count is more accurate (includes this session)
    count_r = get_chat_count_r(uid)
    count   = count_r or get_user_chat_count(uid)
    history = get_chat_history_r(uid, limit=3) or get_chat_history(uid, limit=3)

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

@bot.message_handler(commands=["rank"])
def cmd_rank(m):
    """⭐ Твой XP и уровень."""
    uid  = m.from_user.id
    user = get_user(uid)
    if not user:
        _reply(m, "📊 Ещё нет данных. Напиши что-нибудь в чат!"); return
    xp    = user.get("xp", 0)
    level = user.get("level", 1)
    lvl_name = get_level_name(level)
    next_xp = LEVEL_NEXT_XP.get(level)
    top = get_top_users(100)
    rank = next((i+1 for i, u in enumerate(top) if u["user_id"] == uid), "?")

    # Прогресс-бар XP
    if next_xp:
        prev_xp = {1: 0, 2: 100, 3: 300, 4: 600}.get(level, 0)
        progress = (xp - prev_xp) / (next_xp - prev_xp)
        bar_len = 10
        filled = int(progress * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        xp_line = f"{bar} {xp}/{next_xp} XP"
    else:
        xp_line = f"{'█' * 10} {xp} XP — МАКСИМУМ! 👑"

    _reply(m, (
        f"⭐ <b>Твой ранг, {user['first_name']}</b>\n\n"
        f"🏆 Место в чате: <b>#{rank}</b>\n"
        f"📊 Уровень: <b>{level}</b> — {lvl_name}\n"
        f"✨ {xp_line}\n"
        f"💬 Сообщений: {user['msg_count']}\n\n"
        f"🌱 Новичок → 🌿 Участник (100) → 🌳 Активный (300) → ⭐ Ветеран (600) → 👑 Легенда (1000)"
    ))

@bot.message_handler(commands=["inactive"])
def cmd_inactive(m):
    """😴 Список участников, не писавших 7+ дней (только для админов)."""
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    args = m.text.split()
    days = 7
    if len(args) > 1:
        try: days = max(1, int(args[1]))
        except ValueError: pass
    users = get_inactive_users(days)
    if not users:
        _reply(m, f"✅ Все активны — никто не молчал {days}+ дней."); return
    lines = [f"😴 <b>Не писали {days}+ дней ({len(users)} чел.):</b>\n"]
    for u in users[:15]:
        uname = f"@{u['username']}" if u.get("username") else f"ID {u['user_id']}"
        lines.append(f"• <b>{u['first_name']}</b> {uname} — {u['last_seen_dt']}")
    _reply(m, "\n".join(lines))

@bot.message_handler(commands=["modlog"])
def cmd_modlog(m):
    """📋 Журнал модерации (только для админов)."""
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    target_id = None
    if m.reply_to_message:
        target_id = m.reply_to_message.from_user.id
    elif len(m.text.split()) > 1:
        q = m.text.split()[1]
        u = find_user_by_query(q)
        if u: target_id = u["user_id"]
    logs = get_mod_log(user_id=target_id, limit=15)
    if not logs:
        _reply(m, "📋 Журнал пуст."); return
    action_icons = {"warn": "⚠️", "mute": "🔇", "ban": "🚫", "kick": "👢",
                    "unmute": "🔊", "unban": "✅"}
    lines = ["📋 <b>Журнал модерации</b>\n"]
    for log in logs:
        icon = action_icons.get(log["action"], "•")
        name = log.get("first_name") or f"ID {log['user_id']}"
        ts   = datetime.datetime.utcfromtimestamp(log["ts"]).strftime("%d.%m %H:%M")
        dur  = f" ({format_duration(log['duration'])})" if log.get("duration") else ""
        rsn  = f" — {log['reason']}" if log.get("reason") else ""
        lines.append(f"{icon} {ts} <b>{name}</b>{dur}{rsn}")
    _reply(m, "\n".join(lines))

@bot.message_handler(commands=["dailyreport"])
def cmd_dailyreport(m):
    """📊 Ежедневный отчёт прямо сейчас (только для админов)."""
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    _reply(m, _build_daily_report())


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

    if not check_rate_limit(m.from_user.id, "ai", max_calls=5, window=60):
        try:
            _reply_to_with_retry(m, "⏳ Слишком много вопросов! Подожди минуту.", parse_mode="HTML")
        except Exception:
            pass
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

    # Получаем историю (Redis → SQLite fallback)
    uid = m.from_user.id
    history = get_chat_history_r(uid, limit=5) or get_chat_history(uid, limit=5)
    user_mem = get_user_memory_str(uid)
    context = "Явный вызов через !ai префикс"
    if user_mem: context += "\n" + user_mem

    response = ask_ai(question, m.from_user.first_name, context=context,
                      history=history, user_id=uid)

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


def _update_user_interests(uid: int, name: str):
    """Фоновое обновление профиля интересов пользователя через AI (каждые 10 сообщений)."""
    try:
        history = get_chat_history_r(uid, limit=10) or get_chat_history(uid, limit=10)
        if not history or len(history) < 3:
            return
        msgs = "\n".join(f"- {h['user_msg'][:80]}" for h in history[-10:])
        prompt = (
            f"Определи 2-3 ключевых интереса пользователя {name} по его сообщениям. "
            "Одна строка, максимум 80 символов. Пример: 'трейдер, держит BTC/SOL, интересуется DeFi'. "
            f"Сообщения:\n{msgs}"
        )
        summary = ask_ai(prompt, "")
        if summary and 5 < len(summary) < 120:
            save_user_memory(uid, "interests", summary.strip()[:100])
            write_log(f"USER_PROFILE | uid={uid} | {summary[:50]}")
    except Exception as e:
        write_log(f"USER_PROFILE_ERR | {uid} | {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ
# ══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/"), content_types=["text"])
def handle_message(message):
    # Игнорируем старые сообщения
    if time.time() - message.date > 60:
        return

    user = message.from_user
    record_user(user)

    # ── Redis: контекст чата + тема + профиль пользователя ───────────────────
    if message.text:
        add_global_ctx(user.first_name or "User", message.text)
        _detect_topic(message.text)
    msg_count = incr_user_msg_count(user.id)
    if msg_count % 10 == 0 and (ai.api_key or gemini.enabled):
        threading.Thread(target=_update_user_interests,
                         args=(user.id, user.first_name), daemon=True).start()

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
    if should_random_reply() and (ai.api_key or gemini.enabled):
        uid_r = user.id
        history = get_chat_history_r(uid_r, limit=5) or get_chat_history(uid_r, limit=5)
        global_ctx = get_global_ctx(limit=15)
        ai_response = ask_ai(message.text[:200], user.first_name,
                             context="Случайный ответ в чате\n" + global_ctx,
                             history=history, user_id=uid_r)
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

    # ── Антифлуд (Redis → SQLite fallback) ──────────────────────────────────
    if check_flood_r(uid) or check_flood(uid):
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
# 💎 КРИПТО-КОМАНДЫ (v5.0)
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["price", "p", "цена"])
def cmd_price(m):
    """📊 Цены криптовалют. /price btc eth sol"""
    uid = m.from_user.id
    if not check_rate_limit(uid, "price", max_calls=10, window=60):
        _reply(m, "⏳ Слишком частые запросы. Подожди минуту."); return
    parts = m.text.split()[1:]
    if not parts:
        _reply(m, (
            "📊 <b>Использование:</b> /price [монеты]\n\n"
            "Примеры:\n"
            "• /price btc\n"
            "• /price eth sol bnb\n"
            "• /price ton биток\n\n"
            "Поддерживаются: btc, eth, sol, bnb, xrp, ton, ada, doge, ltc, avax, dot, link..."
        )); return
    try:
        bot.send_chat_action(m.chat.id, "typing")
    except Exception:
        pass
    coins = [c.lower() for c in parts[:5]]
    write_log(f"CMD /price | uid={uid} | coins={coins}")
    msg = format_price_message(coins)
    write_log(f"CMD /price OK | uid={uid} | len={len(msg)}")
    _reply(m, msg)
    if give_achievement(uid, "first_price"):
        try: bot.send_message(uid, "🏅 Достижение: 📊 Первый запрос цены (+5 XP)", parse_mode="HTML")
        except Exception: pass
    add_xp(uid, 2)


@bot.message_handler(commands=["fear", "fng", "страх"])
def cmd_fear(m):
    """😱 Fear & Greed Index"""
    uid = m.from_user.id
    if not check_rate_limit(uid, "fear", max_calls=5, window=60):
        _reply(m, "⏳ Слишком частые запросы."); return
    try:
        bot.send_chat_action(m.chat.id, "typing")
    except Exception:
        pass
    write_log(f"CMD /fear | uid={uid}")
    msg = format_fear_greed()
    write_log(f"CMD /fear OK | uid={uid} | val={msg[:40].replace(chr(10),' ')}")
    _reply(m, msg)
    if give_achievement(uid, "first_fear"):
        try: bot.send_message(uid, "🏅 Достижение: 😱 Первый F&G запрос (+5 XP)", parse_mode="HTML")
        except Exception: pass


@bot.message_handler(commands=["market", "cap", "рынок"])
def cmd_market(m):
    """🌍 Сводка крипторынка"""
    uid = m.from_user.id
    if not check_rate_limit(uid, "market", max_calls=5, window=60):
        _reply(m, "⏳ Слишком частые запросы."); return
    try:
        bot.send_chat_action(m.chat.id, "typing")
    except Exception:
        pass
    write_log(f"CMD /market | uid={uid}")
    msg = format_market_message()
    write_log(f"CMD /market OK | uid={uid}")
    _reply(m, msg)


@bot.message_handler(commands=["alert", "алерт"])
def cmd_alert(m):
    """🔔 Ценовой алерт. /alert btc 100000 или /alert btc below 80000"""
    parts = m.text.split()[1:]
    if not parts or len(parts) < 2:
        _reply(m, (
            "🔔 <b>Ценовой алерт</b>\n\n"
            "Использование:\n"
            "• <code>/alert btc 100000</code> — уведомить когда BTC выше $100k\n"
            "• <code>/alert eth below 3000</code> — уведомить когда ETH ниже $3000\n"
            "• <code>/alert btc above 95000</code> — явно выше\n\n"
            "Смотреть алерты: /alerts\n"
            "Удалить: /delalert btc"
        )); return
    coin = parts[0].lower()
    # Разбираем direction
    if len(parts) == 3:
        direction_raw = parts[1].lower()
        direction = "below" if direction_raw in ("below", "ниже", "под") else "above"
        try:
            target = float(parts[2].replace(",", ""))
        except ValueError:
            _reply(m, "❌ Неверная цена. Пример: /alert btc 100000"); return
    else:
        direction = "above"
        try:
            target = float(parts[1].replace(",", ""))
        except ValueError:
            _reply(m, "❌ Неверная цена. Пример: /alert btc 100000"); return

    if coin not in COIN_ALIASES and len(coin) > 10:
        _reply(m, f"❌ Неизвестная монета: {coin.upper()}. Попробуй btc, eth, sol..."); return

    symbol = coin.upper()
    dir_str = "выше" if direction == "above" else "ниже"

    if add_price_alert(m.from_user.id, coin, target, direction):
        _reply(m, f"🔔 Алерт установлен!\n\n"
                  f"Уведомлю когда <b>{symbol}</b> будет {dir_str} "
                  f"<b>${target:,.0f}</b>\n\n"
                  f"<i>Проверяется каждые 5 минут</i>")
        give_achievement(m.from_user.id, "first_alert")
    else:
        _reply(m, "❌ Не удалось установить алерт. Redis должен быть подключён.")


@bot.message_handler(commands=["alerts", "алерты"])
def cmd_alerts(m):
    """🔔 Мои ценовые алерты"""
    alerts = get_user_alerts(m.from_user.id)
    if not alerts:
        _reply(m, "🔔 Нет активных алертов.\n\nУстанови: /alert btc 100000"); return
    lines = ["🔔 <b>Твои алерты:</b>\n"]
    for a in alerts:
        dir_str = "📈 выше" if a["dir"] == "above" else "📉 ниже"
        lines.append(f"• <b>{a['coin']}</b> {dir_str} ${a['target']:,.0f}")
    lines.append("\nУдалить: /delalert btc")
    _reply(m, "\n".join(lines))


@bot.message_handler(commands=["delalert", "удалерт"])
def cmd_delalert(m):
    """🗑 Удалить алерт. /delalert btc"""
    parts = m.text.split()[1:]
    if not parts:
        _reply(m, "Использование: /delalert btc"); return
    coin = parts[0].upper()
    remove_alert(m.from_user.id, coin)
    _reply(m, f"✅ Алерт на {coin} удалён.")


@bot.message_handler(commands=["forget", "забудь"])
def cmd_forget(m):
    """🗑 Забыть мою историю диалогов и память"""
    uid_f = m.from_user.id
    clear_chat_history_r(uid_f)
    # Чистим и из SQLite
    with _db_lock:
        conn = get_db()
        try:
            conn.execute("DELETE FROM chat_history WHERE user_id=?", (uid_f,))
            conn.commit()
        finally:
            conn.close()
    _reply(m, (
        "✅ Готово! Забыл нашу историю диалогов.\n\n"
        "Факты о тебе (<code>/remember</code>) сохранены.\n"
        "Чтобы удалить — используй <code>/myfacts</code> и скажи мне что удалить."
    ))


@bot.message_handler(commands=["redisstat", "redis"])
def cmd_redis_stat(m):
    """📊 Статус Redis (только для админов)"""
    if not is_admin(m.chat.id, m.from_user.id):
        _reply(m, "❌ Только для администраторов."); return
    stats = redis_stats()
    gemini_status = "✅ Подключён" if gemini.enabled else "❌ Ключ не задан"
    groq_status = "✅ Подключён" if ai.api_key else "❌ Ключ не задан"
    lines = [
        "🔴 <b>Redis статус</b>",
        f"Статус: {stats.get('status', '?')}",
        f"Ключей: {stats.get('keys', '?')}",
        f"Память: {stats.get('memory', '?')}",
        "",
        "🤖 <b>AI статус</b>",
        f"Groq: {groq_status}",
        f"Gemini: {gemini_status}",
    ]
    _reply(m, "\n".join(lines))


# ══════════════════════════════════════════════════════════════════════════════
# 📊 НОВЫЕ КРИПТО-КОМАНДЫ v6.0 (Уровень 2)
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["news", "новости"])
def cmd_news(m):
    """📰 Крипто-новости из RSS. /news [кол-во]"""
    if not check_rate_limit(m.from_user.id, "news", max_calls=3, window=60):
        _reply(m, "⏳ Подожди немного."); return
    try:
        bot.send_chat_action(m.chat.id, "typing")
    except Exception:
        pass
    parts = m.text.split()
    limit = 5
    if len(parts) > 1:
        try: limit = max(1, min(10, int(parts[1])))
        except ValueError: pass
    news = get_crypto_news(limit=limit)
    msg = format_news_message(news)
    _reply(m, msg)
    add_xp(m.from_user.id, 1)


@bot.message_handler(commands=["chart", "график"])
def cmd_chart(m):
    """📈 ASCII-график цены. /chart btc 7d"""
    parts = m.text.split()[1:]
    if not parts:
        _reply(m, "📈 Использование: /chart [монета] [период]\n\nПримеры:\n• /chart btc 7d\n• /chart eth 30d\n• /chart sol 1d\n\nПериоды: 1d, 7d, 30d, 90d"); return
    coin   = parts[0].lower()
    period = parts[1].lower() if len(parts) > 1 else "7d"
    try: bot.send_chat_action(m.chat.id, "typing")
    except Exception: pass
    _reply(m, format_chart_message(coin, period))


@bot.message_handler(commands=["movers", "топ", "движение"])
def cmd_movers(m):
    """🚀 Топ гейнеры и лузеры за 24ч из топ-100."""
    uid = m.from_user.id
    if not check_rate_limit(uid, "movers", max_calls=5, window=60):
        _reply(m, "⏳ Подожди немного."); return
    try: bot.send_chat_action(m.chat.id, "typing")
    except Exception: pass
    write_log(f"CMD /movers | uid={uid}")
    msg = format_movers_message()
    write_log(f"CMD /movers OK | uid={uid} | len={len(msg)}")
    _reply(m, msg)


@bot.message_handler(commands=["alts", "альтсезон", "altseason"])
def cmd_alts(m):
    """🌈 Альтсезон или биткоин-сезон?"""
    uid = m.from_user.id
    try: bot.send_chat_action(m.chat.id, "typing")
    except Exception: pass
    write_log(f"CMD /alts | uid={uid}")
    msg = format_altseason_message()
    write_log(f"CMD /alts OK | uid={uid} | len={len(msg)}")
    _reply(m, msg)


@bot.message_handler(commands=["funding", "фандинг"])
def cmd_funding(m):
    """💸 Funding rate фьючерсов. /funding btc"""
    uid = m.from_user.id
    parts = m.text.split()[1:]
    coin = parts[0].lower() if parts else "btc"
    try: bot.send_chat_action(m.chat.id, "typing")
    except Exception: pass
    write_log(f"CMD /funding | uid={uid} | coin={coin}")
    msg = format_funding_message(coin)
    write_log(f"CMD /funding OK | uid={uid}")
    _reply(m, msg)


@bot.message_handler(commands=["tvl", "defi"])
def cmd_tvl(m):
    """🏦 DeFi TVL по сетям (DeFiLlama)."""
    uid = m.from_user.id
    try: bot.send_chat_action(m.chat.id, "typing")
    except Exception: pass
    write_log(f"CMD /tvl | uid={uid}")
    msg = format_tvl_message()
    write_log(f"CMD /tvl OK | uid={uid}")
    _reply(m, msg)


@bot.message_handler(commands=["portfolio", "port", "портфель"])
def cmd_portfolio(m):
    """💼 Крипто-портфель пользователя."""
    parts = m.text.split()[1:]
    result = handle_portfolio_command(m.from_user.id, parts)
    _reply(m, result)
    # Ачивка при первом добавлении
    if parts and parts[0].lower() in ("add", "set", "добавить", "установить"):
        if give_achievement(m.from_user.id, "first_portfolio"):
            try: bot.send_message(m.from_user.id,
                "🏅 Достижение: 💼 Создал портфель (+15 XP)", parse_mode="HTML")
            except Exception: pass
            add_xp(m.from_user.id, 15)


# ══════════════════════════════════════════════════════════════════════════════
# 📅 ЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ (Уровень 5)
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["calendar", "cal", "календарь", "события"])
def cmd_calendar(m):
    """📅 Экономический календарь: ФРС, CPI, NFP."""
    try: bot.send_chat_action(m.chat.id, "typing")
    except Exception: pass
    _reply(m, format_calendar_message(days_ahead=7))


# ══════════════════════════════════════════════════════════════════════════════
# 🎲 ГЕЙМИФИКАЦИЯ (Уровень 4)
# ══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["predict", "ставка", "прогноз"])
def cmd_predict(m):
    """🔮 Ставка на направление. /predict btc up"""
    parts = m.text.split()[1:]
    if len(parts) < 2:
        _reply(m, (
            "🔮 <b>Ставки на направление</b>\n\n"
            "Использование:\n"
            "• <code>/predict btc up</code> — BTC вырастет за 4 часа\n"
            "• <code>/predict eth down</code> — ETH упадёт за 4 часа\n\n"
            "Результат проверяется автоматически через 4 часа.\n"
            "За правильный ответ: +25 XP 🎯"
        )); return

    from crypto_module import COIN_ALIASES, get_prices
    coin_input = parts[0].lower()
    direction_raw = parts[1].lower()

    if direction_raw in ("up", "вверх", "рост", "pump", "🚀", "лонг", "long"):
        direction = "up"
        dir_str = "🟢 вырастет"
    elif direction_raw in ("down", "вниз", "падение", "dump", "📉", "шорт", "short"):
        direction = "down"
        dir_str = "🔴 упадёт"
    else:
        _reply(m, "❌ Направление: up (рост) или down (падение)"); return

    coin_id = COIN_ALIASES.get(coin_input, coin_input)
    prices = get_prices([coin_input])
    if "_error" in prices or not prices:
        _reply(m, "❌ Монета не найдена."); return

    d = list(prices.values())[0]
    current = d["price"]
    sym = d["symbol"]

    uid = m.from_user.id
    name = m.from_user.first_name or "Аноним"
    ok = make_prediction(uid, name, coin_id, direction, current)
    if not ok:
        _reply(m, f"⚠️ У тебя уже есть активная ставка на <b>{sym}</b>. Дождись результата.")
        return

    give_achievement(uid, "first_predict")
    add_xp(uid, 5)
    from crypto_module import _fmt_price
    _reply(m, (
        f"🔮 <b>Ставка принята!</b>\n\n"
        f"<b>{sym}</b> {dir_str} за 4 часа\n"
        f"Текущая цена: {_fmt_price(current)}\n\n"
        f"⏰ Результат через 4 часа\n"
        f"🎯 Угадаешь — получишь +25 XP"
    ))


@bot.message_handler(commands=["predstats", "стата", "предсказания"])
def cmd_predstats(m):
    """📊 Статистика предсказаний."""
    name = m.from_user.first_name or "Аноним"
    _reply(m, format_predict_stats(m.from_user.id, name))


@bot.message_handler(commands=["predtop", "топставки"])
def cmd_predtop(m):
    """🏆 Топ предсказателей."""
    board = get_prediction_leaderboard(10)
    if not board:
        _reply(m, "🏆 Пока нет данных. Первым сделай ставку: /predict btc up"); return
    lines = ["🏆 <b>Топ предсказателей</b>\n"]
    medals = ["🥇", "🥈", "🥉"] + ["  "] * 10
    for i, row in enumerate(board):
        lines.append(
            f"{medals[i]} #{i+1} — "
            f"Точность: <b>{row['winrate']}%</b> "
            f"({row['wins']}/{row['total']})"
        )
    _reply(m, "\n".join(lines))


@bot.message_handler(commands=["dailyvote", "голос", "vote"])
def cmd_dailyvote(m):
    """📊 Ежедневное голосование с кнопками."""
    import datetime
    question = get_daily_question()
    qid = datetime.date.today().isoformat()
    options_text = "\n".join([f"{i+1}. {o}" for i, o in enumerate(question["options"])])
    results = get_vote_results(qid, question["options"])
    total = results.get("total", 0) if results else 0

    # Создаём клавиатуру
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    btns = [telebot.types.InlineKeyboardButton(
        text=f"{i+1}. {opt}",
        callback_data=f"vote:{qid}:{i}"
    ) for i, opt in enumerate(question["options"])]
    markup.add(*btns)

    text = (
        f"📊 <b>Вопрос дня</b>\n\n"
        f"{question['text']}\n\n"
        f"👥 Уже проголосовало: <b>{total}</b>"
    )
    try:
        bot.send_message(m.chat.id, text, parse_mode="HTML",
                        reply_markup=markup)
    except Exception as e:
        _reply(m, text + "\n\n" + options_text)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("vote:"))
def handle_vote_callback(call):
    """Обработка голосования через inline кнопки."""
    import datetime
    parts = call.data.split(":")
    if len(parts) != 3:
        return
    qid = parts[1]; option_idx = int(parts[2])
    question = get_daily_question()
    current_qid = datetime.date.today().isoformat()
    if qid != current_qid:
        bot.answer_callback_query(call.id, "⏰ Это голосование уже устарело.")
        return
    ok, msg = vote(call.from_user.id, qid, option_idx)
    if not ok:
        bot.answer_callback_query(call.id, msg)
        return
    add_xp(call.from_user.id, 3)
    bot.answer_callback_query(call.id, "✅ Голос принят! +3 XP")
    # Обновляем сообщение с результатами
    try:
        results_text = format_vote_results(qid, question["text"], question["options"])
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        btns = [telebot.types.InlineKeyboardButton(
            text=f"{i+1}. {opt}",
            callback_data=f"vote:{qid}:{i}"
        ) for i, opt in enumerate(question["options"])]
        markup.add(*btns)
        bot.edit_message_text(results_text, call.message.chat.id,
                             call.message.message_id,
                             parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass


@bot.message_handler(commands=["achievements", "ачивки", "бейджи"])
def cmd_achievements(m):
    """🏅 Мои достижения."""
    _reply(m, format_achievements(m.from_user.id))


@bot.message_handler(commands=["summary", "итог"])
def cmd_summary(m):
    """📝 AI-суммаризация последних сообщений чата."""
    if not (ai.api_key or gemini.enabled):
        _reply(m, "❌ AI не настроен."); return
    ctx = get_global_ctx(limit=30)
    if not ctx:
        _reply(m, "📝 Нет достаточно сообщений для суммаризации."); return
    try: bot.send_chat_action(m.chat.id, "typing")
    except Exception: pass
    prompt = f"Суммаризируй кратко (3-5 предложений) о чём говорили в чате:\n\n{ctx}"
    resp = ask_ai(prompt, "Summary", context="Задача: краткое резюме чата на русском.")
    if resp:
        _reply(m, f"📝 <b>О чём говорили в чате:</b>\n\n{resp}")
    else:
        _reply(m, "❌ AI не смог обработать запрос.")


# ══════════════════════════════════════════════════════════════════════════════
# 📱 TELEGRAM MINI APP
# ══════════════════════════════════════════════════════════════════════════════

def _send_miniapp_button(chat_id: int, message_thread_id: int = None):
    """Отправляет сообщение с кнопкой miniapp. web_app для личек, URL для групп."""
    if not PA_DOMAIN:
        return False
    url = f"https://{PA_DOMAIN}/miniapp/"
    markup = telebot.types.InlineKeyboardMarkup()
    # web_app работает только в личке (chat_id > 0); для групп используем URL-кнопку
    if chat_id > 0:
        markup.add(telebot.types.InlineKeyboardButton(
            text="📊 Открыть Statham App",
            web_app=telebot.types.WebAppInfo(url=url)
        ))
    else:
        markup.add(telebot.types.InlineKeyboardButton(
            text="📊 Открыть Statham App",
            url=url
        ))
    kw = {"parse_mode": "HTML", "reply_markup": markup}
    if message_thread_id:
        kw["message_thread_id"] = message_thread_id
    try:
        bot.send_message(
            chat_id,
            "📱 <b>Statham Mini App</b>\n\n"
            "Крипто-дашборд прямо в Telegram:\n"
            "• 📊 Цены Топ-5 и Топ-50 монет + Fear&Greed\n"
            "• 💼 Портфель с оценкой P&L\n"
            "• 🔔 Ценовые алерты (бот уведомит!)\n"
            "• 📅 Экономический календарь (ФРС, CPI, NFP)\n"
            "• 📰 Крипто-новости\n\n"
            f"<i>Или открой прямо: {url}</i>",
            **kw
        )
        return True
    except Exception:
        return False

@bot.message_handler(commands=["app", "webapp", "mini", "miniapp", "минипп", "портал", "дашборд"])
def cmd_webapp(m):
    """📱 Открыть Mini App — портфель, цены, алерты, календарь."""
    write_log(f"CMD /app | uid={m.from_user.id} | chat={m.chat.id}")
    if not PA_DOMAIN:
        _reply(m, "❌ RAILWAY_DOMAIN не задан в переменных окружения Railway."); return
    thread_id = getattr(m, "message_thread_id", None)
    if not _send_miniapp_button(m.chat.id, message_thread_id=thread_id):
        url = f"https://{PA_DOMAIN}/miniapp/"
        _reply(m, f"📱 <b>Statham App:</b> {url}")

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
    """Умный дашборд статистики с графиками."""
    try:
        with _db_lock:
            conn = get_db()
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            top_active  = conn.execute("""
                SELECT first_name, username, msg_count, xp, level
                FROM users ORDER BY msg_count DESC LIMIT 10
            """).fetchall()
            active_warns = conn.execute("""
                SELECT w.user_id, w.count, w.total_warns, u.first_name, u.username
                FROM warns w LEFT JOIN users u ON w.user_id=u.user_id
                WHERE w.count > 0
            """).fetchall()
            conn.close()

        stats = get_daily_stats()
        hourly = stats.get("hourly", [])
        hours_labels = [f"{h}:00" for h in range(24)]
        hours_data   = [0] * 24
        for h in hourly:
            hours_data[h["hour"]] = h["msg_count"]

        # Топ-10 для бар-чарта
        top_names  = [u["first_name"][:12] for u in top_active]
        top_msgs   = [u["msg_count"] for u in top_active]
        top_xp     = [u.get("xp", 0) for u in top_active]

        level_icons = {1:"🌱",2:"🌿",3:"🌳",4:"⭐",5:"👑"}

        rows_html = ""
        for i, u in enumerate(top_active):
            uname   = f"@{u['username']}" if u["username"] else "—"
            lvl     = u.get("level", 1)
            icon    = level_icons.get(lvl, "🌱")
            pct     = min(100, int(u["msg_count"] / max(top_msgs[0], 1) * 100))
            rows_html += f"""
            <tr>
              <td>{'🥇' if i==0 else '🥈' if i==1 else '🥉' if i==2 else f'{i+1}.'}</td>
              <td><b>{u['first_name']}</b><br><small>{uname}</small></td>
              <td>{u['msg_count']}</td>
              <td>{icon} {u.get('xp',0)} XP</td>
              <td><div class="bar"><div class="bar-fill" style="width:{pct}%"></div></div></td>
            </tr>"""

        warns_html = "".join(
            f"<span class='warn-badge'>⚠️ {w['first_name'] or w['user_id']} — {w['count']}/3</span>"
            for w in active_warns
        ) or "<span style='color:#6ee7b7'>✅ Нет активных варнов</span>"

        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>📊 Statham Bot Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0f172a;color:#e2e8f0;font-family:'Segoe UI',sans-serif;padding:20px}}
  h1{{text-align:center;font-size:1.6em;margin-bottom:20px;color:#7dd3fc}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}}
  .card{{background:#1e293b;border-radius:12px;padding:16px;text-align:center}}
  .card .num{{font-size:2em;font-weight:700;color:#7dd3fc}}
  .card .lbl{{font-size:.8em;color:#94a3b8;margin-top:4px}}
  .section{{background:#1e293b;border-radius:12px;padding:16px;margin-bottom:16px}}
  .section h2{{font-size:1em;color:#7dd3fc;margin-bottom:12px}}
  table{{width:100%;border-collapse:collapse;font-size:.85em}}
  td,th{{padding:8px 6px;border-bottom:1px solid #334155;text-align:left}}
  th{{color:#94a3b8;font-weight:500}}
  .bar{{background:#334155;border-radius:4px;height:8px;width:100px}}
  .bar-fill{{background:#3b82f6;border-radius:4px;height:8px;transition:.3s}}
  .warn-badge{{display:inline-block;background:#7f1d1d;color:#fca5a5;padding:4px 10px;
               border-radius:8px;margin:4px;font-size:.85em}}
  canvas{{max-height:200px}}
  .ts{{text-align:center;color:#475569;font-size:.75em;margin-top:16px}}
</style>
</head>
<body>
<h1>📊 Statham Elite — Dashboard</h1>
<div class="grid">
  <div class="card"><div class="num">{total_users}</div><div class="lbl">Участников</div></div>
  <div class="card"><div class="num">{stats['total_msgs']}</div><div class="lbl">Сообщений сегодня</div></div>
  <div class="card"><div class="num">{stats['new_users']}</div><div class="lbl">Новых сегодня</div></div>
  <div class="card"><div class="num">{len(active_warns)}</div><div class="lbl">Активных варнов</div></div>
</div>

<div class="section">
  <h2>🕐 Активность по часам (МСК, сегодня)</h2>
  <canvas id="hourChart"></canvas>
</div>

<div class="section">
  <h2>🏆 Топ-10 участников</h2>
  <table>
    <tr><th>#</th><th>Имя</th><th>Сообщ.</th><th>XP</th><th>Прогресс</th></tr>
    {rows_html}
  </table>
</div>

<div class="section">
  <h2>⚠️ Активные варны</h2>
  {warns_html}
</div>

<div class="ts">Обновлено: {(datetime.datetime.utcnow()+datetime.timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} МСК</div>

<script>
new Chart(document.getElementById('hourChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(hours_labels)},
    datasets: [{{
      label: 'Сообщений',
      data: {json.dumps(hours_data)},
      backgroundColor: 'rgba(59,130,246,0.7)',
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{legend: {{display: false}}}},
    scales: {{
      x: {{ticks: {{color:'#94a3b8', maxRotation:0}}, grid: {{color:'#1e293b'}}}},
      y: {{ticks: {{color:'#94a3b8'}}, grid: {{color:'#334155'}}}}
    }}
  }}
}});
</script>
</body></html>"""
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        return f"<pre>Error: {e}</pre>", 200, {"Content-Type": "text/html; charset=utf-8"}


# ══════════════════════════════════════════════════════════════════════════════
# 📱 MINIAPP BACKEND API  (проксируем внешние API через Railway)
# ══════════════════════════════════════════════════════════════════════════════
# Miniapp вызывает эти эндпоинты — Railway → CoinGecko/alternative.me
# Это обходит CORS-ограничения и даёт Binance-fallback + кэш

@app.route("/api/prices")
def api_prices():
    """Цены монет для miniapp. ?coins=btc,eth,sol или топ-5 по умолчанию."""
    from flask import jsonify, request as freq
    from crypto_module import get_prices, COIN_ALIASES
    raw = freq.args.get("coins", "")
    if raw:
        coins = [c.strip().lower() for c in raw.split(",") if c.strip()][:10]
    else:
        coins = ["btc", "eth", "sol", "bnb", "ton"]
    data = get_prices(coins)
    if "_error" in data:
        return jsonify({"error": data["_error"]}), 503
    result = []
    for coin_id, d in data.items():
        if not isinstance(d, dict): continue
        result.append({
            "id": coin_id,
            "symbol": d.get("symbol", "").upper(),
            "name": d.get("name", ""),
            "price": d.get("price"),
            "change_24h": d.get("change_24h"),
            "change_7d": d.get("change_7d"),
            "market_cap": d.get("market_cap"),
            "rank": d.get("rank"),
            "source": d.get("_source", "coingecko"),
        })
    return jsonify(result)


@app.route("/api/fear")
def api_fear():
    """Fear & Greed Index для miniapp."""
    from flask import jsonify
    from crypto_module import get_fear_greed
    d = get_fear_greed()
    if not d:
        return jsonify({"error": "unavailable"}), 503
    return jsonify(d)


@app.route("/api/alerts")
def api_alerts_get():
    """GET /api/alerts?uid=<user_id> — алерты пользователя из Redis."""
    from flask import jsonify, request as freq
    uid = freq.args.get("uid", "")
    if not uid or not str(uid).lstrip("-").isdigit():
        return jsonify([])
    alerts = get_user_alerts(int(uid))
    return jsonify(alerts or [])


@app.route("/api/alerts", methods=["POST"])
def api_alerts_add():
    """POST /api/alerts — добавить алерт в Redis (miniapp → бот)."""
    from flask import jsonify, request as freq
    try:
        body = freq.get_json(force=True)
        uid  = int(body.get("uid", 0))
        coin = str(body.get("coin", "")).lower()
        target = float(body.get("target", 0))
        direction = str(body.get("dir", "above"))
        if not uid or not coin or not target or direction not in ("above", "below"):
            return jsonify({"ok": False, "error": "bad_params"}), 400
        ok = add_price_alert(uid, coin, target, direction)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/alerts/<coin>", methods=["DELETE"])
def api_alerts_del(coin):
    """DELETE /api/alerts/<coin>?uid=<user_id> — удалить алерт из Redis."""
    from flask import jsonify, request as freq
    uid = freq.args.get("uid", "")
    if not uid or not str(uid).lstrip("-").isdigit():
        return jsonify({"ok": False}), 400
    remove_alert(int(uid), coin.upper())
    return jsonify({"ok": True})


@app.route("/api/portfolio")
def api_portfolio_get():
    """GET /api/portfolio?uid=<user_id> — портфель пользователя из Redis."""
    from flask import jsonify, request as freq
    from portfolio_module import port_get
    uid = freq.args.get("uid", "")
    if not uid or not str(uid).lstrip("-").isdigit():
        return jsonify({})
    holdings = port_get(int(uid))
    return jsonify(holdings)


@app.route("/api/portfolio", methods=["POST"])
def api_portfolio_add():
    """POST /api/portfolio — добавить/обновить монету в портфель."""
    from flask import jsonify, request as freq
    from portfolio_module import port_add, port_set, port_remove
    from crypto_module import COIN_ALIASES
    try:
        body   = freq.get_json(force=True)
        uid    = int(body.get("uid", 0))
        coin   = str(body.get("coin", "")).lower().strip()
        amount = float(body.get("amount", 0))
        action = str(body.get("action", "add"))  # add | set | remove
        if not uid or not coin:
            return jsonify({"ok": False, "error": "bad_params"}), 400
        coin_id = COIN_ALIASES.get(coin, coin)
        if action == "remove":
            ok = port_remove(uid, coin_id)
        elif action == "set":
            ok = port_set(uid, coin_id, amount)
        else:
            ok = port_add(uid, coin_id, amount)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/news")
def api_news():
    """Крипто-новости для miniapp (RSS + CryptoPanic если есть ключ).
    ?limit=N  — кол-во новостей (макс 20)
    ?hours=N  — только за последние N часов (0 = все)
    """
    from flask import jsonify, request as freq
    import time as _time
    limit = min(int(freq.args.get("limit", 10)), 20)
    hours = int(freq.args.get("hours", 0))
    try:
        news = get_crypto_news(limit=limit + 5)  # берём с запасом для фильтрации
        if hours > 0:
            cutoff = _time.time() - hours * 3600
            news = [n for n in news if n.get("pub_ts", 0) >= cutoff or n.get("pub_ts", 0) == 0]
        return jsonify(news[:limit])
    except Exception:
        return jsonify([])


@app.route("/api/prices/top50")
def api_prices_top50():
    """ТОП-50 монет по объёму торгов (Binance USDT пары)."""
    from flask import jsonify
    from chart_module import _SKIP_COINS, _SKIP_SUFFIXES
    from crypto_module import _cache_get, _cache_set
    cached = _cache_get("top50")
    if cached:
        return jsonify(cached)
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10)
        if r.status_code != 200:
            return jsonify([])
        tickers = [
            t for t in r.json()
            if t["symbol"].endswith("USDT")
            and float(t.get("lastPrice", 0)) > 0
            and float(t.get("quoteVolume", 0)) > 1_000_000
            and t["symbol"].replace("USDT","") not in _SKIP_COINS
            and not any(sfx in t["symbol"] for sfx in _SKIP_SUFFIXES)
        ]
        tickers.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
        result = [{
            "symbol":    t["symbol"].replace("USDT", ""),
            "price":     float(t["lastPrice"]),
            "change_24h": float(t.get("priceChangePercent", 0)),
            "volume_24h": float(t.get("quoteVolume", 0)),
            "high_24h":  float(t.get("highPrice", 0)),
            "low_24h":   float(t.get("lowPrice", 0)),
        } for t in tickers[:50]]
        _cache_set("top50", result, ttl=120)  # кэш 2 мин
        return jsonify(result)
    except Exception:
        return jsonify([])


@app.route("/api/calendar")
def api_calendar():
    """Экономический календарь для miniapp — с прогнозом, фактом и крипто-импактом."""
    from flask import jsonify
    try:
        events = get_upcoming_events(days_ahead=30)
        result = []
        for e in events:
            raw_title = e.get("title", "")
            result.append({
                "title":        _translate_event(raw_title),
                "title_en":     raw_title,
                "date":         e.get("date", ""),
                "time":         e.get("time", ""),
                "impact":       e.get("impact", "medium"),
                "forecast":     e.get("forecast", ""),
                "previous":     e.get("previous", ""),
                "actual":       e.get("actual", ""),
                "source":       e.get("source", ""),
                "crypto_impact": get_crypto_impact(raw_title),
            })
        return jsonify(result[:25])
    except Exception:
        return jsonify([])


@app.route("/miniapp/")
def serve_miniapp_index():
    """Mini App — главная страница."""
    from flask import send_from_directory, abort
    import os
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "miniapp")
    if not os.path.exists(d): abort(404)
    return send_from_directory(d, "index.html")

@app.route("/miniapp/<path:filename>")
def serve_miniapp_file(filename):
    """Mini App — статические файлы."""
    from flask import send_from_directory, abort
    import os
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "miniapp")
    safe = os.path.basename(filename)  # только имя файла, без path traversal
    if not os.path.exists(os.path.join(d, safe)): abort(404)
    return send_from_directory(d, safe)


@app.route("/health")
def health():
    from flask import jsonify
    status = {
        "bot": "ok",
        "scheduler": "ok" if _scheduler.running else "fail",
        "redis": "ok" if redis_ok() else "degraded",
        "webhook": "ok" if _webhook_ok else "pending",
    }
    code = 200 if status["scheduler"] == "ok" else 503
    return jsonify(status), code


# ══════════════════════════════════════════════════════════════════════════════
# ⏰ APSCHEDULER — встроенный cron (не нужны внешние Railway Cron Jobs)
# ══════════════════════════════════════════════════════════════════════════════

def _miniapp_markup():
    """URL-кнопка miniapp для шедулер-постов в группу."""
    if not PA_DOMAIN:
        return None
    try:
        url = f"https://{PA_DOMAIN}/miniapp/"
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton(
            text="📊 Открыть Statham App",
            url=url
        ))
        return markup
    except Exception:
        return None

def _job_morning():
    """Утренний пост — 08:00 МСК."""
    write_log("SCHEDULER | morning job triggered")
    text = random.choice(MORNING_MESSAGES)
    if ai.api_key:
        ai_greeting = ai.generate_greeting("чат", is_morning=True)
        if ai_greeting:
            text = ai_greeting
    _send_scheduled_message(text, MORNING_PHOTO_PATH)
    # AI-брифинг по рынку
    try:
        if ai.api_key or gemini.enabled:
            crypto_ctx = get_crypto_ai_context()
            briefing = ask_ai(
                "Напиши краткий утренний крипто-брифинг для чата (3 предложения). "
                "Упомяни состояние BTC, индекс страха/жадности и главный тренд. "
                "Стиль: аналитик-практик, лаконично, с эмодзи.",
                "", context=crypto_ctx
            )
            if briefing:
                _send_scheduled_message(f"📋 <b>Утренний брифинг</b>\n\n{briefing}")
    except Exception as e:
        write_log(f"MORNING_BRIEFING_ERR | {e}")
    # Рынок + кнопка приложения
    try:
        market_msg = format_market_message()
        markup = _miniapp_markup()
        _send_scheduled_message(market_msg, reply_markup=markup)
    except Exception as e:
        write_log(f"MORNING_MARKET_ERR | {e}")
    # Ближайшие события на 3 дня
    try:
        cal_msg = format_calendar_message(days_ahead=3)
        if cal_msg and "Нет " not in cal_msg[:30]:
            _send_scheduled_message(cal_msg)
    except Exception as e:
        write_log(f"MORNING_CAL_ERR | {e}")

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

def _build_daily_report() -> str:
    """Собирает текст ежедневного отчёта."""
    stats = get_daily_stats()
    warns_today = get_mod_log(limit=100)
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=3)).strftime("%Y-%m-%d")
    warns_count = sum(1 for w in warns_today
                      if w.get("action") == "warn" and
                      datetime.datetime.utcfromtimestamp(w["ts"]).strftime("%Y-%m-%d") == today)
    mutes_count = sum(1 for w in warns_today
                      if w.get("action") == "mute" and
                      datetime.datetime.utcfromtimestamp(w["ts"]).strftime("%Y-%m-%d") == today)
    inactive = get_inactive_users(7)

    top_user = stats.get("top_user")
    top_line = ""
    if top_user:
        uname = f"@{top_user['username']}" if top_user.get("username") else top_user["first_name"]
        top_line = f"\n🏆 Самый активный: {uname} ({top_user['msg_count']} сообщ.)"

    # Пиковый час
    hourly = stats.get("hourly", [])
    peak_hour = max(hourly, key=lambda x: x["msg_count"])["hour"] if hourly else None
    peak_line = f"\n🕐 Пик активности: {peak_hour}:00–{peak_hour+1}:00 МСК" if peak_hour is not None else ""

    report = (
        f"📊 <b>Ежедневный отчёт — {stats['date']}</b>\n\n"
        f"💬 Сообщений за день: <b>{stats['total_msgs']}</b>\n"
        f"👥 Новых участников: <b>{stats['new_users']}</b>\n"
        f"⚠️ Варнов: <b>{warns_count}</b>  🔇 Мутов: <b>{mutes_count}</b>"
        f"{top_line}{peak_line}\n"
        f"😴 Молчат 7+ дней: <b>{len(inactive)}</b> чел."
    )
    return report

def _job_daily_report():
    """Ежедневный отчёт администратору — 23:50 МСК."""
    write_log("SCHEDULER | daily_report job triggered")
    report = _build_daily_report()
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, report, parse_mode="HTML")
        except Exception as e:
            write_log(f"DAILY_REPORT_ERR | admin={admin_id} | {e}")

def _job_weekly_top():
    """Еженедельный топ в чат — воскресенье 20:00 МСК."""
    write_log("SCHEDULER | weekly_top job triggered")
    if not CHAT_ID:
        write_log("WEEKLY_TOP_ERR | CHAT_ID не задан")
        return
    top = get_top_users(5)
    if not top:
        write_log("WEEKLY_TOP_SKIP | нет активных пользователей")
        return
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = ["🏆 <b>Топ недели — Statham Elite!</b>\n"]
    for i, u in enumerate(top):
        uname = f"@{u['username']}" if u.get("username") else u["first_name"]
        lvl = get_level_name(u.get("level", 1))
        lines.append(f"{medals[i]} {uname} — {u['msg_count']} сообщ. | {lvl}")
    lines.append("\nПродолжайте в том же духе! 💪")
    lines.append("\n📱 <i>Крипто-дашборд: /app</i>")
    try:
        markup = _miniapp_markup()
        if markup:
            bot.send_message(int(CHAT_ID), "\n".join(lines), parse_mode="HTML", reply_markup=markup)
        else:
            _send_message_simple(int(CHAT_ID), "\n".join(lines), parse_mode="HTML")
        write_log(f"CRON_OK | weekly_top sent to {CHAT_ID}")
    except Exception as e:
        write_log(f"WEEKLY_TOP_ERR | {e}")



def _job_market_summary():
    """Сводка рынка 4 раза в день: 9:00, 13:00, 17:00, 21:00 МСК."""
    write_log("SCHEDULER | market_summary")
    try:
        price_msg  = format_price_message(["btc", "eth", "sol", "bnb", "ton"])
        fg_msg     = format_fear_greed()
        full_msg   = price_msg + "\n\n" + fg_msg
        _send_scheduled_message(full_msg)
    except Exception as e:
        write_log(f"MARKET_SUMMARY_ERR | {e}")


def _job_check_alerts():
    """Проверка ценовых алертов каждые 5 минут."""
    try:
        all_alerts = get_all_alerts()
        if not all_alerts:
            return
        triggered = check_price_alerts(all_alerts)
        for a in triggered:
            uid_alert = a["uid"]
            dir_str = "достиг" if a["dir"] == "above" else "упал до"
            text = (
                f"🔔 <b>Алерт сработал!</b>\n\n"
                f"<b>{a['coin']}</b> {dir_str} <b>${a['current_price']:,.0f}</b>\n"
                f"Твоя цель: ${a['target']:,.0f}"
            )
            try:
                bot.send_message(uid_alert, text, parse_mode="HTML")
            except Exception:
                pass
            remove_alert(uid_alert, a["coin"])
            give_achievement(uid_alert, "alert_fired")
            add_xp(uid_alert, 10)
        if triggered:
            write_log(f"ALERTS | triggered {len(triggered)} alerts")
    except Exception as e:
        write_log(f"ALERT_CHECK_ERR | {e}")




def _job_resolve_predictions():
    """Определяет результаты ставок каждые 4 часа."""
    write_log("SCHEDULER | resolve_predictions")
    try:
        from crypto_module import get_prices, COIN_ALIASES, _fmt_price
        preds = get_active_predictions()
        if not preds: return
        # Собираем уникальные монеты
        coins = list({p["coin"].lower() for p in preds})
        prices = get_prices(coins)
        for coin in coins:
            coin_id = COIN_ALIASES.get(coin, coin)
            d = prices.get(coin_id)
            if not d or "_error" in str(d): continue
            results = resolve_predictions(coin, d["price"])
            for res in results:
                uid_r = res["uid"]
                won = res["won"]
                save_predict_stats(uid_r, won)
                xp_gain = 25 if won else 2
                add_xp(uid_r, xp_gain)
                sym = coin.upper()
                status = "🎯 Угадал!" if won else "❌ Не угадал"
                dir_str = "🟢 вырос" if res["actual"] == "up" else ("🔴 упал" if res["actual"] == "down" else "😐 не изменился")
                text = (
                    f"🔮 <b>Результат ставки</b>\n\n"
                    f"<b>{sym}</b> {dir_str}\n"
                    f"Было: {_fmt_price(res['price_at'])} → Стало: {_fmt_price(res['new_price'])}\n\n"
                    f"{status} {'+' + str(xp_gain) if won else '+' + str(xp_gain)} XP"
                )
                try: bot.send_message(uid_r, text, parse_mode="HTML")
                except Exception: pass
                # Ачивки
                if won:
                    give_achievement(uid_r, "predict_win")
                    stats = get_predict_stats(uid_r)
                    if stats.get("streak", 0) >= 5:
                        if give_achievement(uid_r, "predict_5"):
                            try: bot.send_message(uid_r, "🌟 ДОСТИЖЕНИЕ: 5 угаданных подряд! +100 XP", parse_mode="HTML")
                            except Exception: pass
                            add_xp(uid_r, 100)
                total = get_predict_stats(uid_r).get("total", 0)
                if total >= 10: give_achievement(uid_r, "degen")
        write_log(f"SCHEDULER | resolved {len(preds)} predictions")
    except Exception as e:
        write_log(f"RESOLVE_PRED_ERR | {e}")


def _job_calendar_check():
    """Проверка важных экономических событий (2 раза в день: 8:00 и 20:00 МСК)."""
    write_log("SCHEDULER | calendar_check")
    try:
        # Утром — события на сегодня
        events_today = check_events_today()
        if events_today:
            lines = ["📅 <b>Сегодня важные экономические события:</b>\n"]
            for e in events_today:
                from calendar_module import _translate_event
                lines.append(f"• {_translate_event(e['title'])} — {e.get('time','?')}")
            lines.append("\n⚡ Это может повлиять на рынок. Следите за /price и /fear")
            _send_scheduled_message("\n".join(lines))
        # Проверяем события через ~2 часа
        events_soon = check_events_soon(hours=2)
        for e in events_soon:
            _send_scheduled_message(format_event_alert(e))
    except Exception as e:
        write_log(f"CALENDAR_ERR | {e}")


def _job_daily_vote():
    """Ежедневное голосование в чате в 10:00 МСК."""
    write_log("SCHEDULER | daily_vote")
    if not CHAT_ID:
        write_log("DAILY_VOTE_ERR | CHAT_ID не задан")
        return
    try:
        import datetime
        question = get_daily_question()
        qid = datetime.date.today().isoformat()
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        btns = [telebot.types.InlineKeyboardButton(
            text=f"{i+1}. {opt}",
            callback_data=f"vote:{qid}:{i}"
        ) for i, opt in enumerate(question["options"])]
        markup.add(*btns)
        text = f"📊 <b>Вопрос дня</b>\n\n{question['text']}"
        bot.send_message(int(CHAT_ID), text, parse_mode="HTML", reply_markup=markup)
        write_log(f"CRON_OK | daily_vote sent to {CHAT_ID}")
    except Exception as e:
        write_log(f"DAILY_VOTE_ERR | {e}")

def _job_daytime_engage():
    """Дневной вопрос от AI — 15:00 МСК. Только если AI активен и чат не бурлит."""
    write_log("SCHEDULER | daytime_engage triggered")
    if not (ai.api_key or gemini.enabled) or not CHAT_ID:
        return
    try:
        # Если в последних 15 сообщениях чата много активности — не мешаем
        global_ctx = get_global_ctx(limit=15)
        if global_ctx and global_ctx.count("\n") >= 12:
            write_log("SCHEDULER | daytime_engage skipped — chat is active")
            return
        crypto_ctx = get_crypto_ai_context()
        prompt = (
            "Придумай ОДИН короткий вопрос или тему для обсуждения в крипто-чате. "
            "Опирайся на текущий рынок. Формат: 1-2 предложения + смайлы. "
            "Заверши вопросом чтобы участники ответили. "
            f"Контекст: {crypto_ctx}"
        )
        question = ask_ai(prompt, "", context=crypto_ctx)
        if question:
            markup = _miniapp_markup()
            _send_scheduled_message(f"💬 <b>Тема дня</b>\n\n{question}", reply_markup=markup)
            write_log("SCHEDULER | daytime_engage sent")
    except Exception as e:
        write_log(f"DAYTIME_ENGAGE_ERR | {e}")


def _job_evening_movers():
    """Вечерняя сводка — рынок + топ движения + DeFi TVL — 22:00 МСК."""
    write_log("SCHEDULER | evening_movers job triggered")
    markup = _miniapp_markup()
    try:
        market_msg = format_market_message()
        _send_scheduled_message(market_msg, reply_markup=markup)
    except Exception as e:
        write_log(f"EVENING_MARKET_ERR | {e}")
    try:
        movers_msg = format_movers_message()
        tvl_msg = format_tvl_message()
        _send_scheduled_message(movers_msg + "\n\n" + tvl_msg)
    except Exception as e:
        write_log(f"EVENING_MOVERS_ERR | {e}")


_scheduler = BackgroundScheduler(timezone="Europe/Moscow", daemon=True)
_scheduler.add_job(_job_morning,        "cron", hour=8,  minute=0,  id="morning")
_scheduler.add_job(_job_factofday,      "cron", hour=12, minute=0,  id="factofday")
_scheduler.add_job(_job_night,          "cron", hour=23, minute=0,  id="night")
_scheduler.add_job(_job_daily_report,   "cron", hour=23, minute=50, id="daily_report")
_scheduler.add_job(_job_weekly_top,     "cron", day_of_week="sun", hour=20, minute=0, id="weekly_top")
# 🆕 v5.0 — крипто
_scheduler.add_job(_job_market_summary, "cron", hour="7,9,13,17,21", minute=0, id="market_summary")
_scheduler.add_job(_job_check_alerts,   "interval", minutes=5, id="price_alerts", max_instances=1)
_scheduler.add_job(_job_resolve_predictions, "interval", hours=4, id="resolve_preds", max_instances=1)
_scheduler.add_job(_job_calendar_check, "cron", hour="8,20", minute=0, id="calendar")
_scheduler.add_job(_job_daily_vote,     "cron", hour=10, minute=0, id="daily_vote")
_scheduler.add_job(_job_evening_movers, "cron", hour=22, minute=0, id="evening_movers")
_scheduler.add_job(_job_daytime_engage, "cron", hour=15, minute=0, id="daytime_engage")
_scheduler.start()
write_log("SCHEDULER | APScheduler started (morning=08:00, fact=12:00, night=23:00, "
          "report=23:50, weekly_top=Sun 20:00, vote=10:00, market=07,09,13,17,21, alerts=5min, "
          "movers+market=22:00, engage=15:00, calendar=08,20 MSK)")

# Graceful shutdown при SIGTERM (Railway останавливает контейнер через SIGTERM)
def _handle_shutdown(sig, frame):
    write_log("SHUTDOWN | SIGTERM received, stopping scheduler...")
    _scheduler.shutdown(wait=False)
    sys.exit(0)

signal.signal(signal.SIGTERM, _handle_shutdown)

# 📸 Фото для утреннего/вечернего поста
# Положи hello.jpg и goodb.jpg в корень репозитория
MORNING_PHOTO_PATH = os.path.join(os.path.dirname(__file__), "hello.jpg")
NIGHT_PHOTO_PATH   = os.path.join(os.path.dirname(__file__), "goodb.jpg")

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

def _send_scheduled_message(text: str, photo_path: str = None, reply_markup=None):
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
            kw = {"parse_mode": "HTML"}
            if reply_markup:
                kw["reply_markup"] = reply_markup
            _send_message_simple(chat_id, text, **kw)

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
