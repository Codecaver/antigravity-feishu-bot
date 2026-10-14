"""Normalize model output into Feishu-card-friendly markdown.

Feishu CardKit markdown supports standard fences and lists:
  - code: ```python ... ```
  - unordered: `- item`
  - ordered: `1. item`

Do NOT collapse fences into single backticks, and do NOT replace list markers
with unicode bullets (•) — those break Feishu native rendering.
"""

from __future__ import annotations

import os
import re
from typing import Iterator, Tuple


_FILE_MD_LINK = re.compile(
    r"\[(?:file:///)?([^\]]+)\]\((?:file://)?(/?[^\s\)]+)\)",
    re.IGNORECASE,
)
_BARE_FILE_URL = re.compile(r"file://(/[^\s\)\]\>]+)", re.IGNORECASE)
_ANGLE_FILE = re.compile(r"<(?:file://)?(/Users/[^\s>]+|/home/[^\s>]+|/tmp/[^\s>]+)>")

# Closed fenced code block (language optional). Keep body untouched.
_CLOSED_FENCE = re.compile(r"```([^\n`]*)\n([\s\S]*?)\n```")
# Line that opens a fence (for streaming incomplete blocks)
_OPEN_FENCE_LINE = re.compile(r"^```([^\n`]*)$", re.MULTILINE)


def _pretty_path(raw: str) -> str:
    path = (raw or "").strip()
    path = path.replace("file://", "")
    if path.startswith("`") and path.endswith("`"):
        path = path[1:-1]
    m = re.search(r"^(.*?)(?:#L(\d+)(?:-L(\d+))?|:(\d+)(?:-(\d+))?)$", path)
    if m:
        base, l1, l2, c1, c2 = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        start = l1 or c1
        end = l2 or c2
        rel = _short_path(base)
        if start and end:
            return f"{rel}:{start}-{end}"
        if start:
            return f"{rel}:{start}"
        return _short_path(base)
    return _short_path(path)


def _short_path(path: str) -> str:
    path = path.rstrip("/")
    if not path:
        return path
    for marker in ("/github-repo/", "/gitlab-repo/", "/antigravity-feishu-bot/"):
        idx = path.find(marker)
        if idx >= 0:
            return path[idx + len(marker) :].lstrip("/") or os.path.basename(path)
    home = os.path.expanduser("~")
    if path.startswith(home + "/"):
        return "~/" + path[len(home) + 1 :]
    if len(path) > 64:
        return "…/" + "/".join(path.split("/")[-3:])
    return path


def _sanitize_prose(text: str) -> str:
    """Sanitize non-code markdown for Feishu cards."""

    def _link_sub(m: re.Match) -> str:
        label, target = m.group(1), m.group(2)
        pretty = _pretty_path(target)
        label_pathish = ("/" in label) or label.endswith(
            (".py", ".ts", ".js", ".md", ".go", ".rs", ".tsx", ".jsx")
        )
        if label_pathish:
            alt = _pretty_path(label)
            if len(alt) <= len(pretty):
                pretty = alt
        return f"`{pretty}`"

    out = _FILE_MD_LINK.sub(_link_sub, text)
    out = _ANGLE_FILE.sub(lambda m: f"`{_pretty_path(m.group(1))}`", out)
    out = _BARE_FILE_URL.sub(lambda m: f"`{_pretty_path(m.group(1))}`", out)

    # Headings → bold (Feishu mobile card markdown is weak on # headers)
    out = re.sub(r"^(#{1,6})\s*(.+)$", r"**\2**", out, flags=re.MULTILINE)

    # Unicode / weird bullets → Feishu-native ordered lists when 2+ consecutive
    # otherwise keep as `- ` unordered (also native). Never leave `•`/`·`.
    out = re.sub(r"^[\t ]*[•·▪◦]\s+", "- ", out, flags=re.MULTILINE)
    # Nested indent bullets: normalize to single-level `- ` (Feishu cards)
    out = re.sub(r"^[\t ]+([-*])\s+", r"- ", out, flags=re.MULTILINE)
    # `*` list markers → `-` (avoid clashing with emphasis / star unpack in prose)
    out = re.sub(r"^\*\s+", "- ", out, flags=re.MULTILINE)

    out = _bullets_to_ordered(out)
    return out


def _bullets_to_ordered(text: str) -> str:
    """Turn consecutive `- item` runs (len>=2) into `1. 2. 3.` Feishu sequences."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    bullet_re = re.compile(r"^- (.+)$")

    while i < len(lines):
        m = bullet_re.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        block = []
        while i < len(lines):
            bm = bullet_re.match(lines[i])
            if not bm:
                break
            block.append(bm.group(1))
            i += 1
        if len(block) >= 2:
            for idx, item in enumerate(block, 1):
                out.append(f"{idx}. {item}")
        else:
            out.append(f"- {block[0]}")
    return "\n".join(out)


def _iter_fence_segments(text: str) -> Iterator[Tuple[str, str]]:
    """Yield (kind, chunk) where kind is 'prose' | 'code'."""
    pos = 0
    for m in _CLOSED_FENCE.finditer(text):
        if m.start() > pos:
            yield "prose", text[pos : m.start()]
        # Preserve fence exactly (normalize rare \r\n only)
        fence = m.group(0).replace("\r\n", "\n")
        yield "code", fence
        pos = m.end()
    tail = text[pos:]
    if not tail:
        return
    # Incomplete streaming fence: leave raw from opening ``` to end
    open_m = _OPEN_FENCE_LINE.search(tail)
    if open_m and "```" not in tail[open_m.end() :]:
        if open_m.start() > 0:
            yield "prose", tail[: open_m.start()]
        yield "code", tail[open_m.start() :]
    else:
        # half-closed or no fence
        yield "prose", tail


def sanitize_feishu_markdown(text: str) -> str:
    """Make assistant markdown render cleanly in Feishu cards/messages."""
    if not text:
        return ""

    parts: list[str] = []
    for kind, chunk in _iter_fence_segments(text.replace("\r\n", "\n")):
        if kind == "code":
            parts.append(chunk)
        else:
            parts.append(_sanitize_prose(chunk))
    return "".join(parts).strip()
