# 🤖 Statham Bot v5.0

Telegram-бот для модерации чата с памятью Redis, AI (Groq + Gemini) и крипто-функциями.

## 🚀 Деплой на Railway

### 1. Клонировать / загрузить на GitHub
```bash
git add . && git commit -m "v5.0 redis+crypto" && git push
```

### 2. Добавить Redis в Railway
Railway Dashboard → New → Redis → Connect to project

### 3. Переменные окружения (Settings → Variables)
Смотри `.env.example` — скопируй и заполни все ключи.

**Обязательные:**
- `BOT_TOKEN` — токен от @BotFather
- `CHAT_ID` — ID группового чата
- `ADMIN_IDS` — твой Telegram ID
- `RAILWAY_DOMAIN` — домен Railway
- `GROQ_API_KEY` — https://console.groq.com/keys (бесплатно)

**Рекомендуемые:**
- `GEMINI_API_KEY` — https://aistudio.google.com/app/apikey (бесплатно)
- `CRYPTOPANIC_KEY` — https://cryptopanic.com/developers/api/ (бесплатно)

**Автоматически добавляются** при подключении Redis:
- `REDIS_URL` / `REDIS_PRIVATE_URL`

### 4. Активировать вебхук
Перейти на `https://ВАШ_ДОМЕН/setup`

---

## 📊 Новые команды v5.0

### Крипто
| Команда | Описание |
|---|---|
| `/price btc eth sol` | Цены (CoinGecko, бесплатно) |
| `/fear` | Fear & Greed Index |
| `/market` | Доминация BTC/ETH, капа рынка |
| `/news` | Топ крипто-новостей |
| `/alert btc 100000` | Алерт когда BTC > $100k |
| `/alerts` | Мои алерты |
| `/delalert btc` | Удалить алерт |

### Память
| Команда | Описание |
|---|---|
| `/remember я держу BTC с 2021` | Запомнить факт (Redis) |
| `/forget` | Удалить историю диалогов |
| `/redisstat` | Статус Redis (только админ) |

### AI
| Команда | Описание |
|---|---|
| `/ai вопрос` | Groq AI + Gemini fallback |
| `!ai вопрос` | То же в чате |

---

## ⏰ Расписание
- 08:00 МСК — утреннее приветствие
- 09:00, 13:00, 17:00, 21:00 — сводка крипторынка
- 12:00 МСК — факт дня
- Каждый час — проверка важных новостей (ФРС, SEC, взломы)
- Каждые 5 минут — проверка ценовых алертов
- 23:00 МСК — ночной пост
- 23:50 МСК — ежедневный отчёт
- Воскресенье 20:00 — еженедельный топ

---

## 🔐 Безопасность
⚠️ Если токены были скомпрометированы:
1. `@BotFather` → `/token` → перегенерировать
2. `console.groq.com` → удалить ключ → создать новый
3. Обновить в Railway Variables
