# -*- coding: utf-8 -*-
"""
ai_providers.py — мульти-провайдерный AI-клиент для Statham Bot.

Единый OpenAI-совместимый формат (/chat/completions) для всех провайдеров.
Порядок fallback (сверху вниз):
  Groq → Gemini (OpenAI-compat) → OpenRouter → Cerebras → Mistral → GitHub Models → Cloudflare

Каждый провайдер включается автоматически, если задан его ключ в переменных окружения.
Провайдер без ключа пропускается (PROV_SKIP). При ошибке логируется и идём к следующему.

Env vars:
  GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, CEREBRAS_API_KEY,
  MISTRAL_API_KEY, GITHUB_TOKEN, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID
Опциональные override моделей:
  GROQ_MODEL, GEMINI_MODEL, OPENROUTER_MODEL, CEREBRAS_MODEL,
  MISTRAL_MODEL, GITHUB_MODEL, CLOUDFLARE_MODEL
"""
from __future__ import annotations
import os, time, json
import requests


# ── Глобальный системный промпт (единый для всех провайдеров) ────────────────
SYSTEM_PROMPT = (
    "Ты — Statham, бот в крипто-чате Telegram. Характер прямой, с юмором. "
    "Торгуешь крипто с 2017. Это чат для общения на разные темы: программирование, "
    "жизнь, юмор. Отвечай кратко (1-2 предложения), с юмором, на русском. "
    "Ты модератор, но дружелюбный. Используй эмодзи. Не пиши длинных текстов — чат, а не эссе. "
    "Если спрашивают про код — помогай с кодом. Если спрашивают совет — давай полезный совет."
)


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


class MultiProviderAI:
    def __init__(self, log_fn=None, timeout: int = 20, max_tokens: int = 200):
        self.log = log_fn or (lambda entry: print(entry, flush=True))
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.last_provider = "AI"  # короткое имя последнего успешного провайдера
        cf_account = _env("CLOUDFLARE_ACCOUNT_ID")

        # Порядок = приоритет fallback
        self.providers = [
            self._mk("Groq", "https://api.groq.com/openai/v1",
                     _env("GROQ_API_KEY"), _env("GROQ_MODEL", "llama-3.3-70b-versatile")),
            self._mk("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                     _env("GEMINI_API_KEY"), _env("GEMINI_MODEL", "gemini-2.5-flash")),
            self._mk("OpenRouter", "https://openrouter.ai/api/v1",
                     _env("OPENROUTER_API_KEY"),
                     _env("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
                     extra={"HTTP-Referer": "https://t.me", "X-Title": "StathamBot"}),
            self._mk("Cerebras", "https://api.cerebras.ai/v1",
                     _env("CEREBRAS_API_KEY"), _env("CEREBRAS_MODEL", "llama-3.3-70b")),
            self._mk("Mistral", "https://api.mistral.ai/v1",
                     _env("MISTRAL_API_KEY"), _env("MISTRAL_MODEL", "mistral-small-latest")),
            self._mk("GitHub Models", "https://models.inference.ai.azure.com",
                     _env("GITHUB_TOKEN"), _env("GITHUB_MODEL", "gpt-4o-mini")),
            self._mk("Cloudflare",
                     f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/v1" if cf_account else "",
                     _env("CLOUDFLARE_API_TOKEN"),
                     _env("CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct")),
        ]

    @staticmethod
    def _mk(name, base_url, key, model, extra=None) -> dict:
        return {
            "name": name,
            "base_url": base_url.rstrip("/"),
            "key": key,
            "model": model,
            "extra": extra or {},
            "enabled": bool(key and base_url),
        }

    @property
    def available(self) -> bool:
        return any(p["enabled"] for p in self.providers)

    def status_lines(self) -> list:
        lines = []
        for p in self.providers:
            if p["enabled"]:
                lines.append(f"✅ {p['name']}: {p['model']}")
            else:
                lines.append(f"❌ {p['name']}: ключ не задан")
        return lines

    # ──────────────────────────────────────────────────────────────────────
    def ask(self, prompt: str, user_name: str = "", context: str = "",
            history: list = None) -> str | None:
        """Перебирает провайдеры по порядку, возвращает первый успешный ответ."""
        if not self.available:
            return None

        chat_depth = len(history) if history else 0
        is_close_chat = chat_depth >= 3
        system_prompt = SYSTEM_PROMPT
        if is_close_chat:
            system_prompt += (
                f" Это твой {chat_depth}-й разговор с этим пользователем — "
                "ты уже знаком, можешь быть дружелюбнее и отходить от роли модератора."
            )

        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages.append({"role": "user", "content": f"Контекст: {context}"})
        if history:
            for h in history[-5:]:
                messages.append({"role": "user", "content": h.get("user_msg", "")})
                messages.append({"role": "assistant", "content": h.get("bot_reply", "")})
        messages.append({"role": "user", "content": f"{user_name}: {prompt}" if user_name else prompt})

        for p in self.providers:
            if not p["enabled"]:
                continue
            answer = self._call(p, messages, is_close_chat)
            if answer:
                self.last_provider = p["name"]
                return answer

        self.log("PROV_ALL_FAIL | все провайдеры вернули ошибку или пустой ответ")
        return None

    def generate_greeting(self, user_name: str, is_morning: bool = False) -> str | None:
        prompt = f"Приветствуй пользователя {user_name} в чате. "
        if is_morning:
            prompt += "Утреннее приветствие с пожеланием доброго дня. "
        prompt += "Кратко, с эмодзи, дружелюбно."
        return self.ask(prompt, user_name)

    # ──────────────────────────────────────────────────────────────────────
    def _call(self, p: dict, messages: list, is_close_chat: bool) -> str | None:
        name = p["name"]
        url = f"{p['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {p['key']}",
            "Content-Type": "application/json",
        }
        headers.update(p["extra"])
        payload = {
            "model": p["model"],
            "messages": messages,
            "temperature": 0.8 if is_close_chat else 0.7,
            "max_tokens": self.max_tokens,
        }

        t0 = time.time()
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        except Exception as e:
            self.log(f"PROV_ERR | {name} | {type(e).__name__} | {e}")
            return None

        ms = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            try:
                body = r.json()
                errmsg = body.get("error", {})
                if isinstance(errmsg, dict):
                    errmsg = errmsg.get("message", "")
                detail = str(errmsg)[:160]
            except Exception:
                detail = r.text[:160]
            self.log(f"PROV_ERR | {name} | HTTP {r.status_code} | {detail}")
            return None

        try:
            data = r.json()
        except Exception as e:
            self.log(f"PROV_ERR | {name} | bad json | {e}")
            return None

        try:
            answer = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            self.log(f"PROV_ERR | {name} | нет choices в ответе | {str(data)[:160]}")
            return None

        if not answer:
            self.log(f"PROV_ERR | {name} | пустой ответ")
            return None

        self.log(f"PROV_OK | {name} | {p['model']} | {ms}ms")
        return answer[:500]
