"""
WSGI entry point for PythonAnywhere.
Path: /home/artrush86/mysite/wsgi.py
"""
from __future__ import annotations
import os, sys

PROJECT_HOME = "/home/artrush86/mysite"
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

# ══════════════════════════════════════════════════════════════════════════════
# 🔧 ПРОКСИ — обязательно для бесплатного плана PythonAnywhere!
#    Без этого бот получает сообщения, но НЕ МОЖЕТ отвечать.
# ══════════════════════════════════════════════════════════════════════════════
os.environ["http_proxy"]  = "http://proxy.server:3128"
os.environ["https_proxy"] = "http://proxy.server:3128"

# ══════════════════════════════════════════════════════════════════════════════
# ⚙️  НАСТРОЙКИ БОТА
# ══════════════════════════════════════════════════════════════════════════════
os.environ.setdefault("BOT_TOKEN",  "8790652536:AAGDvP70BhrU-s0tWPYh0OulqvFLDvOtgZs")
os.environ.setdefault("CHAT_ID",    "-1003867089540")
os.environ.setdefault("TOPIC_ID",   "6314")
os.environ.setdefault("PA_DOMAIN",  "artrush86.pythonanywhere.com")

# ADMIN_IDS — через запятую, БЕЗ пробелов.
# Узнать свой Telegram ID: напиши боту @userinfobot
os.environ["ADMIN_IDS"] = "7617558315,789012"  # ← ЗАМЕНИ на реальные ID!

# (Опционально) Текст правил чата
# os.environ.setdefault("RULES_TEXT", (
#     "📋 <b>Правила чата Statham Elite</b>\n\n"
#     "1️⃣ Уважайте друг друга\n"
#     "2️⃣ Запрещён мат и оскорбления\n"
#     "3️⃣ Запрещён спам и флуд\n"
#     "4️⃣ Реклама только с разрешения админа\n\n"
#     "3 нарушения = мут. Не испытывай судьбу 😏"
# ))

from app import app as application