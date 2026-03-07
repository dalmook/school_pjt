from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings


class TelegramService:
    def __init__(self) -> None:
        settings = get_settings()
        self.token = settings.telegram_bot_token
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("TELEGRAM_BOT_TOKEN 이 설정되지 않았습니다.")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{self.base_url}/{method}", json=payload)
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise RuntimeError(body.get("description", "Telegram API 오류"))
            return body

    async def send_message(self, chat_id: str, text: str, inline_keyboard: list[list[dict[str, str]]] | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if inline_keyboard:
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
        await self.call("sendMessage", payload)

    async def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    async def set_webhook(self, webhook_url: str, secret_token: str) -> dict[str, Any]:
        return await self.call("setWebhook", {"url": webhook_url, "secret_token": secret_token})
