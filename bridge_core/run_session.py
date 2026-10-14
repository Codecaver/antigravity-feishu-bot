"""Active run session: reduce events → throttled CardKit full-card updates."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, Optional
from bridge_core.run_renderer import render_card
from bridge_core.run_state import assistant_text, create_initial_state, reduce
from lark_stream import StreamingMarkdownCard, create_card_entity, reply_card_by_ref
from logger import log

# chat_id → RunSession
_ACTIVE: Dict[str, "RunSession"] = {}
_LOCK = threading.Lock()


def register_active_run(chat_id: str, session: "RunSession") -> None:
    with _LOCK:
        _ACTIVE[chat_id] = session


def unregister_active_run(chat_id: str, run_id: Optional[str] = None) -> None:
    with _LOCK:
        cur = _ACTIVE.get(chat_id)
        if cur and (run_id is None or cur.run_id == run_id):
            _ACTIVE.pop(chat_id, None)


def get_active_run(chat_id: str) -> Optional["RunSession"]:
    with _LOCK:
        return _ACTIVE.get(chat_id)


class RunSession:
    """One Feishu CardKit card per agent run (bridge-like)."""

    def __init__(
        self,
        chat_id: str,
        card: StreamingMarkdownCard,
        state: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ):
        self.chat_id = chat_id
        self.card = card
        self.state = state or create_initial_state(run_id)
        self.run_id = self.state["runId"]
        self.state["chatId"] = chat_id
        self.failed = False
        self.stop_requested = False
        self._last_push_at = 0.0
        self._min_interval = 0.18
        self._dirty = False
        self.process = None  # subprocess / session handle for stop

    @property
    def message_id(self) -> str:
        return getattr(self.card, "message_id", "") or ""

    @property
    def card_id(self) -> str:
        return getattr(self.card, "card_id", "") or ""

    @classmethod
    def start_as_reply(
        cls,
        chat_id: str,
        reply_to_message_id: str,
        user_text: str = "",
        initial_label: str = "启动中…",
    ) -> Optional["RunSession"]:
        run_id = str(uuid.uuid4())
        state = create_initial_state(run_id)
        state["chatId"] = chat_id
        state["userText"] = user_text
        state["retryUserText"] = user_text
        state = reduce(state, {"type": "progress", "phase": "starting", "label": initial_label})
        card_json = render_card(state)
        try:
            card_id = create_card_entity(card_json)
            if not card_id:
                # fallback: simple streaming placeholder
                card = StreamingMarkdownCard.start_as_reply(reply_to_message_id, initial_label or "…")
                if not card:
                    return None
                sess = cls(chat_id, card, state=state, run_id=run_id)
                register_active_run(chat_id, sess)
                return sess
            msg_id = reply_card_by_ref(reply_to_message_id, card_id)
            if not msg_id:
                return None
            stream = StreamingMarkdownCard.attach(card_id, msg_id, sequence=0)
            sess = cls(chat_id, stream, state=state, run_id=run_id)
            register_active_run(chat_id, sess)
            log.info(f"[RunSession] started run_id={run_id} card_id={card_id} msg={msg_id}")
            return sess
        except Exception as e:
            log.warning(f"[RunSession] start_as_reply failed: {e}")
            return None

    @classmethod
    def attach(
        cls,
        chat_id: str,
        card_id: str,
        message_id: str,
        sequence: int = 0,
        run_id: Optional[str] = None,
        user_text: str = "",
    ) -> Optional["RunSession"]:
        stream = StreamingMarkdownCard.attach(card_id, message_id, sequence=sequence)
        if not stream:
            return None
        state = create_initial_state(run_id)
        state["chatId"] = chat_id
        state["userText"] = user_text
        state["retryUserText"] = user_text
        sess = cls(chat_id, stream, state=state)
        register_active_run(chat_id, sess)
        return sess

    def apply(self, evt: Dict[str, Any], push: bool = True, force: bool = False) -> Dict[str, Any]:
        self.state = reduce(self.state, evt)
        if push:
            self.push(force=force)
        return self.state

    def push(self, force: bool = False, elements_extra: Optional[list] = None) -> bool:
        if self.failed or not self.card or self.card.failed:
            return False
        now = time.time()
        terminal = self.state.get("terminal")
        # Always flush terminal transitions; throttle running updates
        if (
            not force
            and terminal == "running"
            and (now - self._last_push_at) < self._min_interval
        ):
            self._dirty = True
            return True
        card_json = render_card(self.state, elements_extra=elements_extra)
        ok = self.card.replace_with_card(card_json)
        if not ok:
            # Fallback: flatten to markdown body stream
            text = assistant_text(self.state) or _fallback_md(self.state)
            ok = self.card.set_content(text, force=True)
            if not ok:
                self.failed = True
                return False
        self._last_push_at = time.time()
        self._dirty = False
        return True

    def flush(self) -> bool:
        if self._dirty or self.state.get("terminal") != "running":
            return self.push(force=True)
        return True

    def finish_done(
        self,
        content: str,
        reasoning: str = "",
        elements_extra: Optional[list] = None,
    ) -> bool:
        if self.stop_requested or self.state.get("terminal") in ("interrupted", "done", "error"):
            return self.push(force=True, elements_extra=elements_extra)
        self.apply(
            {"type": "done", "content": content, "reasoning": reasoning},
            push=False,
        )
        return self.push(force=True, elements_extra=elements_extra)

    def finish_error(self, message: str) -> bool:
        if self.stop_requested or self.state.get("terminal") in ("interrupted", "done", "error"):
            return self.push(force=True)
        self.apply({"type": "error", "message": message}, push=False)
        return self.push(force=True)

    def finish_interrupted(self) -> bool:
        self.stop_requested = True
        self.apply({"type": "interrupted"}, push=False)
        return self.push(force=True)

    def request_stop(self) -> bool:
        """Best-effort kill attached process / mark interrupted."""
        self.stop_requested = True
        proc = self.process
        stopped = False
        try:
            if proc is not None:
                if hasattr(proc, "close") and callable(proc.close):
                    import asyncio

                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.ensure_future(proc.close())
                        else:
                            loop.run_until_complete(proc.close())
                        stopped = True
                    except Exception:
                        pass
                if hasattr(proc, "pid"):
                    import os
                    import signal

                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        stopped = True
                    except Exception:
                        try:
                            proc.terminate()
                            stopped = True
                        except Exception:
                            pass
        except Exception as e:
            log.warning(f"[RunSession] request_stop error: {e}")
        self.finish_interrupted()
        return stopped


def _fallback_md(state: Dict[str, Any]) -> str:
    parts = []
    r = (state.get("reasoning") or {}).get("content") or ""
    if r:
        parts.append(f"**思考**\n{r[:800]}")
    t = assistant_text(state)
    if t:
        parts.append(t)
    if not parts:
        parts.append(_activity_phase(state))
    return "\n\n".join(parts)


def _activity_phase(state: Dict[str, Any]) -> str:
    from bridge_core.run_renderer import _activity_text

    return _activity_text(state)
