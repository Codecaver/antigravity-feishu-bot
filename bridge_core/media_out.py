"""Outbound media delivery — thin wrapper over multimodal (bridge-like)."""

from __future__ import annotations

from typing import List, Optional

from multimodal import extract_and_upload_resources


def deliver_outbound_media(
    text: str,
    message_id: Optional[str],
    api_client,
    allowed_dirs: Optional[List] = None,
    chat_id: Optional[str] = None,
) -> str:
    """Upload local media refs as Feishu native msgs; return card-safe prose."""
    cleaned = extract_and_upload_resources(
        text,
        message_id,
        api_client,
        additional_safe_dirs=allowed_dirs,
        chat_id=chat_id,
    )
    return cleaned if isinstance(cleaned, str) else (text or "")
