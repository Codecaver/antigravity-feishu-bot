"""Markdown to Feishu Post (rich text) converter."""

import re
from typing import Dict, Any, List


def markdown_to_feishu_post(md_text: str, title: str = "") -> Dict[str, Any]:
    """Convert standard Markdown into Feishu post (富文本) structure."""
    if not md_text:
        return {"zh_cn": {"title": title, "content": []}}

    lines = md_text.split("\n")
    content_paragraphs: List[List[Dict[str, Any]]] = []

    in_code_block = False
    code_lines: List[str] = []

    for line in lines:
        stripped = line.strip()

        # Code block fence
        if stripped.startswith("```"):
            if in_code_block:
                code_text = "\n".join(code_lines)
                if code_text:
                    content_paragraphs.append([{"tag": "text", "text": code_text}])
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Horizontal rule
        if stripped in ["---", "***", "___"]:
            content_paragraphs.append([{"tag": "text", "text": "───────────────"}])
            continue

        # Heading: #, ##, ###, ####
        m_head = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m_head:
            head_text = m_head.group(2).strip()
            content_paragraphs.append([{"tag": "text", "text": head_text, "style": ["bold"]}])
            continue

        # Bullet list or ordered list items
        prefix = ""
        m_bullet = re.match(r"^([ \t]*)([-*+]|\d+\.)\s+(.*)$", line)
        if m_bullet:
            indent = "  " if len(m_bullet.group(1)) >= 2 else ""
            marker = m_bullet.group(2)
            bullet_sym = "• " if marker in ["-", "*", "+"] else f"{marker} "
            prefix = f"{indent}{bullet_sym}"
            line = m_bullet.group(3)

        # Inline tokens parser: **bold**, `code`, [text](url)
        # Regex captures: **...**, `...`, [text](url)
        token_pattern = re.compile(r"(\*\*.*?\*\*|`[^`]+`|\[.*?\]\(.*?\))")
        parts = token_pattern.split(line)

        row_elements: List[Dict[str, Any]] = []
        if prefix:
            row_elements.append({"tag": "text", "text": prefix})

        for part in parts:
            if not part:
                continue
            # Bold
            if part.startswith("**") and part.endswith("**") and len(part) >= 4:
                row_elements.append({"tag": "text", "text": part[2:-2], "style": ["bold"]})
            # Inline code
            elif part.startswith("`") and part.endswith("`") and len(part) >= 2:
                row_elements.append({"tag": "text", "text": f" {part[1:-1]} "})
            # Markdown link
            elif part.startswith("[") and "](" in part and part.endswith(")"):
                m_link = re.match(r"^\[(.*?)\]\((.*?)\)$", part)
                if m_link:
                    row_elements.append({"tag": "a", "text": m_link.group(1), "href": m_link.group(2)})
                else:
                    row_elements.append({"tag": "text", "text": part})
            else:
                row_elements.append({"tag": "text", "text": part})

        if row_elements:
            content_paragraphs.append(row_elements)
        else:
            content_paragraphs.append([{"tag": "text", "text": ""}])

    # Drain leftover code block if unclosed
    if in_code_block and code_lines:
        content_paragraphs.append([{"tag": "text", "text": "\n".join(code_lines)}])

    return {
        "zh_cn": {
            "title": title,
            "content": content_paragraphs,
        }
    }
