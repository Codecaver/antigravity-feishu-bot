"""Shared card footer helpers."""


def create_footer():
    """Return None to eliminate outdated boilerplate footers across all cards."""
    return None


def build_input_prompt_card(title: str, body_md: str, template: str = "blue"):
    """Simple interactive prompt card (markdown renders; plain text does not)."""
    elements = [{"tag": "markdown", "content": body_md}]
    footer = create_footer()
    if footer is not None:
        elements.append(footer)
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"content": title, "tag": "plain_text"},
        },
        "elements": elements,
    }
