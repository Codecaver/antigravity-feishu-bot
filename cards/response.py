"""Card builders: response."""

import re
from utils.quota import is_quota_error
from utils.feishu_markdown import sanitize_feishu_markdown

# 飞书卡片/消息体有 ~30KB 上限，markdown 字段预留安全余量
MAX_MARKDOWN_CHARS = 25000
TRUNCATION_NOTICE = "\n\n_(回复过长已截断，完整内容请在工作区查看)_"

def build_ai_response(
    reply_text,
    choice_card_data=None,
    current_model="Default",
    current_project="默认",
    is_error=False,
    is_streaming=False,
    session_data=None,
    is_quota=None,
    reasoning_text=None,
):
    """最终 AI 回复卡：可含思考过程 + 正文；不渲染模型/项目/S1 注脚。"""
    elements = []

    # 0. Thinking / process (legacy interactive schema：用折叠文案近似)
    if reasoning_text and str(reasoning_text).strip() and not is_error:
        reason = sanitize_feishu_markdown(str(reasoning_text).strip())
        if len(reason) > 4000:
            reason = reason[:3980] + "\n\n_(思考内容过长已截断)_"
        elements.append({
            "tag": "markdown",
            "content": f"**🧠 思考过程**\n\n{reason}",
        })
        elements.append({"tag": "hr"})

    # 1. Main Text
    if reply_text:
        content = sanitize_feishu_markdown(reply_text)
        if len(content) > MAX_MARKDOWN_CHARS:
            content = content[:MAX_MARKDOWN_CHARS] + TRUNCATION_NOTICE
        if is_streaming:
            content += " ▌"

        elements.append({
            "tag": "markdown",
            "content": content
        })
        
    # 2. Interactive Options
    if choice_card_data and choice_card_data.get("options"):
        if reply_text:
            elements.append({"tag": "hr"})
            
        actions = []
        markdown_options = []
        is_long_options = any(len(opt) > 6 for opt in choice_card_data["options"])
        
        for i, opt in enumerate(choice_card_data["options"][:10]):
            prefix_match = re.match(r'^([a-zA-Z0-9\u4e00-\u9fa5\s]+)[:：.、]\s*(.*)$', opt)
            
            if prefix_match:
                prefix = prefix_match.group(1).strip()
                rest_text = prefix_match.group(2).strip()
                if len(prefix) == 1 and prefix.encode('utf-8').isalpha():
                    btn_label = f"选项 {prefix}"
                elif len(prefix) <= 6:
                    btn_label = prefix
                else:
                    btn_label = f"选项 {i+1}"
            else:
                btn_label = f"选项 {i+1}"
                rest_text = opt
            
            if not is_long_options:
                btn_label = opt[:50]
                
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": btn_label},
                "type": "default",
                "value": {"action": "user_choice", "choice": opt, "label": btn_label}
            })
            if is_long_options:
                markdown_options.append(f"- **{btn_label}**: {rest_text}")

        question_text = f"**{choice_card_data.get('question', '请选择：')}**"
        if is_long_options:
            question_text += "\n\n" + "\n".join(markdown_options)
            
        elements.append({
            "tag": "markdown",
            "content": question_text
        })
        
        # 根据选项数量动态设置布局，保证按钮等宽对齐
        layout_mode = "flow"
        if len(actions) == 2:
            layout_mode = "bisect"
        elif len(actions) == 3:
            layout_mode = "trisection"
            
        elements.append({
            "tag": "action",
            "layout": layout_mode,
            "actions": actions
        })

    # 4. Quota Actions
    if is_quota is None:
        is_quota = is_error and is_quota_error(reply_text)
    else:
        is_quota = bool(is_quota)
    if is_quota:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "action",
            "layout": "bisect",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📊 查看实时配额 (/quota)"},
                    "type": "primary",
                    "value": {"action": "user_choice", "choice": "/quota", "label": "查看实时配额"}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🔄 一键切换模型 (/model)"},
                    "type": "default",
                    "value": {"action": "user_choice", "choice": "/model", "label": "一键切换模型"}
                }
            ]
        })

    if is_quota:
        header_template = "orange"
        header_title = "⚠️ 模型额度已用尽"
    elif is_error:
        header_template = "red"
        header_title = "❌ 发生错误"
    elif is_streaming:
        header_template = "wathet"
        header_title = "✨ AI 回复中..."
    else:
        header_template = "blue"
        header_title = "✨ AI 回复"

    return {
        "config": {
            "wide_screen_mode": True,
            "enable_forward": True,
            "update_multi": True
        },
        "header": {
            "template": header_template,
            "title": {"content": header_title, "tag": "plain_text"}
        },
        "elements": elements
    }
