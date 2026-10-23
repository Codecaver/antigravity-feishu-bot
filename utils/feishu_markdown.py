"""Normalize model output into Feishu CardKit-friendly markdown (bridge-like).

Keep rich formatting that CardKit 2.0 renders well:
  - headings `#` / `##`
  - bold / italic
  - ordered `1.` and unordered `-`
  - fenced code blocks
  - GFM tables
  - blockquotes `>`

Only scrub things that break Feishu cards (local image paths, file://, bare abs paths).
"""

from __future__ import annotations

import os
import re
from typing import Iterator, Tuple


_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\((?:file://)?([^)]+)\)")
_FILE_MD_LINK = re.compile(
    r"(?<!!)\[(?:file:///)?([^\]]+)\]\((?:file://)?(/?[^\s\)]+)\)",
    re.IGNORECASE,
)
_BARE_FILE_URL = re.compile(r"file://(/[^\s\)\]\>]+)", re.IGNORECASE)
_ANGLE_FILE = re.compile(r"<(?:file://)?(/Users/[^\s>]+|/home/[^\s>]+|/tmp/[^\s>]+)>")
_BARE_ABS_PATH = re.compile(
    r"(?<![`\w])((?:/Users|/home|/private/var|/var/folders|/tmp|/opt)/[^\s`\"'<>\]\)]+)"
)

_CLOSED_FENCE = re.compile(r"```([^\n`]*)\n([\s\S]*?)\n```")
_OPEN_FENCE_LINE = re.compile(r"^```([^\n`]*)$", re.MULTILINE)
# GFM table block: header | --- | rows
_TABLE_BLOCK = re.compile(
    r"(?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+",
    re.MULTILINE,
)


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


def format_user_quote(user_text: str, max_chars: int = 280) -> str:
    """Bridge-like quote of the user message for the card header."""
    text = re.sub(r"\s+", " ", (user_text or "").strip())
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    # Feishu markdown blockquote
    return f"> {text}"


def _format_table_for_feishu(table_str: str) -> str:
    """Transform GFM markdown tables into elegant Feishu-friendly structured layouts."""
    lines = [l.strip() for l in table_str.strip().split('\n') if l.strip()]
    if len(lines) < 2:
        return table_str

    def _parse_row(row_line: str) -> list[str]:
        raw = row_line.strip()
        if raw.startswith('|'):
            raw = raw[1:]
        if raw.endswith('|'):
            raw = raw[:-1]
        return [c.strip() for c in raw.split('|')]

    headers = _parse_row(lines[0])
    data_rows = []
    for line in lines[1:]:
        if re.match(r'^[\|\s\-:]+$', line):
            continue
        cols = _parse_row(line)
        while len(cols) < len(headers):
            cols.append('')
        data_rows.append(cols[:len(headers)])

    if not data_rows:
        return table_str

    out_lines = []
    num_cols = len(headers)

    for cols in data_rows:
        primary = cols[0]
        if num_cols == 2:
            h2, v2 = headers[1], cols[1]
            if v2:
                out_lines.append(f"• **{primary}**：{v2}")
            else:
                out_lines.append(f"• **{primary}**")
        elif num_cols == 3:
            pairs = []
            for h, v in zip(headers[1:], cols[1:]):
                if v:
                    pairs.append(f"**{h}**: {v}")
            detail = " ｜ ".join(pairs)
            if detail:
                out_lines.append(f"• **{primary}** ({detail})")
            else:
                out_lines.append(f"• **{primary}**")
        else:
            # 4 columns or more: expand to item with sub-bullets
            out_lines.append(f"• **{primary}**")
            for h, v in zip(headers[1:], cols[1:]):
                if v:
                    out_lines.append(f"  - **{h}**：{v}")

    return "\n" + "\n".join(out_lines) + "\n"


def _sanitize_prose(text: str) -> str:
    """Sanitize non-code markdown for Feishu cards — preserve rich structure."""

    def _img_sub(m: re.Match) -> str:
        target = (m.group(2) or "").strip()
        if target.startswith(("http://", "https://", "img_v")):
            alt = (m.group(1) or "").strip() or "图片"
            return f"（{alt}）"
        alt = (m.group(1) or "").strip() or os.path.basename(target.replace("file://", "")) or "图片"
        return f"（{alt}）"

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

    # Protect table blocks from bullet/heading rewrites
    tables: list[str] = []

    def _park_table(m: re.Match) -> str:
        tables.append(m.group(0))
        return f"\n@@TABLE{len(tables) - 1}@@\n"

    out = _TABLE_BLOCK.sub(_park_table, text)

    out = _MD_IMAGE.sub(_img_sub, out)
    out = _FILE_MD_LINK.sub(_link_sub, out)
    out = _ANGLE_FILE.sub(lambda m: f"`{_pretty_path(m.group(1))}`", out)
    out = _BARE_FILE_URL.sub(lambda m: f"`{_pretty_path(m.group(1))}`", out)
    out = _BARE_ABS_PATH.sub(lambda m: f"`{_pretty_path(m.group(1))}`", out)

    # Keep ATX headings (# / ## / ###). CardKit 2.0 renders them better as bold cues with clear paragraph breaks.
    def _heading_sub(m: re.Match) -> str:
        level = len(m.group(1))
        title = (m.group(2) or "").strip()
        if level <= 2:
            return f"\n\n**{title}**\n"
        return f"\n\n**{title}**\n"

    out = re.sub(r"^(#{1,6})\s+(.+)$", _heading_sub, out, flags=re.MULTILINE)

    # Weird unicode bullets → `- ` (keep as unordered; do NOT force numbered)
    out = re.sub(r"^[\t ]*[•·▪◦]\s+", "- ", out, flags=re.MULTILINE)
    # Only convert list-marker star at line start (`* item`), not italic `*word*`
    out = re.sub(r"^\*\s+", "- ", out, flags=re.MULTILINE)

    # Restore and convert tables into elegant Feishu-friendly layouts
    for i, table in enumerate(tables):
        formatted_table = _format_table_for_feishu(table)
        out = out.replace(f"@@TABLE{i}@@", formatted_table)

    return out


def _iter_fence_segments(text: str) -> Iterator[Tuple[str, str]]:
    """Yield (kind, chunk) where kind is 'prose' | 'code'."""
    pos = 0
    for m in _CLOSED_FENCE.finditer(text):
        if m.start() > pos:
            yield "prose", text[pos : m.start()]
        fence = m.group(0).replace("\r\n", "\n")
        yield "code", fence
        pos = m.end()
    tail = text[pos:]
    if not tail:
        return
    open_m = _OPEN_FENCE_LINE.search(tail)
    if open_m and "```" not in tail[open_m.end() :]:
        if open_m.start() > 0:
            yield "prose", tail[: open_m.start()]
        yield "code", tail[open_m.start() :]
    else:
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
    # Collapse 3+ blank lines
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()
