"""
calendar_module.py — Statham Bot v6.0
Уровень 5: Экономический календарь
Источники (все бесплатно, без ключей):
  - ForexFactory RSS — заседания ФРС, CPI, NFP, PPI, GDP
  - FedSite JSON API — точные даты заседаний ФРС
  - Trading Economics RSS (fallback)
"""
from __future__ import annotations
import re, time, requests
from datetime import datetime, timedelta, timezone

# ── Известные даты ФРС 2025-2026 (статичный fallback) ─────────────────────────
_FED_DATES_2025_2026 = [
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07",
    "2025-06-18", "2025-07-30", "2025-09-17",
    "2025-10-29", "2025-12-10",
    # 2026
    "2026-01-28", "2026-03-18", "2026-04-29",
    "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-10-28", "2026-12-09",
]

# ── Приоритет событий (1=высокий, 2=средний) ──────────────────────────────────
_HIGH_IMPACT_KEYWORDS = [
    "federal reserve", "fed rate", "fomc", "interest rate decision",
    "non-farm payroll", "nonfarm", "nfp", "jobs report",
    "cpi", "consumer price index", "inflation",
    "gdp", "gross domestic product",
    "jerome powell", "powell speech",
    "ppi", "producer price",
    "unemployment rate", "jobless claims",
    "retail sales",
]

_EVENT_TRANSLATIONS = {
    "federal reserve": "🏦 Заседание ФРС — Решение по ставке",
    "fed rate":        "🏦 ФРС — Решение по ставке",
    "fomc":            "🏦 FOMC — Протоколы/заседание ФРС",
    "non-farm payroll":"👷 NFP — Занятость вне с/х (США)",
    "nonfarm":         "👷 NFP — Данные по занятости (США)",
    "nfp":             "👷 NFP — Занятость (США)",
    "cpi":             "📊 CPI — Индекс потребительских цен (инфляция)",
    "consumer price":  "📊 CPI — Инфляция США",
    "gdp":             "💹 ВВП — Данные по экономике США",
    "gross domestic":  "💹 ВВП США",
    "ppi":             "🏭 PPI — Индекс цен производителей",
    "producer price":  "🏭 PPI — Цены производителей",
    "unemployment":    "👤 Безработица — США",
    "jobless claims":  "👤 Первичные заявки по безработице",
    "retail sales":    "🛒 Розничные продажи — США",
    "powell":          "🎤 Выступление Пауэлла (Глава ФРС)",
    "jerome":          "🎤 Выступление Пауэлла",
    "inflation":       "📊 Данные по инфляции",
    "interest rate":   "🏦 Решение по процентной ставке",
}

def _translate_event(title: str) -> str:
    tl = title.lower()
    for en, ru in _EVENT_TRANSLATIONS.items():
        if en in tl:
            return ru
    return title  # оставить оригинал если не нашли перевод

def _is_high_impact(title: str) -> bool:
    tl = title.lower()
    return any(kw in tl for kw in _HIGH_IMPACT_KEYWORDS)

# ── ПАРСИНГ ForexFactory RSS ────────────────────────────────────────────────────
def _fetch_forexfactory() -> list[dict]:
    """
    ForexFactory не имеет публичного RSS, но есть JSON calendar API.
    Используем scrape-friendly endpoint.
    """
    events = []
    try:
        # ForexFactory calendar JSON (unofficial but stable)
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            return []
        for item in r.json():
            if item.get("country", "").upper() != "USD":
                continue
            if item.get("impact", "").lower() not in ("high", "medium"):
                continue
            title = item.get("title", "")
            date_str = item.get("date", "")  # "01-13-2025"
            time_str = item.get("time", "")  # "8:30am"
            events.append({
                "title": title,
                "date": date_str,
                "time": time_str,
                "impact": item.get("impact", "").lower(),
                "source": "ForexFactory",
            })
    except Exception:
        pass
    return events


def _fetch_next_fed() -> list[dict]:
    """Ближайшие заседания ФРС из статичного списка."""
    today = datetime.utcnow().date().isoformat()
    upcoming = [d for d in _FED_DATES_2025_2026 if d >= today][:3]
    return [{"title": "Заседание ФРС — Решение по ставке",
             "date": d, "time": "21:00", "impact": "high",
             "source": "FedSchedule"} for d in upcoming]


def _fetch_tradingeconomics_rss() -> list[dict]:
    """Trading Economics RSS как fallback."""
    events = []
    try:
        r = requests.get(
            "https://tradingeconomics.com/rss/calendar.aspx?c=united+states",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            return []
        items = re.findall(r"<item>(.*?)</item>", r.text, re.DOTALL)
        for item in items[:20]:
            tm = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", item)
            dm = re.search(r"<pubDate>(.*?)</pubDate>", item)
            title = (tm.group(1) or tm.group(2) or "").strip() if tm else ""
            date  = dm.group(1).strip() if dm else ""
            if title and _is_high_impact(title):
                events.append({"title": title, "date": date, "time": "",
                               "impact": "high", "source": "TradingEconomics"})
    except Exception:
        pass
    return events[:10]


# ── ОСНОВНАЯ ФУНКЦИЯ ────────────────────────────────────────────────────────────
def get_upcoming_events(days_ahead: int = 7) -> list[dict]:
    """Объединяет все источники, дедуплицирует, сортирует."""
    all_events = []

    # 1. ForexFactory (самый точный)
    ff = _fetch_forexfactory()
    all_events.extend(ff)

    # 2. Ближайшие заседания ФРС (всегда актуально)
    fed = _fetch_next_fed()
    all_events.extend(fed)

    # 3. Trading Economics RSS (если FF пустой)
    if not ff:
        te = _fetch_tradingeconomics_rss()
        all_events.extend(te)

    # Дедупликация по title
    seen = set()
    unique = []
    for e in all_events:
        key = _translate_event(e["title"])[:30]
        if key not in seen:
            seen.add(key)
            unique.append(e)

    return unique


def format_calendar_message(days_ahead: int = 7) -> str:
    events = get_upcoming_events(days_ahead)
    if not events:
        return (
            "📅 <b>Экономический календарь</b>\n\n"
            "⚠️ Данные временно недоступны.\n\n"
            "Ближайшие плановые события:\n"
            + "\n".join(f"  • {d}" for d in _FED_DATES_2025_2026
                        if d >= datetime.utcnow().date().isoformat())[:5]
        )

    high   = [e for e in events if e.get("impact") == "high"]
    medium = [e for e in events if e.get("impact") == "medium"]

    msk = time.strftime("%H:%M", time.gmtime(time.time() + 3*3600))
    lines = [f"📅 <b>Экономический календарь</b> • {msk} МСК\n"]

    if high:
        lines.append("🔴 <b>Высокая важность:</b>")
        for e in high[:6]:
            title_ru = _translate_event(e["title"])
            date_str = e.get("date", "?")
            time_str = e.get("time", "")
            when = f"{date_str} {time_str}".strip()
            lines.append(f"  • {title_ru}\n    🕐 {when}")

    if medium:
        lines.append("\n🟡 <b>Средняя важность:</b>")
        for e in medium[:4]:
            title_ru = _translate_event(e["title"])
            date_str = e.get("date", "?")
            time_str = e.get("time", "")
            when = f"{date_str} {time_str}".strip()
            lines.append(f"  • {title_ru}\n    🕐 {when}")

    lines.append("\n<i>Источник: ForexFactory / Trading Economics</i>")
    return "\n".join(lines)


# ── ПРОВЕРКА ДЛЯ ПЛАНИРОВЩИКА ──────────────────────────────────────────────────
def check_events_today() -> list[dict]:
    """Возвращает события СЕГОДНЯ — для утреннего уведомления."""
    today = datetime.utcnow().date().isoformat()[:10]
    events = get_upcoming_events(days_ahead=1)
    return [e for e in events
            if str(e.get("date", ""))[:10] == today
            and e.get("impact") == "high"]


def check_events_soon(hours: int = 2) -> list[dict]:
    """Возвращает события через N часов — для предупреждения."""
    # Из статичного списка ФРС
    now = datetime.utcnow()
    result = []
    for d in _FED_DATES_2025_2026:
        event_dt = datetime.fromisoformat(d + "T18:00:00")  # ФРС обычно 18:00 UTC
        diff = event_dt - now
        if timedelta(0) < diff <= timedelta(hours=hours):
            result.append({
                "title": "Заседание ФРС — Решение по ставке",
                "date": d, "time": "21:00 МСК",
                "impact": "high",
                "hours_left": round(diff.total_seconds() / 3600, 1),
            })
    return result


def format_event_alert(event: dict) -> str:
    title_ru = _translate_event(event.get("title", ""))
    hours_left = event.get("hours_left")
    date_str = event.get("date", "")
    time_str = event.get("time", "")
    if hours_left:
        timing = f"через ~{hours_left:.0f} ч"
    else:
        timing = f"{date_str} в {time_str}"
    return (
        f"⚠️ <b>ВАЖНОЕ СОБЫТИЕ</b> — {timing}\n\n"
        f"{title_ru}\n\n"
        f"📊 Это событие обычно влияет на крипто-рынок.\n"
        f"Рекомендуется проверить позиции и алерты!\n\n"
        f"💡 /fear — текущий индекс страха\n"
        f"💡 /price btc — текущая цена BTC"
    )
