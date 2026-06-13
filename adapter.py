from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:18888"
DEFAULT_CHAT_ID = "whisplay-device"
MAX_MESSAGE_LENGTH = 8192


def _headers(token: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def _extra(config: PlatformConfig) -> dict:
    return getattr(config, "extra", {}) or {}


def _base_url_from(extra: dict) -> str:
    return (extra.get("base_url") or os.getenv("WHISPLAY_IM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _token_from(extra: dict) -> str:
    return extra.get("token") or os.getenv("WHISPLAY_IM_TOKEN", "")


def check_requirements() -> bool:
    return HTTPX_AVAILABLE


def validate_config(config) -> bool:
    return bool(_base_url_from(_extra(config)))


def is_connected(config) -> bool:
    return validate_config(config)


def _env_enablement() -> dict | None:
    base_url = os.getenv("WHISPLAY_IM_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    if not base_url:
        return None
    chat_id = os.getenv("WHISPLAY_IM_CHAT_ID", DEFAULT_CHAT_ID).strip() or DEFAULT_CHAT_ID
    seed: dict[str, Any] = {
        "base_url": base_url,
        "chat_id": chat_id,
        "home_channel": {"chat_id": chat_id, "name": "Whisplay"},
    }
    token = os.getenv("WHISPLAY_IM_TOKEN", "").strip()
    if token:
        seed["token"] = token
    return seed


class WhisplayIMAdapter(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config=config, platform=Platform("whisplay_im"))
        extra = _extra(config)
        self._base_url = _base_url_from(extra)
        self._token = _token_from(extra)
        self._chat_id = extra.get("chat_id") or os.getenv("WHISPLAY_IM_CHAT_ID", DEFAULT_CHAT_ID)
        self._poll_path = extra.get("poll_path") or os.getenv("WHISPLAY_IM_POLL_PATH", "/whisplay-im/poll")
        self._send_path = extra.get("send_path") or os.getenv("WHISPLAY_IM_SEND_PATH", "/whisplay-im/send")
        self._status_path = extra.get("status_path") or os.getenv("WHISPLAY_IM_STATUS_PATH", "/whisplay-im/status")
        self._wait_sec = int(extra.get("wait_sec") or os.getenv("WHISPLAY_IM_WAIT_SEC", "30"))
        self._client: Optional["httpx.AsyncClient"] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._seen_ids: set[str] = set()

    async def connect(self) -> bool:
        if not HTTPX_AVAILABLE:
            logger.warning("[whisplay-im] httpx is not installed")
            return False
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._wait_sec + 10.0, connect=5.0))
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._mark_connected()
        logger.info("[whisplay-im] Connected to %s", self._base_url)
        return True

    async def disconnect(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._client:
            await self._client.aclose()
            self._client = None
        self._mark_disconnected()
        logger.info("[whisplay-im] Disconnected")

    async def _poll_loop(self) -> None:
        assert self._client is not None
        backoff = 1.0
        while self._running:
            try:
                resp = await self._client.get(
                    f"{self._base_url}{self._poll_path}",
                    params={"waitSec": self._wait_sec},
                    headers=_headers(self._token),
                )
                if resp.status_code == 204:
                    backoff = 1.0
                    continue
                if resp.status_code >= 300:
                    logger.warning("[whisplay-im] poll HTTP %s: %s", resp.status_code, resp.text[:200])
                    await asyncio.sleep(min(backoff, 30.0))
                    backoff = min(backoff * 2, 30.0)
                    continue
                backoff = 1.0
                await self._handle_payload(resp.json())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[whisplay-im] poll failed: %s", exc)
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)

    async def _handle_payload(self, data: dict) -> None:
        text = (data.get("message") or data.get("text") or "").strip()
        if not text:
            messages = data.get("messages") or []
            if messages and isinstance(messages[-1], dict):
                text = str(messages[-1].get("content") or "").strip()
        if not text:
            return

        message_id = str(data.get("id") or data.get("messageId") or data.get("timestamp") or uuid.uuid4().hex)
        if message_id in self._seen_ids:
            return
        self._seen_ids.add(message_id)
        if len(self._seen_ids) > 1000:
            self._seen_ids = set(list(self._seen_ids)[-500:])

        source = self.build_source(
            chat_id=self._chat_id,
            chat_name="Whisplay",
            chat_type="dm",
            user_id=self._chat_id,
            user_name="Whisplay",
            message_id=message_id,
        )
        event = MessageEvent(
            text=text,
            message_type=MessageType.COMMAND if text.startswith("/") else MessageType.TEXT,
            source=source,
            raw_message=data,
            message_id=message_id,
        )
        if data.get("imageBase64") or data.get("image"):
            event.text = f"{event.text}\n\n[Whisplay attached an image payload.]"
        await self.handle_message(event)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="whisplay-im is not connected", retryable=True)
        body = {"reply": (content or "")[:MAX_MESSAGE_LENGTH], "emoji": "🙂"}
        try:
            resp = await self._client.post(
                f"{self._base_url}{self._send_path}",
                json=body,
                headers=_headers(self._token),
                timeout=20.0,
            )
            if resp.status_code >= 300:
                return SendResult(
                    success=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    retryable=resp.status_code >= 500,
                )
            msg_id = uuid.uuid4().hex[:12]
            try:
                data = resp.json()
                msg_id = str(data.get("id") or data.get("messageId") or msg_id)
            except Exception:
                pass
            return SendResult(success=True, message_id=msg_id)
        except Exception as exc:
            return SendResult(success=False, error=str(exc), retryable=True)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        if not self._client:
            return
        try:
            await self._client.post(
                f"{self._base_url}{self._status_path}",
                json={"status": "thinking", "emoji": "🙂", "text": "Thinking..."},
                headers=_headers(self._token),
                timeout=5.0,
            )
        except Exception:
            logger.debug("[whisplay-im] failed to send typing status", exc_info=True)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"id": chat_id, "name": "Whisplay", "type": "dm"}


def register(ctx) -> None:
    ctx.register_platform(
        name="whisplay_im",
        label="Whisplay IM",
        adapter_factory=lambda cfg: WhisplayIMAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["WHISPLAY_IM_BASE_URL"],
        install_hint="httpx is included with Hermes",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="WHISPLAY_IM_CHAT_ID",
        allowed_users_env="WHISPLAY_IM_ALLOWED_USERS",
        allow_all_env="WHISPLAY_IM_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji="W",
        pii_safe=True,
        platform_hint=(
            "You are communicating through the Whisplay device screen and "
            "button interface. Keep replies concise and readable on a small display."
        ),
    )
