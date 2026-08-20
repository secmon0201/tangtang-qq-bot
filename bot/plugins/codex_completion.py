from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status
from nonebot import get_driver, logger

from bot.config import settings
from bot.services.codex_completion import (
    DEFAULT_COMPLETION_MESSAGE,
    MAX_COMPLETION_DETAILS_LENGTH,
    MAX_COMPLETION_MESSAGE_LENGTH,
    fetch_test_group_members,
    parse_test_group_notification,
    notify_codex_completion,
    notify_test_group,
)


driver = get_driver()


def _authorized(request: Request) -> bool:
    token = settings.codex_completion_notify_token
    provided = request.headers.get("X-Codex-Completion-Token", "")
    return bool(token) and hmac.compare_digest(provided, token)


@driver.server_app.post("/internal/codex/test-group-members")
async def receive_test_group_members(request: Request) -> dict[str, object]:
    """Return current fixed-test-group display members for local test rendering only."""
    if not settings.codex_completion_notify_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification is disabled")
    if not _authorized(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid notification token")
    try:
        members = await fetch_test_group_members()
    except Exception as exc:
        logger.exception("Codex test-group member lookup failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OneBot member lookup failed",
        ) from exc
    return {"members": members}


@driver.server_app.post("/internal/codex/completion")
async def receive_codex_completion(request: Request) -> dict[str, bool]:
    """Receive a local Codex completion event and notify the fixed QQ target."""
    if not settings.codex_completion_notify_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification is disabled")
    if not _authorized(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid notification token")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="JSON body must be an object")
    message = payload.get("message", DEFAULT_COMPLETION_MESSAGE)
    if not isinstance(message, str) or len(message.strip()) > MAX_COMPLETION_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"message must be text up to {MAX_COMPLETION_MESSAGE_LENGTH} characters",
        )
    details = payload.get("details")
    if details is not None and (
        not isinstance(details, str) or len(details.strip()) > MAX_COMPLETION_DETAILS_LENGTH
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"details must be text up to {MAX_COMPLETION_DETAILS_LENGTH} characters",
        )
    try:
        await notify_codex_completion(message, details=details)
    except Exception as exc:
        logger.exception("Codex completion notification failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OneBot notification delivery failed",
        ) from exc
    return {"ok": True}


@driver.server_app.post("/internal/codex/test-group-notice")
async def receive_test_group_notice(request: Request) -> dict[str, bool]:
    """Receive a validated local text/image delivery for the fixed test group."""
    if not settings.codex_completion_notify_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="notification is disabled")
    if not _authorized(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid notification token")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="JSON body must be an object")
    try:
        notification = parse_test_group_notification(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    try:
        await notify_test_group(notification)
    except Exception as exc:
        logger.exception("Codex test-group notification failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OneBot notification delivery failed",
        ) from exc
    return {"ok": True}
