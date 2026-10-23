"""Card builders: projects."""

import os
from config import WORKSPACE_ROOT
from logger import log

from cards.common import create_footer

# Only skip obvious non-project dirs (keep macOS home folders so pagination still appears).
_DEFAULT_SKIP_DIR_NAMES = frozenset({"venv", "downloads", "Downloads"})


def _safe_md_path(path: str) -> str:
    """Escape backticks so paths cannot break Feishu markdown code spans."""
    return (path or "").replace("`", "'")


def build_dir_browser_card(active_project_path, recent_projects=None, recent_page=1, workspace_root=None, ignored_projects=None):
    elements = []

    # 确定公共根目录
    proj_root = workspace_root if workspace_root else WORKSPACE_ROOT
    try:
        recent_page = int(recent_page or 1)
    except (TypeError, ValueError):
        recent_page = 1

    # 1. 顶部公共项目根目录展示与设置按钮 (位于开发工作区上方)
    elements.append({
        "tag": "column_set",
        "flex_mode": "none",
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [
                    {
                        "tag": "markdown",
                        "content": f"⚙️ **当前公共项目根目录**：\n`{_safe_md_path(proj_root)}`"
                    }
                ]
            },
            {
                "tag": "column",
                "width": "auto",
                "vertical_align": "bottom",
                "elements": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⚙️ 设置根目录"},
                        "type": "primary",
                        "value": {"action": "prompt_custom_workspace_root"}
                    }
                ]
            }
        ]
    })

    # 2. 当前活跃开发工作区展示
    elements.append({
        "tag": "markdown",
        "content": f"📂 **当前活跃开发工作区**：\n`{_safe_md_path(active_project_path)}`"
    })

    # 2. 新建项目动作行
    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "➕ 新建项目"},
                "type": "default",
                "value": {"action": "create_project_prompt", "parent_path": proj_root}
            }
        ]
    })
    elements.append({"tag": "hr"})

    # 3. 扫描项目根目录 proj_root 下的所有子文件夹作为项目列表
    all_projects = []
    try:
        for name in os.listdir(proj_root):
            if name.startswith('.') or name in _DEFAULT_SKIP_DIR_NAMES:
                continue
            full_path = os.path.join(proj_root, name)
            if ignored_projects and full_path in ignored_projects:
                continue
            if os.path.isdir(full_path):
                all_projects.append((name, full_path))
    except Exception as e:
        log.error(f"Failed to scan project root: {e}")

    # 4. 合并数据库中记录的 recent_projects（防止有用户在外部路径单独添加的项目）
    if recent_projects:
        known = {x[1] for x in all_projects}
        for p in recent_projects:
            if ignored_projects and p in ignored_projects:
                continue
            if not p or p == "/" or p in known:
                continue
            try:
                if os.path.isdir(p):
                    all_projects.append((os.path.basename(p) or p, p))
                    known.add(p)
            except OSError:
                continue

    # 与本机 ls/访达一致：默认字符串序（ASCII 下大写目录在前），不再 casefold 打乱
    all_projects.sort(key=lambda x: x[0])

    # 5. 内嵌分页展示全部项目列表
    if all_projects:
        items_per_page = 5
        total_items = len(all_projects)
        total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)

        page = max(1, min(recent_page, total_pages))

        elements.append({
            "tag": "markdown",
            "content": f"📁 **项目选择列表** (第 {page}/{total_pages} 页)："
        })

        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        page_items = all_projects[start_idx:end_idx]

        for name, p in page_items:
            # 高亮当前活跃项目
            is_active = (p == active_project_path)
            safe_name = (name or "").replace("*", "∗")
            name_display = f"🌟 **{safe_name} (当前活跃)**" if is_active else f"📁 **{safe_name}**"

            columns = [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "center",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"{name_display}\n`{_safe_md_path(p)}`"
                        }
                    ]
                }
            ]

            # 如果不是当前活跃项目，提供选择按钮和列表删除按钮并排在右侧
            if not is_active:
                columns.append({
                    "tag": "column",
                    "width": "auto",
                    "vertical_align": "center",
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "选择"},
                            "type": "primary",
                            "value": {"action": "select_project", "path": p}
                        }
                    ]
                })
                columns.append({
                    "tag": "column",
                    "width": "auto",
                    "vertical_align": "center",
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "删除"},
                            "type": "danger",
                            "value": {"action": "remove_project_from_list", "path": p}
                        }
                    ]
                })
            else:
                columns.append({
                    "tag": "column",
                    "width": "auto",
                    "vertical_align": "center",
                    "elements": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 已选"},
                            "type": "default",
                            "value": {"action": "already_active"}
                        }
                    ]
                })

            elements.append({
                "tag": "column_set",
                "flex_mode": "none",
                "columns": columns
            })

        # 分页控制
        page_actions = []
        if page > 1:
            page_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "◀️ 上一页"},
                "type": "default",
                "value": {"action": "browse_recent_page", "page": page - 1, "current_path": active_project_path}
            })
        if page < total_pages:
            page_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "下一页 ▶️"},
                "type": "default",
                "value": {"action": "browse_recent_page", "page": page + 1, "current_path": active_project_path}
            })

        if page_actions:
            elements.append({
                "tag": "action",
                "actions": page_actions
            })
    else:
        elements.append({
            "tag": "markdown",
            "content": "📭 *当前没有可用的项目，请点击上方按钮新建一个项目！*"
        })

    footer = create_footer()
    if footer is not None:
        elements.append(footer)

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "📁 项目管理器", "tag": "plain_text"}
        },
        "elements": elements
    }
