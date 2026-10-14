"""Feishu app registration wizard (QR create + incremental scope grant).

Uses official lark_oapi.register_app (OAuth device flow), same mechanism as
lark-agent-bridge's registerApp.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from typing import Any, Callable, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Capability packs mapped to Feishu addons (incremental on platform default template).
FEATURE_PACKS: dict[str, dict[str, Any]] = {
    "base": {
        "title": "基础消息",
        "desc": "收发消息、机器人回复、资源下载、卡片回调、读取用户/群基础信息",
        "addons": {
            "scopes": {
                "tenant": [
                    "im:message",
                    "im:message:send_as_bot",
                    "im:message:readonly",
                    "im:resource",
                    "im:image",
                    "im:file",
                    "contact:user.base:readonly",
                    "im:chat:readonly",
                    # CardKit 2.0 原生流式打字机（与 lark-agent-bridge 同机制）
                    "cardkit:card:write",
                ]
            },
            "events": {"items": {"tenant": ["im.message.receive_v1"]}},
            "callbacks": {"items": ["card.action.trigger"]},
        },
    },
    "reaction": {
        "title": "消息表情",
        "desc": "消息表情回复（reaction）相关能力",
        "addons": {
            "scopes": {"tenant": ["im:message.reaction:read", "im:message.reaction:write"]},
            "events": {
                "items": {
                    "tenant": [
                        "im.message.reaction.created_v1",
                        "im.message.reaction.deleted_v1",
                    ]
                }
            },
        },
    },
    "chat": {
        "title": "群聊管理",
        "desc": "创建/管理群聊（im:chat）",
        "addons": {"scopes": {"tenant": ["im:chat"]}},
    },
    "drive": {
        "title": "云文档",
        "desc": "云文档读写与评论 @bot 事件",
        "addons": {
            "scopes": {"tenant": ["drive:drive"]},
            "events": {"items": {"tenant": ["drive.notice.comment_add_v1"]}},
        },
    },
}

_grant_lock = threading.Lock()
_grant_cancel: Optional[threading.Event] = None
_grant_active = False


def list_feature_packs() -> list[tuple[str, str, str]]:
    return [(key, pack["title"], pack["desc"]) for key, pack in FEATURE_PACKS.items()]


def resolve_feature_addons(feature: str) -> dict[str, Any]:
    key = (feature or "base").strip().lower()
    if key not in FEATURE_PACKS:
        known = ", ".join(FEATURE_PACKS)
        raise ValueError(f"未知能力包: {feature}（可选: {known}）")
    return FEATURE_PACKS[key]["addons"]


def _print_qr_to_terminal(url: str, expire_in: int) -> None:
    print("\n请用飞书 App 扫描以下二维码完成应用创建/授权：\n")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception as exc:
        print(f"(终端二维码渲染失败: {exc}，请直接打开下方链接)\n")
    mins = max(1, int(round((expire_in or 600) / 60)))
    print(f"\n二维码有效期：约 {mins} 分钟")
    print(f"也可直接在浏览器/飞书打开：\n{url}\n")


def upsert_env_values(updates: dict[str, str], env_path: str = ENV_PATH) -> None:
    """Create or update keys in .env while preserving unrelated lines."""
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()

    keys = set(updates)
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in keys:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out).rstrip() + "\n")


def apply_runtime_credentials(app_id: str, app_secret: str) -> None:
    """Patch process env + live module globals used by the running bot."""
    os.environ["FEISHU_APP_ID"] = app_id
    os.environ["FEISHU_APP_SECRET"] = app_secret
    os.environ["APP_ID"] = app_id
    os.environ["APP_SECRET"] = app_secret

    import config

    config.APP_ID = app_id
    config.APP_SECRET = app_secret

    try:
        import lark_oapi as lark
        import lark_client

        lark_client.api_client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .timeout(10.0)
            .build()
        )
    except Exception:
        pass


def persist_registration_result(
    result: dict[str, Any],
    *,
    set_admin_allowlist: bool = True,
) -> tuple[str, str]:
    app_id = result["client_id"]
    app_secret = result["client_secret"]
    updates = {
        "FEISHU_APP_ID": app_id,
        "FEISHU_APP_SECRET": app_secret,
    }
    open_id = (result.get("user_info") or {}).get("open_id")
    if set_admin_allowlist and open_id:
        # Optional legacy whitelist boost; primary admin still bootstraps via first p2p.
        existing = ""
        if os.path.exists(ENV_PATH):
            for line in open(ENV_PATH, encoding="utf-8"):
                if line.startswith("ALLOWED_USERS="):
                    existing = line.split("=", 1)[1].strip()
                    break
        users = [u for u in existing.split(",") if u.strip()]
        if open_id not in users:
            users.insert(0, open_id)
        updates["ALLOWED_USERS"] = ",".join(users)

    upsert_env_values(updates)
    apply_runtime_credentials(app_id, app_secret)
    return app_id, app_secret


def _register(
    *,
    on_qr_code: Callable[[dict], None],
    on_status_change: Optional[Callable[[dict], None]] = None,
    addons: Optional[dict] = None,
    create_only: Optional[bool] = None,
    app_id: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    app_preset: Optional[dict] = None,
) -> dict[str, Any]:
    import lark_oapi as lark

    register = getattr(lark, "register_app", None)
    if register is None:
        raise RuntimeError(
            "当前 lark-oapi 不支持 register_app，请升级到 >=1.7.4：pip install -U 'lark-oapi>=1.7.4'"
        )

    kwargs: dict[str, Any] = {
        "on_qr_code": on_qr_code,
        "source": "antigravity-feishu-bot",
    }
    if on_status_change is not None:
        kwargs["on_status_change"] = on_status_change
    if addons is not None:
        kwargs["addons"] = addons
    if create_only is not None:
        kwargs["create_only"] = create_only
    if app_id is not None:
        kwargs["app_id"] = app_id
    if cancel_event is not None:
        kwargs["cancel_event"] = cancel_event
    if app_preset is not None:
        kwargs["app_preset"] = app_preset

    try:
        return register(**kwargs)
    except TypeError as exc:
        # Older wheels may lack addons/create_only/app_id — fail clearly.
        raise RuntimeError(
            f"register_app 参数不受当前 SDK 支持 ({exc})。请升级: pip install -U 'lark-oapi>=1.7.4'"
        ) from exc


def run_setup_wizard(*, terminal_qr: bool = True) -> dict[str, Any]:
    """Create a new Feishu app via QR and persist credentials to .env."""
    print("\n未检测到飞书应用凭据，进入扫码创建向导。\n")

    def on_status(info: dict) -> None:
        status = info.get("status")
        if status == "domain_switched":
            print("识别到国际版租户，已切换到 larksuite.com 域名。")
        elif status == "slow_down":
            print("轮询过快，已自动降速。")

    result = _register(
        on_qr_code=lambda info: _print_qr_to_terminal(info.get("url", ""), info.get("expire_in", 600))
        if terminal_qr
        else None,
        on_status_change=on_status,
        addons=resolve_feature_addons("base"),
        create_only=True,
        app_preset={
            "name": "{user}的 Antigravity Bot",
            "desc": "由 antigravity-feishu-bot 扫码创建，驱动本机 agy CLI",
        },
    )
    app_id, _ = persist_registration_result(result)
    open_id = (result.get("user_info") or {}).get("open_id")
    print("\n✓ 应用创建成功")
    print(f"  App ID:  {app_id}")
    if open_id:
        print(f"  扫码用户 open_id: {open_id}（已写入 ALLOWED_USERS）")
    print(f"  凭据已写入: {ENV_PATH}")
    print_post_create_reminder()
    return result


def print_post_create_reminder() -> None:
    print(
        "\n请到飞书开放平台确认（扫码确认页通常已预填，仍建议核对并发布版本）：\n"
        "  权限: im:message / im:message:send_as_bot / im:resource / im:image / im:file\n"
        "  事件: im.message.receive_v1（接收方式选 WebSocket 长连接）\n"
        "  回调: card.action.trigger\n"
        "  版本管理 → 创建版本并发布；可用范围包含你自己\n"
    )


def run_grant_wizard(
    feature: str,
    *,
    app_id: Optional[str] = None,
    on_qr_code: Optional[Callable[[dict], None]] = None,
    on_status_change: Optional[Callable[[dict], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict[str, Any]:
    """Incrementally request scopes/events for an existing app via QR confirm page."""
    import config

    target_app_id = (app_id or config.APP_ID or "").strip()
    if not target_app_id:
        raise RuntimeError("当前没有 FEISHU_APP_ID，请先执行 setup 扫码创建应用")

    addons = resolve_feature_addons(feature)
    pack = FEATURE_PACKS[feature.strip().lower()]
    print(f"\n正在申请能力包「{pack['title']}」({feature})，请扫码确认…\n")

    def _default_status(info: dict) -> None:
        if info.get("status") == "domain_switched":
            print("识别到国际版租户，已切换到 larksuite.com 域名。")

    result = _register(
        on_qr_code=on_qr_code
        or (lambda info: _print_qr_to_terminal(info.get("url", ""), info.get("expire_in", 600))),
        on_status_change=on_status_change or _default_status,
        addons=addons,
        create_only=False,
        app_id=target_app_id,
        cancel_event=cancel_event,
    )
    persist_registration_result(result, set_admin_allowlist=False)
    print(f"\n✓ 能力包「{pack['title']}」授权流程完成（如控制台要求发布版本，请再发一版）\n")
    return result


def start_grant_in_background(
    feature: str,
    *,
    on_qr_code: Callable[[dict], None],
    on_done: Callable[[Optional[dict], Optional[BaseException]], None],
    on_status_change: Optional[Callable[[dict], None]] = None,
) -> bool:
    """Start one in-process grant poller. Returns False if another grant is running."""
    global _grant_cancel, _grant_active
    if not _grant_lock.acquire(blocking=False):
        return False
    if _grant_active:
        _grant_lock.release()
        return False

    cancel = threading.Event()
    _grant_cancel = cancel
    _grant_active = True

    def worker() -> None:
        global _grant_active, _grant_cancel
        err: Optional[BaseException] = None
        result: Optional[dict] = None
        try:
            result = run_grant_wizard(
                feature,
                on_qr_code=on_qr_code,
                on_status_change=on_status_change,
                cancel_event=cancel,
            )
        except BaseException as exc:  # noqa: BLE001 — surface to chat
            err = exc
        finally:
            _grant_active = False
            _grant_cancel = None
            _grant_lock.release()
            try:
                on_done(result, err)
            except Exception:
                pass

    threading.Thread(target=worker, name=f"feishu-grant-{feature}", daemon=True).start()
    return True


def cancel_active_grant() -> bool:
    global _grant_cancel
    if _grant_cancel is None:
        return False
    _grant_cancel.set()
    return True


def credentials_present() -> bool:
    import config

    return bool((config.APP_ID or "").strip() and (config.APP_SECRET or "").strip())


def bootstrap_credentials() -> None:
    """Called before WS start: run terminal QR setup when credentials are missing."""
    if credentials_present():
        return
    if not sys.stdin.isatty():
        print(
            "❌ 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET。\n"
            "   非交互环境无法扫码。请在终端执行：\n"
            "   python wizard.py setup\n"
            "   或手动写入 .env 后重启。",
            file=sys.stderr,
        )
        sys.exit(2)
    run_setup_wizard(terminal_qr=True)


def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Feishu app QR setup / scope grant")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="扫码创建飞书应用并写入 .env")
    g = sub.add_parser("grant", help="为已有应用增量申请能力包")
    g.add_argument(
        "feature",
        nargs="?",
        default="base",
        help="能力包: " + ", ".join(FEATURE_PACKS),
    )
    sub.add_parser("list", help="列出可用能力包")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        for key, title, desc in list_feature_packs():
            print(f"{key:10}  {title}  — {desc}")
        return 0
    if args.cmd == "setup":
        run_setup_wizard()
        return 0
    if args.cmd == "grant":
        run_grant_wizard(args.feature)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
