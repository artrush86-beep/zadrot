#!/usr/bin/env python3
"""
test_api.py — Диагностика Statham Bot v6.0
Запуск: python3 test_api.py
Проверяет все внешние API и переменные окружения.
"""
import os, sys, time, json, requests

BOT_DOMAIN = os.environ.get("RAILWAY_DOMAIN", "")
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
GROQ_KEY   = os.environ.get("GROQ_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
REDIS_URL  = os.environ.get("REDIS_PRIVATE_URL") or os.environ.get("REDIS_URL", "")

GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"; RESET = "\033[0m"; BOLD = "\033[1m"
ok  = lambda s: print(f"  {GREEN}✅ {s}{RESET}")
err = lambda s: print(f"  {RED}❌ {s}{RESET}")
warn= lambda s: print(f"  {YELLOW}⚠️  {s}{RESET}")
hdr = lambda s: print(f"\n{BOLD}{s}{RESET}")

passed = 0; failed = 0

def check(label, condition, fix=""):
    global passed, failed
    if condition:
        ok(label); passed += 1
    else:
        err(f"{label}  {'→ ' + fix if fix else ''}"); failed += 1

# ══════════════════════════════════════════════════════════════════════════════
hdr("1. ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ")
check("BOT_TOKEN задан",     bool(BOT_TOKEN),  "Settings → Variables → BOT_TOKEN")
check("CHAT_ID задан",       bool(os.environ.get("CHAT_ID")), "Settings → Variables → CHAT_ID")
check("RAILWAY_DOMAIN задан",bool(BOT_DOMAIN), "Settings → Variables → RAILWAY_DOMAIN")
check("GROQ_API_KEY задан",  bool(GROQ_KEY),   "console.groq.com → Create key")
check("GEMINI_API_KEY задан",bool(GEMINI_KEY), "aistudio.google.com → Get API key (опционально)")
check("REDIS_URL задан",     bool(REDIS_URL),  "Railway → + New → Redis → Connect")
check("DATA_DIR задан",      bool(os.environ.get("DATA_DIR")), "Settings → Variables → DATA_DIR=/data")

# ══════════════════════════════════════════════════════════════════════════════
hdr("2. TELEGRAM BOT API")
if BOT_TOKEN:
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10)
        d = r.json()
        if d.get("ok"):
            bot_name = d["result"].get("username","?")
            ok(f"Бот @{bot_name} отвечает")
            passed += 1
            # Check webhook
            r2 = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo", timeout=10)
            wh = r2.json().get("result", {})
            wh_url = wh.get("url","")
            pending = wh.get("pending_update_count", 0)
            last_err = wh.get("last_error_message","")
            check(f"Webhook установлен: {wh_url[:60] or 'НЕТ'}",
                  bool(wh_url),
                  f"Открой https://{BOT_DOMAIN}/setup")
            if last_err:
                warn(f"Последняя ошибка webhook: {last_err}")
            if pending > 10:
                warn(f"Очередь обновлений: {pending} — бот не обрабатывает сообщения!")
        else:
            err(f"Telegram API: {d}"); failed += 1
    except Exception as e:
        err(f"Telegram недоступен: {e}"); failed += 1
else:
    warn("Пропуск: BOT_TOKEN не задан")

# ══════════════════════════════════════════════════════════════════════════════
hdr("3. AI ПРОВАЙДЕРЫ (OpenAI-compatible)")

AI_PROVIDERS = [
    ("Groq",        "https://api.groq.com/openai/v1", os.environ.get("GROQ_API_KEY",""), os.environ.get("GROQ_MODEL","llama-3.3-70b-versatile")),
    ("Gemini",      "https://generativelanguage.googleapis.com/v1beta/openai", os.environ.get("GEMINI_API_KEY",""), os.environ.get("GEMINI_MODEL","gemini-2.5-flash")),
    ("OpenRouter",  "https://openrouter.ai/api/v1", os.environ.get("OPENROUTER_API_KEY",""), os.environ.get("OPENROUTER_MODEL","meta-llama/llama-3.3-70b-instruct:free")),
    ("Cerebras",    "https://api.cerebras.ai/v1", os.environ.get("CEREBRAS_API_KEY",""), os.environ.get("CEREBRAS_MODEL","llama-3.3-70b")),
    ("Mistral",     "https://api.mistral.ai/v1", os.environ.get("MISTRAL_API_KEY",""), os.environ.get("MISTRAL_MODEL","mistral-small-latest")),
    ("GitHub Models","https://models.inference.ai.azure.com", os.environ.get("GITHUB_TOKEN",""), os.environ.get("GITHUB_MODEL","gpt-4o-mini")),
]

def _test_provider(name, base_url, key, model, extra_headers=None):
    global passed, failed
    if not key:
        warn(f"{name}: ключ не задан (пропуск)")
        return
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    try:
        r = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json={"model": model, "messages":[{"role":"user","content":"ping"}], "max_tokens":5},
            timeout=20
        )
        if r.status_code == 200:
            ok(f"{name} работает ({model})"); passed += 1
        elif r.status_code in (401, 403):
            err(f"{name}: неверный ключ (HTTP {r.status_code})"); failed += 1
        elif r.status_code == 429:
            warn(f"{name}: лимит запросов")
        elif r.status_code == 404:
            err(f"{name}: модель не найдена ({model}) → поменяй {name.upper().replace(' ','_')}_MODEL"); failed += 1
        else:
            err(f"{name}: HTTP {r.status_code} — {r.text[:120]}"); failed += 1
    except Exception as e:
        err(f"{name} недоступен: {e}"); failed += 1

for name, base, key, model in AI_PROVIDERS:
    extra = {"HTTP-Referer":"https://t.me", "X-Title":"StathamBot"} if name == "OpenRouter" else None
    _test_provider(name, base, key, model, extra)

# Cloudflare — отдельно (нужен account_id)
CF_KEY   = os.environ.get("CLOUDFLARE_API_TOKEN","")
CF_ACC   = os.environ.get("CLOUDFLARE_ACCOUNT_ID","")
CF_MODEL = os.environ.get("CLOUDFLARE_MODEL","@cf/meta/llama-3.1-8b-instruct")
if CF_KEY and CF_ACC:
    _test_provider("Cloudflare", f"https://api.cloudflare.com/client/v4/accounts/{CF_ACC}/ai/v1", CF_KEY, CF_MODEL)
elif not CF_KEY:
    warn("Cloudflare: CLOUDFLARE_API_TOKEN не задан (пропуск)")
else:
    warn("Cloudflare: CLOUDFLARE_ACCOUNT_ID не задан (пропуск)")

# ══════════════════════════════════════════════════════════════════════════════
hdr("5. REDIS")
if REDIS_URL:
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)
        r.ping()
        info = r.info("memory")
        keys = r.dbsize()
        ok(f"Redis подключён | ключей: {keys} | RAM: {info.get('used_memory_human','?')}")
        passed += 1
        # Test write/read
        r.setex("_test_key", 10, "ok")
        val = r.get("_test_key")
        check("Redis чтение/запись", val == "ok")
    except ImportError:
        err("redis не установлен → pip install redis"); failed += 1
    except Exception as e:
        err(f"Redis ошибка: {e}"); failed += 1
else:
    err("REDIS_URL не задан → Railway → + New → Redis"); failed += 1

# ══════════════════════════════════════════════════════════════════════════════
hdr("6. КРИПТО API")
tests = [
    ("CoinGecko (цены)",
     "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=bitcoin&per_page=1",
     lambda d: isinstance(d, list) and len(d) > 0,
     "Основной API цен — бесплатно, без ключа"),

    ("CoinGecko (global)",
     "https://api.coingecko.com/api/v3/global",
     lambda d: "data" in d,
     "Market overview — /market, /alts"),

    ("Fear & Greed (alternative.me)",
     "https://api.alternative.me/fng/?limit=1",
     lambda d: "data" in d,
     "Индекс страха — /fear"),

    ("Binance Futures funding",
     "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
     lambda d: isinstance(d, list) and len(d) > 0,
     "Funding rate — /funding"),

    ("DeFiLlama TVL",
     "https://api.llama.fi/v2/chains",
     lambda d: isinstance(d, list) and len(d) > 0,
     "TVL данные — /tvl"),

    ("ForexFactory calendar",
     "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
     lambda d: isinstance(d, list),
     "Экономический календарь — /calendar"),
]

for name, url, validator, note in tests:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 429:
            warn(f"{name}: Rate limit (подожди 1 мин)")
        elif r.status_code == 200:
            try:
                data = r.json()
                if validator(data):
                    ok(f"{name}")
                    passed += 1
                else:
                    warn(f"{name}: неожиданный формат — {str(data)[:60]}")
            except:
                warn(f"{name}: не JSON")
        else:
            err(f"{name}: HTTP {r.status_code}"); failed += 1
    except Exception as e:
        err(f"{name}: {type(e).__name__} — {e}"); failed += 1

# ══════════════════════════════════════════════════════════════════════════════
hdr("7. ЛОКАЛЬНЫЙ БОТ")
if BOT_DOMAIN:
    try:
        r = requests.get(f"https://{BOT_DOMAIN}/health", timeout=10)
        check(f"Health check: {r.text[:30]}", r.status_code == 200)
    except Exception as e:
        err(f"Health check: {e}"); failed += 1

    try:
        r = requests.get(f"https://{BOT_DOMAIN}/miniapp/", timeout=10)
        check("Mini App доступен", r.status_code == 200 and "Statham" in r.text)
    except Exception as e:
        err(f"Mini App: {e}"); failed += 1
else:
    warn("Пропуск: RAILWAY_DOMAIN не задан")

# ══════════════════════════════════════════════════════════════════════════════
hdr("8. ФАЙЛЫ")
files = [
    ("hello.jpg",  "Утреннее фото"),
    ("goodb.jpg",  "Вечернее фото"),
    ("miniapp/index.html", "Mini App"),
    ("data/bot.db", "База данных SQLite"),
]
for fname, desc in files:
    exists = os.path.exists(fname) or os.path.exists(f"/app/{fname}")
    if exists: ok(f"{fname} — {desc}"); passed += 1
    else:      warn(f"{fname} не найден — {desc} не будет работать")

# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
total = passed + failed
bar = "█" * passed + "░" * failed
color = GREEN if failed == 0 else (YELLOW if failed <= 3 else RED)
print(f"{color}{BOLD}Итого: {passed}/{total} проверок пройдено{RESET}")
print(f"[{color}{bar}{RESET}]")
if failed > 0:
    print(f"\n{RED}Исправь ошибки выше и перезапусти{RESET}")
else:
    print(f"\n{GREEN}Всё готово к работе! 🚀{RESET}")
