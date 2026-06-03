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

# ── Приоритет событий ──────────────────────────────────────────────────────────
_HIGH_IMPACT_KEYWORDS = [
    "federal reserve", "fed rate", "fomc", "interest rate decision",
    "non-farm payroll", "nonfarm", "nfp", "jobs report",
    "cpi", "consumer price index", "inflation",
    "gdp", "gross domestic product",
    "jerome powell", "powell speech", "powell",
    "ppi", "producer price",
    "unemployment rate", "jobless claims",
    "retail sales",
    "crude oil", "oil inventories", "eia crude",
    "core inflation", "core cpi", "core pce", "pce",
    "ism manufacturing", "ism services",
    "durable goods",
    "housing starts", "building permits",
    "consumer confidence", "consumer sentiment",
    "trade balance",
    "treasury", "10-year", "bond auction",
]

_EVENT_TRANSLATIONS = {
    "federal reserve": "🏦 Заседание ФРС — Решение по ставке",
    "fed rate":        "🏦 ФРС — Решение по ставке",
    "fomc":            "🏦 FOMC — Протоколы/заседание ФРС",
    "non-farm payroll":"👷 NFP — Занятость вне с/х (США)",
    "nonfarm":         "👷 NFP — Данные по занятости (США)",
    "nfp":             "👷 NFP — Занятость (США)",
    "cpi m/m":         "📊 CPI м/м — Месячная инфляция США",
    "cpi y/y":         "📊 CPI г/г — Годовая инфляция США",
    "core cpi":        "📊 Core CPI — Базовая инфляция (без еды и энергии)",
    "cpi":             "📊 CPI — Индекс потребительских цен (инфляция)",
    "consumer price":  "📊 CPI — Инфляция США",
    "core pce":        "📊 Core PCE — Базовый дефлятор расходов (цель ФРС)",
    "pce":             "📊 PCE — Дефлятор личных расходов",
    "gdp":             "💹 ВВП — Данные по экономике США",
    "gross domestic":  "💹 ВВП США",
    "ppi":             "🏭 PPI — Индекс цен производителей",
    "producer price":  "🏭 PPI — Цены производителей",
    "unemployment rate":"👤 Уровень безработицы США",
    "unemployment":    "👤 Безработица — США",
    "jobless claims":  "👤 Заявки по безработице (недельные)",
    "initial jobless": "👤 Первичные заявки по безработице",
    "continuing claim":"👤 Повторные заявки по безработице",
    "retail sales":    "🛒 Розничные продажи — США",
    "core retail":     "🛒 Базовые розничные продажи",
    "powell":          "🎤 Выступление Пауэлла (Глава ФРС)",
    "jerome":          "🎤 Выступление Пауэлла",
    "inflation":       "📊 Данные по инфляции",
    "interest rate":   "🏦 Решение по процентной ставке",
    "crude oil inventories": "🛢 EIA: Запасы сырой нефти США",
    "crude oil":       "🛢 Запасы нефти — США (EIA)",
    "eia crude":       "🛢 EIA: Запасы нефти США",
    "oil inventories": "🛢 Запасы нефти США",
    "ism manufacturing":"🏭 ISM: Деловая активность в промышленности",
    "ism services":    "📋 ISM: Деловая активность в услугах",
    "durable goods":   "⚙️ Заказы на товары длительного пользования",
    "housing starts":  "🏠 Строительство новых домов",
    "building permits":"🏗 Разрешения на строительство",
    "consumer confidence":"🛍 Индекс потребительской уверенности",
    "consumer sentiment":"🛍 Индекс потребительских настроений (U. Michigan)",
    "trade balance":   "⚖️ Торговый баланс США",
    "treasury":        "📜 Аукцион казначейских облигаций",
    "10-year":         "📜 Доходность 10-летних облигаций США",
}

# Описание влияния каждого события на крипто-рынок
_EVENT_CRYPTO_IMPACT = {
    "cpi":             "📈 Выше прогноза → ФРС может повысить ставку → давление на крипту. Ниже → позитив.",
    "core cpi":        "📈 Базовая инфляция — ключевой показатель для ФРС. Снижение = позитив для BTC.",
    "core pce":        "📈 Любимый показатель ФРС. Снижение PCE = вероятность снижения ставки = рост крипты.",
    "federal reserve": "⚡ МАКСИМАЛЬНАЯ ВОЛАТИЛЬНОСТЬ. Ожидай движения ±5-10% в течение часа.",
    "fomc":            "⚡ Протоколы ФРС — может дать сигналы о будущих ставках. Высокая волатильность.",
    "non-farm payroll":"📊 Сильный рынок труда → ФРС не торопится снижать ставку → осторожно.",
    "nonfarm":         "📊 NFP: сильные данные обычно негативны для крипты краткосрочно.",
    "unemployment":    "📊 Рост безработицы → ФРС склонна снижать ставку → позитив для крипты.",
    "gdp":             "💹 Слабый ВВП → вероятность стимулирования → осторожный позитив для крипты.",
    "crude oil":       "🛢 Нефть влияет на инфляцию → рост нефти = потенциальное давление на крипту.",
    "ism manufacturing":"🏭 Снижение PMI → замедление экономики → риск off, осторожно с позициями.",
    "consumer confidence":"🛍 Падение уверенности → риск снижения расходов → нейтрально/негативно.",
    "retail sales":    "🛒 Сильные продажи = ФРС не снижает ставку. Слабые = позитив для крипты.",
    "ppi":             "🏭 PPI опережает CPI. Снижение PPI → будущая дефляция → потенциальный позитив.",
}

def _translate_event(title: str) -> str:
    tl = title.lower()
    for en, ru in _EVENT_TRANSLATIONS.items():
        if en in tl:
            return ru
    return title


def get_crypto_impact(title: str) -> str:
    """Возвращает описание влияния события на крипто-рынок."""
    tl = title.lower()
    for kw, impact in _EVENT_CRYPTO_IMPACT.items():
        if kw in tl:
            return impact
    return "📊 Следи за реакцией рынка после выхода данных."

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
    # Запрашиваем и текущую и следующую неделю
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            for item in r.json():
                if item.get("country", "").upper() != "USD":
                    continue
                impact = item.get("impact", "").lower()
                if impact not in ("high", "medium"):
                    continue
                title    = item.get("title", "")

                # Нормализуем дату: "2026-05-31T08:30:00-04:00" или "05-31-2026" → "2026-05-31"
                raw_date = item.get("date", "") or ""
                if "T" in raw_date:
                    # ISO 8601 со временем и таймзоной — берём только дату
                    date_str = raw_date[:10]
                elif len(raw_date) == 10 and raw_date[2] == "-":
                    # "MM-DD-YYYY" → "YYYY-MM-DD"
                    parts = raw_date.split("-")
                    date_str = f"{parts[2]}-{parts[0]}-{parts[1]}" if len(parts) == 3 else raw_date
                else:
                    date_str = raw_date[:10]

                # Время как строка (например "8:30am")
                time_str = item.get("time", "") or ""

                # Пропускаем события в прошлом (старше 1 дня)
                try:
                    from datetime import date as _date
                    event_date = _date.fromisoformat(date_str)
                    if event_date < _date.today():
                        if item.get("actual"):  # показываем уже прошедшие если есть факт
                            pass
                        else:
                            continue
                except Exception:
                    pass

                events.append({
                    "title":    title,
                    "date":     date_str,
                    "time":     time_str,
                    "impact":   impact,
                    "forecast": item.get("forecast", "") or "",
                    "previous": item.get("previous", "") or "",
                    "actual":   item.get("actual",   "") or "",
                    "source":   "ForexFactory",
                })
        except Exception:
            continue
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
