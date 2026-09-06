# -*- coding: utf-8 -*-
"""
web_search_module.py — веб-поиск для Statham Bot (грounding для AI-ответов).

Зачем: LLM (Groq/Gemini/итд) не имеет доступа в интернет и ничего не знает
о событиях после своего обучения — он видит только то, что мы кладём ему
в промпт. Этот модуль ищет в вебе и отдаёт компактную сводку для контекста.

Порядок fallback: Tavily (основной, заточен под LLM/RAG, 1000 кредитов/мес,
без карты) → Brave Search (запасной, 2000 запросов/мес). Оба опциональны —
включаются автоматически при наличии ключа, при отсутствии обоих модуль
просто возвращает пустую строку (AI отвечает как раньше, без грounding).

Суточный бюджет считается через Redis (redis_memory.check_rate_limit),
чтобы не выжечь месячный лимит за один активный день в чате. При исчерпании
бюджета у одного провайдера — пробуем следующего, при исчерпании у всех —
тихо возвращаем "" (не роняем ответ AI).

Env vars:
  TAVILY_API_KEY, BRAVE_API_KEY          — ключи (опциональны, но нужен хотя бы один)
  TAVILY_DAILY_BUDGET (default 30)       — 1000/мес ≈ 33/день, с запасом
  BRAVE_DAILY_BUDGET  (default 60)       — 2000/мес ≈ 65/день, с запасом
  WEB_SEARCH_CACHE_TTL (default 600)     — кэш одинаковых запросов, секунды
"""
from __future__ import annotations
import os, time, json
import requests

try:
    from redis_memory import check_rate_limit as _redis_rate_limit, _get as _redis_client
except Exception:
    _redis_rate_limit = None
    _redis_client = None


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


TAVILY_KEY = _env("TAVILY_API_KEY")
BRAVE_KEY = _env("BRAVE_API_KEY")
TAVILY_BUDGET = int(_env("TAVILY_DAILY_BUDGET", "30"))
BRAVE_BUDGET = int(_env("BRAVE_DAILY_BUDGET", "60"))
CACHE_TTL = int(_env("WEB_SEARCH_CACHE_TTL", "600"))

_log = print

def set_logger(fn):
    """Даёт app.py подключить свой write_log вместо print."""
    global _log
    _log = fn


# ── бюджет и кэш через Redis (best-effort, без Redis просто не ограничиваем) ──
def _budget_ok(provider: str, limit: int) -> bool:
    if not _redis_rate_limit:
        return True
    try:
        # uid=0 — общий (не per-user) счётчик на весь бот, window=сутки
        return _redis_rate_limit(0, f"search_budget:{provider}", max_calls=limit, window=86400)
    except Exception:
        return True

def _cache_get(query: str):
    if not _redis_client:
        return None
    r = _redis_client()
    if not r:
        return None
    try:
        raw = r.get(f"wsc:{query.lower().strip()[:200]}")
        return json.loads(raw) if raw else None
    except Exception:
        return None

def _cache_set(query: str, results: list):
    if not _redis_client:
        return
    r = _redis_client()
    if not r:
        return
    try:
        r.setex(f"wsc:{query.lower().strip()[:200]}", CACHE_TTL, json.dumps(results))
    except Exception:
        pass


# ── провайдеры ─────────────────────────────────────────────────────────────
def _tavily_search(query: str, max_results: int = 3) -> list | None:
    if not TAVILY_KEY:
        return None
    if not _budget_ok("tavily", TAVILY_BUDGET):
        _log("WSEARCH_SKIP | Tavily | суточный бюджет исчерпан")
        return None
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {TAVILY_KEY}", "Content-Type": "application/json"},
            json={
                "query": query, "search_depth": "basic", "max_results": max_results,
                "include_answer": "basic", "topic": "general",
            },
            timeout=12,
        )
        if r.status_code != 200:
            _log(f"WSEARCH_ERR | Tavily | HTTP {r.status_code} | {r.text[:150]}")
            return None
        data = r.json()
        out = []
        if data.get("answer"):
            out.append({"title": "Краткий ответ", "url": "", "snippet": data["answer"]})
        for item in data.get("results", [])[:max_results]:
            out.append({
                "title": item.get("title", ""), "url": item.get("url", ""),
                "snippet": (item.get("content") or "")[:300],
            })
        return out or None
    except Exception as e:
        _log(f"WSEARCH_ERR | Tavily | {type(e).__name__} | {e}")
        return None


def _brave_search(query: str, max_results: int = 3) -> list | None:
    if not BRAVE_KEY:
        return None
    if not _budget_ok("brave", BRAVE_BUDGET):
        _log("WSEARCH_SKIP | Brave | суточный бюджет исчерпан")
        return None
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_KEY},
            params={"q": query, "count": max_results},
            timeout=12,
        )
        if r.status_code != 200:
            _log(f"WSEARCH_ERR | Brave | HTTP {r.status_code} | {r.text[:150]}")
            return None
        data = r.json()
        out = []
        for item in (data.get("web", {}) or {}).get("results", [])[:max_results]:
            out.append({
                "title": item.get("title", ""), "url": item.get("url", ""),
                "snippet": (item.get("description") or "")[:300],
            })
        return out or None
    except Exception as e:
        _log(f"WSEARCH_ERR | Brave | {type(e).__name__} | {e}")
        return None


def search_web(query: str, max_results: int = 3) -> list:
    """Пробует провайдеров по порядку, возвращает первый успешный список результатов."""
    query = (query or "").strip()
    if not query:
        return []
    cached = _cache_get(query)
    if cached is not None:
        return cached
    for name, fn in (("tavily", _tavily_search), ("brave", _brave_search)):
        results = fn(query, max_results)
        if results:
            _log(f"WSEARCH_OK | {name} | {len(results)} результатов")
            _cache_set(query, results)
            return results
    _log("WSEARCH_ALL_FAIL | ни один провайдер поиска не дал результата (или оба не настроены)")
    return []


def get_web_context(query: str, max_results: int = 3) -> str:
    """Готовая строка для system/user-контекста AI. Пусто, если поиск не дал ничего."""
    results = search_web(query, max_results)
    if not results:
        return ""
    lines = [f"Результаты веб-поиска по запросу «{query}»:"]
    for i, r in enumerate(results, 1):
        tail = f" ({r['url']})" if r.get("url") else ""
        lines.append(f"{i}. {r['title']}: {r['snippet']}{tail}")
    return "\n".join(lines)


# ── эвристика: нужен ли вообще веб-поиск для этого вопроса ────────────────
_SEARCH_TRIGGERS = (
    "новост", "сегодня", "сейчас", "последн", "актуальн", "что случилось",
    "кто такой", "кто такая", "что такое", "когда выйдет", "когда состоится",
    "что нового", "расскажи про", "погода", "курс доллара", "news", "latest",
    "who is", "what happened", "current",
)

def needs_web_search(question: str) -> bool:
    """Грубая эвристика: похож ли вопрос на тот, что требует свежих фактов.
    Не идеальна — рассчитана на то, чтобы не жечь бюджет на болтовню
    ('как дела', 'напиши код'), но ловить явные запросы на актуальную инфу."""
    q = (question or "").lower()
    return any(t in q for t in _SEARCH_TRIGGERS)
