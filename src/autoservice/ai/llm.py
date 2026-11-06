"""Режим B: адаптер локальной языковой модели.

Подключение только к серверу модели на этом же компьютере (127.0.0.1 /
localhost), например Ollama или llama.cpp server. Модель не скачивается и не
устанавливается программой. Если модель не запущена — режим B честно
показывается недоступным, программа работает в базовом режиме.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

from autoservice.application.context import AppContext
from autoservice.application.settings_service import get_all
from autoservice.domain.errors import DomainError

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class LLMStatus:
    available: bool
    text: str


def check_local_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise DomainError("Адрес локальной модели должен начинаться с http://")
    host = (parsed.hostname or "").lower()
    if host not in LOCAL_HOSTS:
        try:
            if not ip_address(host).is_loopback:
                raise ValueError
        except ValueError as exc:
            raise DomainError("Разрешены только адреса этого компьютера (127.0.0.1 или localhost). "
                              "Облачные и сетевые модели не используются.") from exc
    return url.rstrip("/")


def _opener():
    # Системный прокси не используется — запрос не должен уйти за пределы компьютера.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _request(url: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data else "GET")
    with _opener().open(req, timeout=timeout) as resp:  # noqa: S310 — адрес проверен check_local_url
        return json.loads(resp.read().decode("utf-8"))


class LocalLLM:
    def __init__(self, kind: str, url: str, model: str):
        self.kind = kind
        self.url = check_local_url(url)
        self.model = model.strip()

    @classmethod
    def from_settings(cls, ctx: AppContext) -> "LocalLLM | None":
        with ctx.db.read() as s:
            st = get_all(s)
        if not st.get("llm.enabled"):
            return None
        return cls(st.get("llm.kind") or "ollama", st.get("llm.url") or "http://127.0.0.1:11434", st.get("llm.model") or "")

    def status(self) -> LLMStatus:
        if not self.model:
            return LLMStatus(False, "Модель не выбрана в настройках")
        try:
            if self.kind == "ollama":
                tags = _request(f"{self.url}/api/tags", timeout=2.5)
                names = {m.get("name", "") for m in tags.get("models", [])}
                if self.model not in names and f"{self.model}:latest" not in names:
                    return LLMStatus(False, f"Сервер модели работает, но модель «{self.model}» не установлена")
            else:
                models = _request(f"{self.url}/v1/models", timeout=2.5)
                names = {m.get("id", "") for m in models.get("data", [])}
                if names and self.model not in names:
                    return LLMStatus(False, f"Модель «{self.model}» не найдена на локальном сервере")
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            return LLMStatus(False, f"Локальная модель недоступна ({exc.__class__.__name__}). Работает базовый режим.")
        return LLMStatus(True, f"Локальная модель «{self.model}» доступна")

    def chat(self, system: str, user: str, timeout: float = 180.0) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            if self.kind == "ollama":
                out = _request(f"{self.url}/api/chat", {"model": self.model, "messages": messages, "stream": False,
                                                         "options": {"temperature": 0.2}}, timeout)
                return out.get("message", {}).get("content", "").strip()
            out = _request(f"{self.url}/v1/chat/completions", {"model": self.model, "messages": messages, "temperature": 0.2}, timeout)
            return out["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError) as exc:
            raise DomainError(f"Локальная модель не ответила: {exc}") from exc


SYSTEM_PROMPT = (
    "Ты помощник мастера автосервиса. Отвечай на русском языке. Используй ТОЛЬКО сведения из блока «Контекст». "
    "Не придумывай OEM-артикулы, совместимость деталей и технические параметры. Всё, что не подтверждено контекстом, "
    "помечай словами «гипотеза — требует проверки». Ссылайся на источники номерами [1], [2]. Если данных недостаточно — "
    "прямо скажи, каких сведений не хватает. Для тормозов, рулевого управления, подушек безопасности, высоковольтных "
    "систем и топливной системы напоминай о необходимости профессиональной проверки. Не назначай замену деталей без "
    "результатов диагностики."
)


def build_context(basic: dict) -> tuple[str, list[str]]:
    sources: list[str] = []
    parts: list[str] = []

    def src(label: str) -> int:
        sources.append(label)
        return len(sources)

    for h in basic.get("hypotheses", []):
        n = src(f"Правило: {h['title']} — {h['source']}")
        parts.append(f"[{n}] {h['title']}. Возможные причины: {'; '.join(h['causes'])}. Проверки: {'; '.join(h['checks'])}.")
    for r in basic.get("history", [])[:5]:
        n = src(f"Заказ-наряд № {r['number']} (подтверждённый ремонт)")
        parts.append(f"[{n}] {r['vehicle']}: жалоба «{r['complaint']}», диагностика «{r['diagnosis']}», работы: {'; '.join(r['services'])}.")
    for d in basic.get("library", [])[:5]:
        n = src(f"Библиотека: {d['title']} — {d['source']} ({d['status_title']})")
        parts.append(f"[{n}] {d['title']}: {d['snippet']}")
    return "\n".join(parts) or "Контекст пуст: в локальной базе нет подходящих сведений.", sources


def ask_llm(ctx: AppContext, inp: dict, basic: dict) -> dict:
    llm = LocalLLM.from_settings(ctx)
    if llm is None:
        raise DomainError("Режим локальной модели выключен в настройках")
    status = llm.status()
    if not status.available:
        raise DomainError(status.text)
    context, sources = build_context(basic)
    user = (f"Автомобиль: {inp.get('make', '')} {inp.get('model', '')} {inp.get('year', '')}, двигатель {inp.get('engine', '')}, "
            f"пробег {inp.get('mileage', '')}.\nЖалоба: {inp.get('complaint', '')}\nКоды ошибок: {inp.get('codes', '')}\n"
            f"Результаты диагностики: {inp.get('diagnostics', '')}\n\nКонтекст:\n{context}\n\n"
            "Составь: 1) возможные причины (гипотезы) со ссылками на источники; 2) порядок диагностических проверок; "
            "3) каких сведений не хватает.")
    text = llm.chat(SYSTEM_PROMPT, user)
    return {"text": text, "sources": sources, "level": "needs_check", "model": llm.model}
