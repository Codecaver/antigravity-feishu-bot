"""RunState reducer — mirrors lark-agent-bridge src/card/run-state.ts."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional


def create_initial_state(run_id: Optional[str] = None) -> Dict[str, Any]:
    now = time.time()
    return {
        "runId": run_id or str(uuid.uuid4()),
        "blocks": [],
        "todos": [],
        "reasoning": {"content": "", "active": False},
        "footer": "starting",
        "activity": {"kind": "phase", "phase": "starting"},
        "terminal": "running",
        "errorMsg": "",
        "startedAt": now,
        "updatedAt": now,
        "lastActivityAt": now,
        "userText": "",
        "chatId": "",
        "retryUserText": "",
    }


def _touch(state: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    now = time.time()
    out = {**state, **kwargs, "updatedAt": now, "lastActivityAt": now}
    return out


def _close_streaming_text(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for b in blocks:
        if b.get("kind") == "text" and b.get("streaming"):
            out.append({**b, "streaming": False})
        else:
            out.append(b)
    return out


_TERMINAL = frozenset({"done", "error", "interrupted"})


def reduce(state: Dict[str, Any], evt: Dict[str, Any]) -> Dict[str, Any]:
    et = evt.get("type")
    # 终态锁定：停止/完成后不再被 done/error/流式事件覆盖（避免 stop 后刷成失败）
    if state.get("terminal") in _TERMINAL and et not in ("meta",):
        return state

    if et == "meta":
        patch = {}
        if "userText" in evt:
            patch["userText"] = evt["userText"]
            patch["retryUserText"] = evt.get("retryUserText", evt["userText"])
        if "chatId" in evt:
            patch["chatId"] = evt["chatId"]
        return _touch(state, **patch) if patch else state

    if et == "text":
        delta = evt.get("delta") or ""
        if not delta:
            return state
        blocks = list(state.get("blocks") or [])
        last = blocks[-1] if blocks else None
        if last and last.get("kind") == "text" and last.get("streaming"):
            blocks = blocks[:-1] + [{**last, "content": (last.get("content") or "") + delta}]
        else:
            blocks = blocks + [{"kind": "text", "content": delta, "streaming": True}]
        return _touch(
            state,
            blocks=blocks,
            reasoning={**state.get("reasoning", {}), "active": False},
            footer="streaming",
            activity={"kind": "phase", "phase": "streaming"},
        )

    if et == "thinking":
        delta = evt.get("delta") or ""
        if not delta:
            return state
        reasoning = state.get("reasoning") or {"content": "", "active": False}
        return _touch(
            state,
            reasoning={"content": (reasoning.get("content") or "") + delta, "active": True},
            footer="thinking",
            activity={"kind": "phase", "phase": "thinking"},
        )

    if et == "set_thinking":
        content = evt.get("content") or ""
        active = evt.get("active", True)
        return _touch(
            state,
            reasoning={"content": content, "active": active},
            footer="thinking" if active else state.get("footer"),
            activity={"kind": "phase", "phase": "thinking"} if active else state.get("activity"),
        )

    if et == "progress":
        phase = evt.get("phase") or state.get("footer") or "thinking"
        return _touch(
            state,
            footer=phase,
            activity={
                "kind": "phase",
                "phase": phase,
                "label": evt.get("label") or "",
                "detail": evt.get("detail") or "",
            },
        )

    if et == "tool_use":
        tool = {
            "id": evt.get("id") or str(uuid.uuid4()),
            "name": evt.get("name") or "tool",
            "input": evt.get("input") or {},
            "label": evt.get("label") or evt.get("name") or "tool",
            "status": "running",
        }
        blocks = _close_streaming_text(list(state.get("blocks") or []))
        blocks.append({"kind": "tool", "tool": tool})
        return _touch(
            state,
            blocks=blocks,
            reasoning={**state.get("reasoning", {}), "active": False},
            footer="tool",
            activity={"kind": "tool", "name": tool["name"], "label": tool["label"]},
        )

    if et == "tool_result":
        tool_id = evt.get("id")
        status = evt.get("status") or "done"
        blocks = []
        for b in state.get("blocks") or []:
            if b.get("kind") == "tool" and (b.get("tool") or {}).get("id") == tool_id:
                tool = {**(b.get("tool") or {}), "status": status}
                if evt.get("label"):
                    tool["label"] = evt["label"]
                blocks.append({**b, "tool": tool})
            else:
                blocks.append(b)
        return _touch(state, blocks=blocks)

    if et == "set_text":
        # Replace streaming text block with full assistant text (non-delta sync)
        text = evt.get("content") or ""
        blocks = [b for b in (state.get("blocks") or []) if b.get("kind") != "text"]
        if text:
            blocks.append({"kind": "text", "content": text, "streaming": True})
        return _touch(
            state,
            blocks=blocks,
            reasoning={**state.get("reasoning", {}), "active": False},
            footer="streaming" if text else state.get("footer"),
            activity={"kind": "phase", "phase": "streaming"} if text else state.get("activity"),
        )

    if et == "error":
        return _touch(
            state,
            terminal="error",
            errorMsg=evt.get("message") or "agent 失败",
            blocks=_close_streaming_text(list(state.get("blocks") or [])),
            reasoning={**state.get("reasoning", {}), "active": False},
            footer="error",
        )

    if et == "interrupted":
        return _touch(
            state,
            terminal="interrupted",
            blocks=_close_streaming_text(list(state.get("blocks") or [])),
            reasoning={**state.get("reasoning", {}), "active": False},
            footer="interrupted",
        )

    if et == "done":
        if evt.get("content"):
            # Final prose override
            blocks = [b for b in (state.get("blocks") or []) if b.get("kind") != "text"]
            blocks.append({"kind": "text", "content": evt["content"], "streaming": False})
        else:
            blocks = _close_streaming_text(list(state.get("blocks") or []))
        reasoning = state.get("reasoning") or {"content": "", "active": False}
        if evt.get("reasoning"):
            reasoning = {"content": evt["reasoning"], "active": False}
        else:
            reasoning = {**reasoning, "active": False}
        return _touch(
            state,
            terminal="done",
            blocks=blocks,
            reasoning=reasoning,
            footer="done",
            activity={"kind": "phase", "phase": "done"},
        )

    return state


def assistant_text(state: Dict[str, Any]) -> str:
    parts = []
    for b in state.get("blocks") or []:
        if b.get("kind") == "text":
            parts.append(b.get("content") or "")
    return "".join(parts).strip()
