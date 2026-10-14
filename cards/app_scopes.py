"""Cards for Feishu app scope grant (open-platform capabilities)."""
from cards.common import create_footer


def build_scope_panel_card(packs: list[tuple[str, str, str]], current_app_id: str = "") -> dict:
    app_line = f"当前 App ID：`{current_app_id}`\n\n" if current_app_id else ""
    elements = [
        {
            "tag": "markdown",
            "content": (
                "🔐 **飞书应用能力授权**\n\n"
                f"{app_line}"
                "这是**开放平台应用权限**（不是 `/auth` 会话白名单）。\n"
                "点击下方能力包后，机器人会发起扫码确认页；请用飞书打开链接并确认。"
            ),
        },
        {"tag": "hr"},
    ]
    actions = []
    for key, title, desc in packs:
        elements.append(
            {
                "tag": "markdown",
                "content": f"**{title}** (`{key}`)\n{desc}",
            }
        )
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"申请 {title}"},
                "type": "primary" if key == "base" else "default",
                "value": {"action": "app_scope_grant", "feature": key},
            }
        )
    if actions:
        elements.append({"tag": "action", "layout": "flow", "actions": actions})
    elements.extend(
        [
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    "也可发送：`/grant base` / `/grant drive` / `/grant chat` / `/grant reaction`\n"
                    "终端等价：`python wizard.py grant <feature>`"
                ),
            },
            create_footer(),
        ]
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "飞书应用能力授权"},
            "template": "blue",
        },
        "elements": elements,
    }


def build_scope_grant_link_card(feature: str, title: str, url: str, expire_in: int = 600) -> dict:
    mins = max(1, int(round((expire_in or 600) / 60)))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"请确认授权：{title}"},
            "template": "orange",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    f"正在为能力包 **{title}** (`{feature}`) 申请开放平台权限。\n\n"
                    f"请在约 **{mins} 分钟**内用飞书打开下方链接并确认：\n\n"
                    f"{url}\n\n"
                    "确认完成后我会自动继续；也可发送 `/grant cancel` 取消本次等待。"
                ),
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开确认页"},
                        "type": "primary",
                        "multi_url": {
                            "url": url,
                            "pc_url": url,
                            "android_url": url,
                            "ios_url": url,
                        },
                    }
                ],
            },
            create_footer(),
        ],
    }


def build_scope_grant_result_card(ok: bool, title: str, detail: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": ("✅ 授权完成" if ok else "❌ 授权未完成") + f"：{title}",
            },
            "template": "green" if ok else "red",
        },
        "elements": [
            {"tag": "markdown", "content": detail},
            create_footer(),
        ],
    }
