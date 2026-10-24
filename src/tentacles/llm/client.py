"""Minimal OpenAI-compatible chat client (httpx based)."""

from __future__ import annotations

import logging

import httpx

from tentacles.config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None):
        settings = get_settings()
        self.api_key = api_key or settings.llm_api_key
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        if not self.api_key:
            raise LLMError("LLM api key is empty；设置 TENTACLES_LLM_API_KEY 或写入 .env")

    def _payload(self, system: str, user: str, max_tokens: int, temperature: float) -> dict:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

    async def chat(self, system: str, user: str, *, max_tokens: int = 4096,
                   temperature: float = 0.4) -> str:
        async with httpx.AsyncClient(timeout=120) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=self._payload(system, user, max_tokens, temperature),
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise LLMError(f"LLM request failed: {exc}") from exc
        try:
            return resp.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"unexpected LLM response: {resp.text[:500]}") from exc
