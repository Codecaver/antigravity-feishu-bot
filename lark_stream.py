"""Feishu CardKit native streaming (typewriter) — same mechanism as lark-agent-bridge."""

from __future__ import annotations

import json
import time
from typing import Optional

from lark_oapi.api.cardkit.v1 import (
    Card,
    ContentCardElementRequest,
    ContentCardElementRequestBody,
    CreateCardRequest,
    CreateCardRequestBody,
    SettingsCardRequest,
    SettingsCardRequestBody,
    UpdateCardRequest,
    UpdateCardRequestBody,
)
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from lark_client import api_client
from logger import log
from utils.feishu_markdown import sanitize_feishu_markdown

ELEMENT_ID = "md_body"
INITIAL_SUMMARY = "生成中…"


def build_streaming_card_json(text: str, streaming: bool = True) -> dict:
    content = text if text else "…"
    cfg = {
        "streaming_mode": streaming,
        "summary": {"content": _summary(content)},
    }
    if streaming:
        cfg["streaming_config"] = {
            "print_frequency_ms": {"default": 50},
            "print_step": {"default": 2},
            "print_strategy": "fast",
        }
    return {
        "schema": "2.0",
        "config": cfg,
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "element_id": ELEMENT_ID,
                    "content": content,
                }
            ]
        },
    }


def _truncate_reasoning_body(body: str, limit: int = 5000) -> str:
    if len(body) <= limit:
        return body
    cut = body[: limit - 30]
    for sep in ("\n\n", "\n", "。", ". ", "；", "; "):
        idx = cut.rfind(sep)
        if idx >= limit // 3:
            cut = cut[: idx + len(sep)].rstrip()
            break
    return cut + "\n\n_(思考过长已截断)_"


def build_reasoning_collapsible(reasoning_text: str, expanded: bool = False) -> dict:
    """对齐 lark-agent-bridge：可折叠思考面板。"""
    body = _truncate_reasoning_body((reasoning_text or "").strip()) or "_（无详细思考内容）_"
    title = "🧠 **思考中**" if expanded else "🧠 **思考完成，点击查看**"
    return {
        "tag": "collapsible_panel",
        "expanded": bool(expanded),
        "header": {
            "title": {"tag": "markdown", "content": title},
            "vertical_align": "center",
            "icon": {
                "tag": "standard_icon",
                "token": "down-small-ccm_outlined",
                "size": "16px 16px",
            },
            "icon_position": "follow_text",
            "icon_expanded_angle": -180,
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "vertical_spacing": "8px",
        "padding": "8px 8px 8px 8px",
        "elements": [
            {
                "tag": "markdown",
                "content": body,
                "text_size": "notation",
            }
        ],
    }


def build_final_markdown_card_json(
    text: str,
    elements_extra: Optional[list] = None,
    reasoning_text: Optional[str] = None,
    user_text: Optional[str] = None,
) -> dict:
    """Schema 2.0 final card (streaming off)，可选保留思考折叠面板与用户引用。"""
    from utils.feishu_markdown import format_user_quote, sanitize_feishu_markdown

    body_elements = []
    quote = format_user_quote(user_text or "")
    if quote:
        body_elements.append({"tag": "markdown", "content": quote})
    if reasoning_text and reasoning_text.strip():
        body_elements.append(build_reasoning_collapsible(reasoning_text, expanded=False))
    body_elements.append(
        {
            "tag": "markdown",
            "element_id": ELEMENT_ID,
            "content": sanitize_feishu_markdown(text) or "_(空回复)_",
        }
    )
    if elements_extra:
        body_elements.extend(elements_extra)
    # 与正文空一行（不要 &nbsp;+\n\n，飞书里会变成空两行）
    body_elements.append({"tag": "markdown", "content": "\n_✅ 已完成_"})
    return {
        "schema": "2.0",
        "config": {
            "streaming_mode": False,
            # 会话列表预览用短状态，勿塞整段正文
            "summary": {"content": "已完成"},
        },
        "body": {"elements": body_elements},
    }


def _summary(text: str, max_chars: int = 40) -> str:
    cleaned = re_sub_ws(text)
    if not cleaned:
        return INITIAL_SUMMARY
    return cleaned if len(cleaned) <= max_chars else cleaned[: max_chars - 1] + "…"


def re_sub_ws(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", (text or "").strip())


class StreamingMarkdownCard:
    """CardKit streaming session: create → reply-by-reference → element.content updates → finish."""

    def __init__(self, card_id: str, message_id: str, sequence: int = 0):
        self.card_id = card_id
        self.message_id = message_id
        self.sequence = int(sequence or 0)
        self.content = ""
        self.failed = False
        self._last_push_at = 0.0

    @classmethod
    def attach(cls, card_id: str, message_id: str, sequence: int = 0) -> Optional["StreamingMarkdownCard"]:
        """Resume an already-sent CardKit streaming card (e.g. pipeline early placeholder)."""
        if not card_id or not message_id:
            return None
        return cls(card_id, message_id, sequence=sequence)

    @classmethod
    def start_as_reply(cls, reply_to_message_id: str, initial_text: str = "…") -> Optional["StreamingMarkdownCard"]:
        if not reply_to_message_id:
            return None
        try:
            card_id = _create_card_entity(build_streaming_card_json(initial_text, streaming=True))
            if not card_id:
                return None
            msg_id = _reply_card_by_ref(reply_to_message_id, card_id)
            if not msg_id:
                return None
            sess = cls(card_id, msg_id)
            sess.content = initial_text or "…"
            log.info(f"[CardKit] streaming card started card_id={card_id} message_id={msg_id}")
            return sess
        except Exception as e:
            log.warning(f"[CardKit] start_as_reply failed (will fallback): {e}")
            return None

    def set_content(self, text: str, force: bool = False) -> bool:
        if not self.card_id:
            return False
        # 内容 patch 失败是可恢复的（网关超时等），勿永久 failed，留给 replace_with_card
        text = sanitize_feishu_markdown(text) if text else "…"
        if not text:
            text = "…"
        if text == self.content and not force:
            return True
        # light client-side throttle; Feishu typewriter handles visual smoothness
        now = time.time()
        if not force and (now - self._last_push_at) < 0.045 and len(text) - len(self.content) < 24:
            self.content = text
            return True
        self.content = text
        try:
            self.sequence += 1
            ok = _update_element_content(self.card_id, ELEMENT_ID, text, self.sequence)
            if not ok:
                return False
            self._last_push_at = time.time()
            self.failed = False
            return True
        except Exception as e:
            log.warning(f"[CardKit] set_content failed: {e}")
            return False

    def finish(self, final_text: Optional[str] = None) -> bool:
        if self.failed or not self.card_id:
            return False
        try:
            if final_text is not None:
                self.set_content(final_text, force=True)
            self.sequence += 1
            settings = json.dumps(
                {
                    "config": {
                        "streaming_mode": False,
                        "summary": {"content": _summary(self.content)},
                    }
                },
                ensure_ascii=False,
            )
            req = (
                SettingsCardRequest.builder()
                .card_id(self.card_id)
                .request_body(
                    SettingsCardRequestBody.builder()
                    .settings(settings)
                    .sequence(self.sequence)
                    .uuid(f"s_{self.card_id}_{self.sequence}")
                    .build()
                )
                .build()
            )
            resp = api_client.cardkit.v1.card.settings(req)
            if resp.code != 0:
                log.warning(f"[CardKit] finish settings failed: {resp.code} {resp.msg}")
                return False
            return True
        except Exception as e:
            log.warning(f"[CardKit] finish failed: {e}")
            return False

    def replace_with_card(self, card_json: dict) -> bool:
        """Replace entire card entity (e.g. final text + choice buttons)."""
        if not self.card_id:
            return False
        try:
            self.sequence += 1
            card = Card.builder().type("card_json").data(json.dumps(card_json, ensure_ascii=False)).build()
            req = (
                UpdateCardRequest.builder()
                .card_id(self.card_id)
                .request_body(
                    UpdateCardRequestBody.builder()
                    .card(card)
                    .sequence(self.sequence)
                    .uuid(f"u_{self.card_id}_{self.sequence}")
                    .build()
                )
                .build()
            )
            resp = api_client.cardkit.v1.card.update(req)
            if resp.code != 0:
                log.warning(f"[CardKit] replace_with_card failed: {resp.code} {resp.msg}")
                # 仅在明确不可恢复时标记 failed（schema/权限类）
                if int(getattr(resp, "code", 0) or 0) in (200861, 200830, 230099):
                    self.failed = True
                return False
            self.failed = False
            return True
        except Exception as e:
            log.warning(f"[CardKit] replace_with_card error: {e}")
            return False


def create_card_entity(card_json: dict) -> Optional[str]:
    """Public alias for CardKit card.create."""
    return _create_card_entity(card_json)


def reply_card_by_ref(message_id: str, card_id: str) -> Optional[str]:
    """Public alias for reply with card reference."""
    return _reply_card_by_ref(message_id, card_id)


def _create_card_entity(card_json: dict) -> Optional[str]:
    # CardKit create 也走统一样式（去顶栏）；流式 2.0 卡本身通常无 header.template
    try:
        from lark_client import normalize_card_style
        card_json = normalize_card_style(card_json)
    except Exception:
        pass
    req = (
        CreateCardRequest.builder()
        .request_body(
            CreateCardRequestBody.builder()
            .type("card_json")
            .data(json.dumps(card_json, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = api_client.cardkit.v1.card.create(req)
    if resp.code != 0:
        log.warning(f"[CardKit] create failed: {resp.code} {resp.msg}")
        return None
    return getattr(resp.data, "card_id", None) if resp.data else None


def _reply_card_by_ref(message_id: str, card_id: str) -> Optional[str]:
    content = json.dumps({"type": "card", "data": {"card_id": card_id}})
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(content)
            .build()
        )
        .build()
    )
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        log.warning(f"[CardKit] reply-by-ref failed: {resp.code} {resp.msg}")
        return None
    try:
        return json.loads(resp.raw.content).get("data", {}).get("message_id")
    except Exception:
        return None


def _update_element_content(card_id: str, element_id: str, content: str, sequence: int) -> bool:
    req = (
        ContentCardElementRequest.builder()
        .card_id(card_id)
        .element_id(element_id)
        .request_body(
            ContentCardElementRequestBody.builder()
            .content(content)
            .sequence(sequence)
            .uuid(f"c_{card_id}_{sequence}")
            .build()
        )
        .build()
    )
    resp = api_client.cardkit.v1.card_element.content(req)
    if resp.code != 0:
        log.warning(f"[CardKit] element.content failed: {resp.code} {resp.msg}")
        return False
    return True


def delete_message_sdk(message_id: str) -> bool:
    """Best-effort delete (e.g. remove early typing placeholder)."""
    if not message_id:
        return False
    try:
        from lark_oapi.api.im.v1 import DeleteMessageRequest

        req = DeleteMessageRequest.builder().message_id(message_id).build()
        resp = api_client.im.v1.message.delete(req)
        if resp.code != 0:
            log.debug(f"[CardKit] delete message {message_id} failed: {resp.msg}")
            return False
        return True
    except Exception as e:
        log.debug(f"[CardKit] delete message error: {e}")
        return False
