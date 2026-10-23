"""render_card(state) → CardKit 2.0 JSON — mirrors lark-agent-bridge run-renderer."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from utils.feishu_markdown import format_user_quote, sanitize_feishu_markdown

REASONING_MAX = 5000
COLLAPSE_TOOL_THRESHOLD = 3


def render_card(state: Dict[str, Any], elements_extra: Optional[List[dict]] = None) -> dict:
    elements: List[dict] = []
    terminal = state.get("terminal") or "running"

    # Bridge-like：顶部引用用户原话
    quote_md = format_user_quote(state.get("userText") or "")
    if quote_md:
        elements.append({"tag": "markdown", "content": quote_md})

    reasoning = state.get("reasoning") or {}
    if reasoning.get("content"):
        elements.append(_reasoning_panel(reasoning.get("content") or "", bool(reasoning.get("active"))))

    body_added = False
    for group in _group_blocks(state.get("blocks") or []):
        if group["kind"] == "text":
            content = sanitize_feishu_markdown(group.get("content") or "")
            if content.strip():
                elements.append(
                    {
                        "tag": "markdown",
                        "element_id": "md_body",
                        "content": content,
                    }
                )
                body_added = True
        else:
            elements.extend(_render_tool_group(group.get("tools") or [], finalized=terminal != "running"))

    # 运行中：若非思考中，保留 md_body 占位便于后续 patch；思考中时不塞无意义省略号
    if terminal == "running" and not body_added:
        is_thinking = bool(reasoning.get("active") and reasoning.get("content"))
        if not is_thinking:
            elements.append(
                {
                    "tag": "markdown",
                    "element_id": "md_body",
                    "content": "…",
                }
            )
            body_added = True

    # choice / 额外按钮放在「已完成」之前
    if elements_extra:
        elements.extend(elements_extra)

    if terminal == "interrupted":
        elements.append(_note("⏹ 已被中断"))
    elif terminal == "error":
        msg = state.get("errorMsg") or "agent 失败"
        elements.append(_note(f"⚠️ {msg}"))
    elif terminal == "done":
        if not body_added:
            elements.append(_note("_✅ 思考与步骤已完成_"))

    if terminal == "running":
        activity = _activity_text(state)
        if activity:
            elements.append(
                {
                    "tag": "markdown",
                    "element_id": "md_activity",
                    "content": f"_{activity}_" if not activity.startswith("_") else activity,
                }
            )
        elements.append(
            _stop_button(
                state.get("runId") or "",
                state.get("chatId") or "",
                state.get("cardId") or "",
            )
        )
    elif state.get("runId") and terminal in ("error", "interrupted"):
        elements.append(
            _retry_button(
                state.get("runId") or "",
                state.get("chatId") or "",
                state.get("retryUserText") or state.get("userText") or "",
            )
        )

    if not elements:
        elements.append(
            {
                "tag": "markdown",
                "element_id": "md_body",
                "content": "…" if terminal == "running" else "_（空）_",
            }
        )

    return {
        "schema": "2.0",
        "config": {
            "streaming_mode": terminal == "running",
            "summary": {"content": _summary_text(state)},
            **(
                {
                    "streaming_config": {
                        "print_frequency_ms": {"default": 50},
                        "print_step": {"default": 2},
                        "print_strategy": "fast",
                    }
                }
                if terminal == "running"
                else {}
            ),
        },
        "body": {"elements": elements},
    }


def _note(md: str) -> dict:
    return {"tag": "markdown", "content": f"_{md}_" if not md.startswith("_") else md}


def _reasoning_panel(content: str, active: bool) -> dict:
    body = sanitize_feishu_markdown((content or "").strip())
    if len(body) > REASONING_MAX:
        body = body[: REASONING_MAX - 1] + "…"
    body = body or "_（无详细思考内容）_"
    if active:
        title = "🧠 **思考中…**"
        display_body = body + " ▌"
    else:
        title = "🧠 **思考完成，点击查看**"
        display_body = body

    return {
        "tag": "collapsible_panel",
        "expanded": bool(active),
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
        "elements": [{"tag": "markdown", "content": display_body, "text_size": "notation"}],
    }


def _group_blocks(blocks: List[Dict[str, Any]]):
    tool_buf: List[dict] = []
    for b in blocks:
        if b.get("kind") == "tool":
            tool_buf.append(b.get("tool") or {})
        else:
            if tool_buf:
                yield {"kind": "tools", "tools": tool_buf}
                tool_buf = []
            yield {"kind": "text", "content": b.get("content") or ""}
    if tool_buf:
        yield {"kind": "tools", "tools": tool_buf}


def _render_tool_group(tools: List[dict], finalized: bool) -> List[dict]:
    if not tools:
        return []
    if len(tools) < COLLAPSE_TOOL_THRESHOLD:
        return [_tool_panel(t) for t in tools]
    if finalized:
        # 终态时所有命令与步骤已完整收拢进「思考完成，点击查看」折叠面板中，外部不再冗余平铺
        return []
    prior = tools[:-1]
    latest = tools[-1]
    out: List[dict] = []
    if prior:
        names = "、".join((t.get("label") or t.get("name") or "tool")[:32] for t in prior[:6])
        out.append(_note(f"🛠️ 已完成 {len(prior)} 步：{names}"))
    out.append(_tool_panel(latest, highlight=True))
    return out


def _tool_panel(tool: dict, highlight: bool = False) -> dict:
    label = (tool.get("label") or tool.get("name") or "tool").strip()
    status = tool.get("status") or "running"
    icon = "⏳" if status == "running" else ("✅" if status == "done" else "⚠️")
    prefix = "**" if highlight else ""
    suffix = "**" if highlight else ""
    return {"tag": "markdown", "content": f"{icon} {prefix}{label}{suffix}"}


def _activity_text(state: Dict[str, Any]) -> str:
    act = state.get("activity") or {}
    phase = act.get("phase") or state.get("footer") or ""
    label = act.get("label") or ""
    if act.get("kind") == "tool":
        return f"正在执行：{act.get('label') or act.get('name') or 'tool'}…"
    mapping = {
        "starting": "启动中…",
        "thinking": "思考中…",
        "streaming": "生成中…",
        "tool": "执行工具中…",
    }
    base = mapping.get(phase, phase or "处理中…")
    if label:
        return f"{base} {label}"
    return base


def _summary_text(state: Dict[str, Any], max_chars: int = 40) -> str:
    terminal = state.get("terminal")
    if terminal == "running":
        return _activity_text(state)[:max_chars]
    if terminal == "error":
        return "失败"
    if terminal == "interrupted":
        return "已中断"
    text = ""
    for b in reversed(state.get("blocks") or []):
        if b.get("kind") == "text" and (b.get("content") or "").strip():
            text = b["content"]
            break
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return "已完成"
    return cleaned if len(cleaned) <= max_chars else cleaned[: max_chars - 1] + "…"


def _stop_button(run_id: str, chat_id: str, card_id: str = "") -> dict:
    # CardKit schema 2.0: button 直接放 elements，禁止再包 tag:action
    value = {
        "action": "stop_run",
        "run_id": run_id,
        "chat_id": chat_id,
    }
    if card_id:
        value["card_id"] = card_id
    return {
        "tag": "button",
        "element_id": "btn_stop",
        "text": {"tag": "plain_text", "content": "⏹ 停止"},
        "type": "danger",
        "width": "default",
        "behaviors": [
            {
                "type": "callback",
                "value": value,
            }
        ],
    }


def _retry_button(run_id: str, chat_id: str, user_text: str) -> dict:
    return {
        "tag": "button",
        "element_id": "btn_retry",
        "text": {"tag": "plain_text", "content": "🔄 重试"},
        "type": "primary",
        "width": "default",
        "behaviors": [
            {
                "type": "callback",
                "value": {
                    "action": "retry_run",
                    "run_id": run_id,
                    "chat_id": chat_id,
                    "user_text": (user_text or "")[:2000],
                },
            }
        ],
    }
