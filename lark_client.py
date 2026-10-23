import json
import os
import re
import asyncio
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest, ReplyMessageRequestBody, PatchMessageRequest,
    PatchMessageRequestBody, GetMessageResourceRequest, CreateMessageRequest,
    CreateMessageRequestBody, CreateMessageReactionRequest, GetChatRequest,
    CreateMessageReactionRequestBody, Emoji, DeleteMessageReactionRequest,
    CreateFileRequest, CreateFileRequestBody
)
from lark_oapi.api.contact.v3 import GetUserRequest
from config import APP_ID, APP_SECRET
from logger import log
from utils import with_retry

api_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).timeout(10.0).build()

@with_retry()
def set_emoji_sdk(message_id, emoji_type):
    try:
        req = CreateMessageReactionRequest.builder() \
            .message_id(message_id) \
            .request_body(CreateMessageReactionRequestBody.builder() \
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build()) \
                .build()) \
            .build()
        resp = api_client.im.v1.message_reaction.create(req)
        if resp.code == 0:
            return json.loads(resp.raw.content).get("data", {}).get("reaction_id")
        else:
            log.error(f"[set_emoji_sdk] Failed: {resp.msg}")
            return None
    except Exception as e:
        log.error(f"[set_emoji_sdk] Error: {e}")
        return None

@with_retry()
def delete_emoji_sdk(message_id, reaction_id):
    if not reaction_id:
        return
    try:
        req = DeleteMessageReactionRequest.builder() \
            .message_id(message_id) \
            .reaction_id(reaction_id) \
            .build()
        resp = api_client.im.v1.message_reaction.delete(req)
        if resp.code != 0:
            log.error(f"[delete_emoji_sdk] Failed: {resp.msg}")
    except Exception as e:
        log.error(f"[delete_emoji_sdk] Error: {e}")


def split_text_into_chunks(text, max_chunk_size=20000):
    if not text or len(text) <= max_chunk_size:
        return [text]
    
    chunks = []
    current_chunk = []
    current_length = 0
    
    lines = text.split("\n")
    for line in lines:
        line_len = len(line) + 1
        if current_length + line_len > max_chunk_size and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_length = line_len
        else:
            current_chunk.append(line)
            current_length += line_len
            
    if current_chunk:
        chunks.append("\n".join(current_chunk))
        
    return chunks


@with_retry(max_retries=1)
def send_reply_sdk(message_id, reply_text):
    if not reply_text:
        return
        
    max_text_chars = 20000
    if len(reply_text) <= max_text_chars:
        chunks = [reply_text]
    else:
        raw_chunks = split_text_into_chunks(reply_text, max_text_chars)
        total = len(raw_chunks)
        chunks = [f"📄 **[第 {idx+1}/{total} 页]**\n\n{c}" for idx, c in enumerate(raw_chunks)]

    for chunk in chunks:
        req = ReplyMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(ReplyMessageRequestBody.builder() \
                .msg_type("text") \
                .content(json.dumps({"text": chunk})) \
                .build()) \
            .build()
        resp = api_client.im.v1.message.reply(req)
        if resp.code != 0:
            log.error(f"[send_reply_sdk] Failed: {resp.msg}")

@with_retry(max_retries=1)
def send_post_reply_sdk(message_id, post_content):
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder() \
            .msg_type("post") \
            .content(json.dumps(post_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        log.error(f"[send_post_reply_sdk] Failed: {resp.msg}")
        return None
    try:
        return json.loads(resp.raw.content).get("data", {}).get("message_id")
    except:
        return None

@with_retry(max_retries=1)
def send_post_to_chat_sdk(chat_id, post_content):
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("post") \
            .content(json.dumps(post_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_post_to_chat_sdk] Failed: {resp.msg}")
        return None
    try:
        return json.loads(resp.raw.content).get("data", {}).get("message_id")
    except:
        return None

def _normalize_feishu_list_markdown(content: str) -> str:
    """把手写圆点改成飞书 markdown 原生列表语法（`- ` / 已有 `1.` 不动）。"""
    if not content:
        return content
    # • · ▪ ◦ → `- `
    out = re.sub(r"(?m)^([\t ]*)[•·▪◦]\s+", r"\1- ", content)
    # 非列表行后紧贴 `- `/`1.` 时补空行，否则飞书常当成普通文本
    out = re.sub(
        r"(?m)^(?![\t ]*(?:- |\d+\. ))(.+\S)\n(?=[\t ]*(?:- |\d+\. ))",
        r"\1\n\n",
        out,
    )
    return out


def _normalize_card_style(card_content):
    """Single choke-point for interactive card style (Card 1.0 + CardKit 2.0).

    All interactive egress MUST go through send_interactive_card_sdk /
    patch_interactive_card_sdk / send_card_to_chat_sdk, or callback helper
    that calls this function. Do not call im.v1.message.create with raw card JSON.

    1. Filter out None elements from create_footer() removals.
    2. Strip any legacy 'Powered by Antigravity' notes.
    3. Remove top-level colored header bars (keep collapsible_panel headers).
    4. Normalize handwritten bullets into Feishu native markdown lists.
    """
    if not isinstance(card_content, dict):
        return card_content
    card = dict(card_content)

    # Card 1.0: elements[]; CardKit 2.0: body.elements[]
    body = card.get("body")
    if isinstance(body, dict) and isinstance(body.get("elements"), list):
        elems_owner = dict(body)
        elems_key = "body"
        raw_elems = list(elems_owner.get("elements") or [])
    else:
        elems_owner = card
        elems_key = "elements"
        raw_elems = list(card.get("elements") or []) if isinstance(card.get("elements"), list) else []

    clean_elems = []
    for e in raw_elems:
        if e is None:
            continue
        if isinstance(e, dict) and e.get("tag") == "note":
            sub_elems = e.get("elements", [])
            if any("Powered by Antigravity" in str(sub.get("content", "")) for sub in sub_elems if isinstance(sub, dict)):
                continue
        # 手写 •/· 子弹不成飞书原生列表；统一成 markdown `- ` / 保留已有 `1.`
        if isinstance(e, dict) and e.get("tag") == "markdown" and isinstance(e.get("content"), str):
            e = dict(e)
            e["content"] = _normalize_feishu_list_markdown(e["content"])
        clean_elems.append(e)

    # 只剥卡片顶栏 header（带 template 的彩条）；不动折叠面板内部 header
    header = card.pop("header", None)
    if header and isinstance(header, dict):
        title_obj = header.get("title", {})
        title_text = ""
        if isinstance(title_obj, dict):
            title_text = title_obj.get("content", "").strip()
        elif isinstance(title_obj, str):
            title_text = title_obj.strip()

        if title_text:
            first_content = ""
            if clean_elems and isinstance(clean_elems[0], dict) and clean_elems[0].get("tag") == "markdown":
                first_content = clean_elems[0].get("content", "")

            key_words = [w for w in re.split(r'[\s·\-_():：]+', title_text) if len(w) >= 2]
            has_title = any(w in first_content for w in key_words) if key_words else (title_text in first_content)
            if not has_title:
                clean_elems.insert(0, {
                    "tag": "markdown",
                    "content": f"**{title_text}**"
                })

    if elems_key == "body":
        elems_owner["elements"] = clean_elems
        card["body"] = elems_owner
    else:
        card["elements"] = clean_elems
    return card


# Public alias — plugins / handlers should import this name when needed.
normalize_card_style = _normalize_card_style


@with_retry(max_retries=1)
def send_interactive_card_sdk(message_id, card_content):
    card_content = _normalize_card_style(card_content)
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder() \
            .msg_type("interactive") \
            .content(json.dumps(card_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        log.error(f"[send_interactive_card_sdk] Failed: {resp.msg}")
        return None
    try:
        return json.loads(resp.raw.content).get("data", {}).get("message_id")
    except:
        return None

@with_retry(max_retries=4, initial_delay=0.6)
def patch_interactive_card_sdk(message_id, card_content):
    card_content = _normalize_card_style(card_content)
    req = PatchMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(PatchMessageRequestBody.builder() \
            .content(json.dumps(card_content)) \
            .build()) \
        .build()
    resp = api_client.im.v1.message.patch(req)
    if resp.code != 0:
        log.error(f"[patch_interactive_card_sdk] Failed (code {resp.code}): {resp.msg}")
        raise Exception(f"Feishu Patch API error code {resp.code}: {resp.msg}")
    return True

@with_retry(max_retries=5, initial_delay=2.0)
def download_message_resource_sdk(message_id, file_key, resource_type, output_path):
    """
    Downloads a message resource (image, file, audio, media) using the official SDK.
    Writes to a temp file first, then atomically renames on success so a failed
    download never leaves a partial file at output_path.
    """
    import tempfile
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    req = GetMessageResourceRequest.builder() \
        .message_id(message_id) \
        .file_key(file_key) \
        .type(resource_type) \
        .build()

    resp = api_client.im.v1.message_resource.get(req)

    if resp.code != 0:
        log.error(f"[download_message_resource_sdk] Failed: {resp.msg}")
        return False

    tmp_fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=".dl_tmp_")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            while True:
                chunk = resp.file.read(8192)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp_path, output_path)
        return True
    except Exception as e:
        log.error(f"[download_message_resource_sdk] Error saving file: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Outbound messages to a chat (used by auth flows / admin notifications)
# ---------------------------------------------------------------------------

@with_retry(max_retries=1)
def send_card_to_chat_sdk(chat_id, card_content):
    """Send an interactive card as a NEW message to a chat (not a reply)."""
    card_content = _normalize_card_style(card_content)
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card_content))
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_card_to_chat_sdk] Failed: {resp.msg}")
        return False
    return True


@with_retry(max_retries=1)
def send_text_to_chat_sdk(chat_id, text):
    """Send a plain text message as a NEW message to a chat."""
    if text and len(text) > 28000:
        text = text[:28000] + "\n\n（内容过长已截断）"
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_text_to_chat_sdk] Failed: {resp.msg}")
        return False
    return True


def get_chat_name_sdk(chat_id):
    """Resolve a group chat display name (empty on failure)."""
    try:
        req = GetChatRequest.builder().chat_id(chat_id).build()
        resp = api_client.im.v1.chat.get(req)
        # GetChatResponse.data 本身就是群信息对象，name 为其直接字段
        if resp.code == 0 and resp.data:
            return resp.data.name or ""
        log.error(f"[get_chat_name_sdk] Failed: {resp.msg}")
    except Exception as e:
        log.error(f"[get_chat_name_sdk] Error: {e}")
    return ""


def get_user_name_sdk(open_id):
    """Resolve a user display name by open_id (empty on failure)."""
    if not open_id:
        return ""
    try:
        req = GetUserRequest.builder().user_id_type("open_id").user_id(open_id).build()
        resp = api_client.contact.v3.user.get(req)
        if resp.code == 0 and resp.data and resp.data.user:
            return resp.data.user.name or ""
        log.error(f"[get_user_name_sdk] Failed: {resp.msg}")
    except Exception as e:
        log.error(f"[get_user_name_sdk] Error: {e}")
    return ""


async def send_card_to_chat_async(chat_id, card_content):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: send_card_to_chat_sdk(chat_id, card_content))


async def send_text_to_chat_async(chat_id, text):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: send_text_to_chat_sdk(chat_id, text))


async def get_chat_name_async(chat_id):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_chat_name_sdk(chat_id))


async def get_user_name_async(open_id):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_user_name_sdk(open_id))


# ---------------------------------------------------------------------------
# File & Voice transfer helpers using Lark OpenAPI
# ---------------------------------------------------------------------------

@with_retry(max_retries=2)
def upload_file_sdk(file_path, file_type=None, duration_ms=None):
    """上传本地文件到飞书，返回 file_key（失败返回 None）。
    支持 file_type: 'stream', 'pdf', 'doc', 'xls', 'ppt', 'mp4', 'opus'
    """
    if not os.path.exists(file_path):
        log.error(f"[upload_file_sdk] File not found: {file_path}")
        return None

    if not file_type:
        ext = os.path.splitext(file_path)[1].lower()
        ext_map = {
            ".opus": "opus",
            ".mp4": "mp4",
            ".pdf": "pdf",
            ".doc": "doc", ".docx": "doc",
            ".xls": "xls", ".xlsx": "xls",
            ".ppt": "ppt", ".pptx": "ppt"
        }
        file_type = ext_map.get(ext, "stream")

    try:
        with open(file_path, "rb") as f:
            builder = CreateFileRequestBody.builder() \
                .file_type(file_type) \
                .file_name(os.path.basename(file_path)) \
                .file(f)
            if duration_ms is not None:
                builder.duration(int(duration_ms))
            req = CreateFileRequest.builder().request_body(builder.build()).build()
            resp = api_client.im.v1.file.create(req)

        if resp.code == 0:
            data = json.loads(resp.raw.content).get("data", {})
            return data.get("file_key")
        log.error(f"[upload_file_sdk] Failed (code {resp.code}): {resp.msg}")
        return None
    except Exception as e:
        log.error(f"[upload_file_sdk] Error: {e}")
        return None


@with_retry(max_retries=1)
def send_file_to_chat_sdk(chat_id, file_key):
    """向会话发送原生文件消息。"""
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("file")
            .content(json.dumps({"file_key": file_key}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_file_to_chat_sdk] Failed: {resp.msg}")
        return False
    return True


@with_retry(max_retries=1)
def reply_file_sdk(message_id, file_key):
    """回复消息：发送原生文件卡片。"""
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder()
            .msg_type("file")
            .content(json.dumps({"file_key": file_key}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        log.error(f"[reply_file_sdk] Failed: {resp.msg}")
        return False
    return True


@with_retry(max_retries=1)
def send_voice_to_chat_sdk(chat_id, file_key):
    """向会话发送原生语音消息（可直接在聊天窗口播放的语音条）。"""
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("audio")
            .content(json.dumps({"file_key": file_key}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.create(req)
    if resp.code != 0:
        log.error(f"[send_voice_to_chat_sdk] Failed: {resp.msg}")
        return False
    return True


@with_retry(max_retries=1)
def reply_voice_sdk(message_id, file_key):
    """回复消息：发送原生语音条消息（带波形与时长，点击直接播放）。"""
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder()
            .msg_type("audio")
            .content(json.dumps({"file_key": file_key}))
            .build()) \
        .build()
    resp = api_client.im.v1.message.reply(req)
    if resp.code != 0:
        log.error(f"[reply_voice_sdk] Failed: {resp.msg}")
        return False
    return True


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"}
# abs_path -> deadline; shared dedupe across send_to_feishu / multimodal（图+文件）
_RECENTLY_SENT_MEDIA = {}
_RECENTLY_SENT_LOCK = __import__("threading").Lock()


def _is_image_path(file_path: str) -> bool:
    return os.path.splitext((file_path or "").lower())[1] in _IMAGE_EXTS


def mark_media_sent(file_path: str, ttl_sec: float = 180.0) -> None:
    try:
        import time
        with _RECENTLY_SENT_LOCK:
            _RECENTLY_SENT_MEDIA[os.path.abspath(file_path)] = time.time() + ttl_sec
    except Exception:
        pass


def was_media_recently_sent(file_path: str) -> bool:
    try:
        import time
        abs_p = os.path.abspath(file_path)
        with _RECENTLY_SENT_LOCK:
            exp = _RECENTLY_SENT_MEDIA.get(abs_p)
            if exp is None:
                return False
            if time.time() > exp:
                _RECENTLY_SENT_MEDIA.pop(abs_p, None)
                return False
            return True
    except Exception:
        return False


# 兼容旧调用名
def mark_image_sent(file_path: str, ttl_sec: float = 180.0) -> None:
    mark_media_sent(file_path, ttl_sec=ttl_sec)


def was_image_recently_sent(file_path: str) -> bool:
    return was_media_recently_sent(file_path)


def send_local_image_to_chat(chat_id, file_path) -> bool:
    """Upload local image and send as Feishu native image message (not file)."""
    if not chat_id or not file_path or not os.path.isfile(file_path):
        return False
    if was_image_recently_sent(file_path):
        log.info(f"[send_local_image_to_chat] skip duplicate {file_path}")
        return True
    try:
        with open(file_path, "rb") as f:
            req = lark.api.im.v1.CreateImageRequest.builder().request_body(
                lark.api.im.v1.CreateImageRequestBody.builder()
                .image_type("message")
                .image(f)
                .build()
            ).build()
            resp = api_client.im.v1.image.create(req)
        if resp.code != 0:
            log.error(f"[send_local_image_to_chat] upload failed: {resp.msg}")
            return False
        img_key = json.loads(resp.raw.content).get("data", {}).get("image_key")
        if not img_key:
            return False
        msg_req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("image")
            .content(json.dumps({"image_key": img_key}))
            .build()
        ).build()
        msg_resp = api_client.im.v1.message.create(msg_req)
        if msg_resp.code != 0:
            log.error(f"[send_local_image_to_chat] send failed: {msg_resp.msg}")
            return False
        mark_image_sent(file_path)
        log.info(f"[send_local_image_to_chat] sent image_key={img_key} chat={chat_id}")
        return True
    except Exception as e:
        log.error(f"[send_local_image_to_chat] error: {e}")
        return False


def send_local_file_to_chat(chat_id, file_path, file_type=None, caption=None, duration_ms=None):
    """组合工具：直接上传本地文件并通过 Lark API 发送到指定会话。

    图片一律按原生 image 发送（不发 file、不另发 caption 文本），避免刷屏。
    同一路径短时间内只发一次（与 multimodal 共用去重）。
    """
    if _is_image_path(file_path):
        return send_local_image_to_chat(chat_id, file_path)

    if was_media_recently_sent(file_path):
        log.info(f"[send_local_file_to_chat] skip duplicate file {file_path}")
        return True

    file_key = upload_file_sdk(file_path, file_type=file_type, duration_ms=duration_ms)
    if not file_key:
        return False
    is_audio = (file_type == "opus") or file_path.lower().endswith(".opus")
    ok = send_voice_to_chat_sdk(chat_id, file_key) if is_audio else send_file_to_chat_sdk(chat_id, file_key)
    if ok:
        mark_media_sent(file_path)
        # 文件不另发 caption，避免「说明文字 + 文件」再叠一条；说明写进主卡片即可
    return ok


def send_local_file_as_reply(message_id, file_path, file_type=None, caption=None, duration_ms=None):
    """组合工具：直接上传本地文件并通过 Lark API 回复指定消息。图片走 image，不发 file。"""
    if _is_image_path(file_path):
        # reply path: upload image then reply with image msg
        try:
            with open(file_path, "rb") as f:
                req = lark.api.im.v1.CreateImageRequest.builder().request_body(
                    lark.api.im.v1.CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(f)
                    .build()
                ).build()
                resp = api_client.im.v1.image.create(req)
            if resp.code != 0:
                return False
            img_key = json.loads(resp.raw.content).get("data", {}).get("image_key")
            if not img_key:
                return False
            msg_req = ReplyMessageRequest.builder().message_id(message_id).request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("image")
                .content(json.dumps({"image_key": img_key}))
                .build()
            ).build()
            msg_resp = api_client.im.v1.message.reply(msg_req)
            if msg_resp.code == 0:
                mark_image_sent(file_path)
                return True
            return False
        except Exception as e:
            log.error(f"[send_local_file_as_reply] image error: {e}")
            return False

    if was_media_recently_sent(file_path):
        log.info(f"[send_local_file_as_reply] skip duplicate file {file_path}")
        return True

    file_key = upload_file_sdk(file_path, file_type=file_type, duration_ms=duration_ms)
    if not file_key:
        return False
    is_audio = (file_type == "opus") or file_path.lower().endswith(".opus")
    ok = reply_voice_sdk(message_id, file_key) if is_audio else reply_file_sdk(message_id, file_key)
    if ok:
        mark_media_sent(file_path)
    return ok

