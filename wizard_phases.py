"""Two-phase Feishu register_app helper for remote/chat-driven setup.

phase begin  — print JSON {url, expire_in, state_file} and exit (state persisted)
phase wait   — poll until user confirms, then write .env
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

from wizard import (
    ENV_PATH,
    persist_registration_result,
    print_post_create_reminder,
    resolve_feature_addons,
)

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".register_app_state.json")
ENDPOINT = "/oauth/v1/app/registration"
SDK_NAME = "python-sdk"


def _encode_addons(addons: dict) -> str:
    import base64
    import gzip

    payload = json.dumps(addons, ensure_ascii=False, separators=(",", ":"))
    compressed = gzip.compress(payload.encode("utf-8"), mtime=0)
    return base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")


def _build_qr_url(uri: str, *, addons: dict | None, create_only: bool, app_preset: dict | None) -> str:
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    params["from"] = ["sdk"]
    params["tp"] = ["sdk"]
    params["source"] = [f"{SDK_NAME}/antigravity-feishu-bot"]
    if app_preset:
        if app_preset.get("name"):
            params["name"] = [app_preset["name"]]
        if app_preset.get("desc"):
            params["desc"] = [app_preset["desc"]]
    if addons is not None:
        params["addons"] = [_encode_addons(addons)]
    if create_only:
        params["createOnly"] = ["true"]
    flat = []
    for k, vals in params.items():
        for v in vals:
            flat.append((k, v))
    return urlunparse(parsed._replace(query=urlencode(flat)))


def _post(base_url: str, data: dict) -> dict:
    resp = requests.post(
        base_url + ENDPOINT,
        data=urlencode(data),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def phase_begin(domain: str = "https://accounts.feishu.cn") -> int:
    init_res = _post(domain, {"action": "init"})
    methods = init_res.get("supported_auth_methods") or []
    if "client_secret" not in methods:
        print(json.dumps({"error": "unsupported_auth_method", "detail": methods}), file=sys.stderr)
        return 1

    begin_res = _post(
        domain,
        {
            "action": "begin",
            "archetype": "PersonalAgent",
            "auth_method": "client_secret",
            "request_user_info": "open_id",
        },
    )
    addons = resolve_feature_addons("base")
    app_preset = {
        "name": "{user}的 Antigravity Bot",
        "desc": "由 antigravity-feishu-bot 扫码创建，驱动本机 agy CLI",
    }
    url = _build_qr_url(
        begin_res["verification_uri_complete"],
        addons=addons,
        create_only=True,
        app_preset=app_preset,
    )
    expire_in = int(begin_res.get("expires_in") or 600)
    state = {
        "domain": domain,
        "lark_domain": "https://accounts.larksuite.com",
        "device_code": begin_res["device_code"],
        "interval": int(begin_res.get("interval") or 5),
        "expire_in": expire_in,
        "deadline": time.time() + expire_in,
        "url": url,
        "domain_switched": False,
    }
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    print(json.dumps({"ok": True, "url": url, "expire_in": expire_in, "state_file": STATE_PATH}, ensure_ascii=False))
    return 0


def phase_wait() -> int:
    if not os.path.exists(STATE_PATH):
        print(json.dumps({"error": "no_state", "hint": "run: python wizard_phases.py begin"}), file=sys.stderr)
        return 1
    with open(STATE_PATH, encoding="utf-8") as fh:
        state = json.load(fh)

    domain = state["domain"]
    lark_domain = state.get("lark_domain") or "https://accounts.larksuite.com"
    device_code = state["device_code"]
    interval = int(state.get("interval") or 5)
    deadline = float(state.get("deadline") or (time.time() + 600))
    domain_switched = bool(state.get("domain_switched"))

    print(json.dumps({"status": "polling", "url": state.get("url")}, ensure_ascii=False), flush=True)

    while time.time() < deadline:
        poll_res = _post(domain, {"action": "poll", "device_code": device_code})

        if poll_res.get("client_id") and poll_res.get("client_secret"):
            result = {
                "client_id": poll_res["client_id"],
                "client_secret": poll_res["client_secret"],
            }
            if poll_res.get("user_info"):
                result["user_info"] = poll_res["user_info"]
            app_id, _ = persist_registration_result(result)
            try:
                os.remove(STATE_PATH)
            except OSError:
                pass
            print(
                json.dumps(
                    {
                        "ok": True,
                        "app_id": app_id,
                        "open_id": (result.get("user_info") or {}).get("open_id"),
                        "env": ENV_PATH,
                    },
                    ensure_ascii=False,
                )
            )
            print_post_create_reminder()
            return 0

        user_info = poll_res.get("user_info") or {}
        if user_info.get("tenant_brand") == "lark" and not domain_switched:
            domain = lark_domain
            domain_switched = True
            print(json.dumps({"status": "domain_switched"}), flush=True)
            continue

        err = poll_res.get("error", "")
        if err == "authorization_pending":
            time.sleep(interval)
            continue
        if err == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        if err == "access_denied":
            print(json.dumps({"error": "access_denied", "detail": poll_res}), file=sys.stderr)
            return 2
        if err:
            print(json.dumps({"error": err, "detail": poll_res}), file=sys.stderr)
            return 3
        time.sleep(interval)

    print(json.dumps({"error": "expired_token"}), file=sys.stderr)
    return 4


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("phase", choices=["begin", "wait"])
    args = p.parse_args(argv)
    if args.phase == "begin":
        return phase_begin()
    return phase_wait()


if __name__ == "__main__":
    raise SystemExit(main())
