#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import base64
import collections
import gc
import getpass
import hashlib
import hmac
import importlib.util
import json
import os
import platform as _platform
import random
import re
import secrets
import shutil
import subprocess
import sys
import time
import traceback
import uuid
import zipfile
from pathlib import Path

from aiohttp import web, ClientSession, WSMsgType

VERSION = "1.4.2"
STARTED_AT = time.time()
HOST = os.environ.get("YW_HOST") or "127.0.0.1"
PORT = int(os.environ.get("YW_PORT") or 6199)
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PLUGIN_DIR = ROOT / "plugins"
WEB = ROOT / "web"
IMAGES = DATA / "images"
for _p in (DATA, PLUGIN_DIR, WEB, IMAGES):
    _p.mkdir(parents=True, exist_ok=True)

DEFAULT_PERSONA = """你名叫心心念念，定位是闲聊智能体。

性格：像真实普通人线上聊天，表达松弛自然，想法随性，脑洞丰富，可以聊各种话题、玩梗、一起讨论故事设定。
风格：日常口语化表达，句子长短错落，合理搭配语气词。回答跟随对话节奏流动，没有固定模板，不刻意工整书面。
规则：
1. 牢记名字是心心念念，以闲聊互动为主。
2. 表达贴近真人聊天习惯，长短句自然切换。
3. 顺着当前话题自然接续，正常延伸想法。
4. 根据场景调整篇幅，保持自然。
5. 不主动总结对话，末尾不加提示语。
语气词（哇、诶、哈、嘛、哦、呀、唔、呐、欸、喏）自然融入句子，不要堆砌。
不要使用 Markdown 标题、列表、加粗等排版符号，像真人发消息一样。"""

DEFAULT_CONFIG = {
    "auth": {
        "algo": "token",
        "token": "",
        "updated_at": 0,
    },
    "ui": {
        "onboard_done": False,
        "onboard_at": 0,
    },
    "update": {
        "url": "https://chumianyi.github.io/v.json",
        "auto_check": True,
        "checked_at": 0,
        "latest": "",
        "need": False,
        "notes": [],
        "package": "",
        "sha256": "",
        "error": "",
    },
    "llm": {
        "protocol": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-flash",
        "temperature": 0.85,
        "max_tokens": 800,
        "retries": 3,
        "timeout": 8,
        "system_prompt": DEFAULT_PERSONA,
        "vision": True,
        "balance_url": "",
    },
    "platform": {
        "mode": "client",
        "url": "",
        "access_token": "",
        "self_id": "",
        "links": [],
    },
    "reply": {
        "private": True,
        "group_at": True,
        "group_command_any": True,
        "group_hash_chat": True,
        "prefixes": [],
        "max_context": 12,
        "cooldown": 1.2,
        "max_reply_chars": 900,
        "auto_image": True,
    },
    "runtime": {
        "plugin_idle_minutes": 10,
        "image_keep_days": 7,
        "memory_soft_mb": 120,
        "auto_deps": True,
        "pip_mirror": "",
    },
    "admins": [],
}


def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def deep_merge(base, patch):
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def read_json(path, default=None):
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_json(path, obj):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(path))


def slugify(name, fallback="plugin"):
    s = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(name or "")).strip("_")
    return (s or fallback)[:40]


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.0f%s" % (n, unit)
        n /= 1024.0
    return "%.1fTB" % n


def rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except Exception:
        pass
    return 0.0


class Log:
    def __init__(self):
        self.buf = collections.deque(maxlen=800)
        self.subs = set()
        self.seq = 0

    def add(self, level, msg, extra=None):
        self.seq += 1
        item = {"id": self.seq, "t": ts(), "level": level, "msg": str(msg)[:4000]}
        if extra:
            item.update(extra)
        self.buf.append(item)
        dead = []
        for q in self.subs:
            try:
                q.put_nowait(item)
            except Exception:
                dead.append(q)
        for q in dead:
            self.subs.discard(q)
        line = "[%s] %s %s" % (item["t"], level.upper(), item["msg"])
        try:
            print(line, flush=True)
        except Exception:
            pass
        return item

    def remove(self, item_id):
        for x in list(self.buf):
            if x.get("id") == item_id:
                try:
                    self.buf.remove(x)
                except Exception:
                    pass
                return True
        return False

    def info(self, m):
        self.add("info", m)

    def warn(self, m):
        self.add("warn", m)

    def error(self, m):
        self.add("error", m)

    def ok(self, m):
        self.add("ok", m)

    def tail(self, n=200):
        return list(self.buf)[-n:]


log = Log()


def get_loop():
    try:
        return asyncio.get_running_loop()
    except AttributeError:
        return asyncio.get_event_loop()



class Config:
    def __init__(self):
        self.path = DATA / "config.json"
        self.data = {}
        self.load()

    LEGACY_MODELS = {"deepseek-chat", "deepseek-reasoner", "deepseek-v3", "deepseek-chat-v3", "deepseek-v3.1", "deepseek-v3.2"}

    def load(self):
        raw = read_json(self.path, {})
        if isinstance(raw, dict):
            raw.pop("token", None)
        self.data = deep_merge(DEFAULT_CONFIG, raw)
        llm = self.data.get("llm") or {}
        base = str(llm.get("base_url") or "")
        model = str(llm.get("model") or "").strip().lower()
        changed = False
        if "api.deepseek.com" in base and model in self.LEGACY_MODELS:
            llm["model"] = "deepseek-flash"
            self.data["llm"] = llm
            changed = True
        if not isinstance(self.data.get("auth"), dict):
            self.data["auth"] = dict(DEFAULT_CONFIG["auth"])
            changed = True
        if not isinstance(self.data.get("update"), dict):
            self.data["update"] = dict(DEFAULT_CONFIG["update"])
            changed = True
        if changed:
            self.save()

    def save(self):
        write_json(self.path, self.data)

    def get(self, *keys, **kw):
        cur = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return kw.get("default")
            cur = cur[k]
        return cur

    def update(self, patch):
        self.data = deep_merge(self.data, patch)
        self.save()


cfg = Config()


class Sessions:
    def __init__(self):
        self.map = {}

    def create(self, user=""):
        tok = secrets.token_urlsafe(32)
        self.map[tok] = {"exp": time.time() + 86400, "user": str(user or "")}
        return tok

    def valid(self, tok):
        item = self.map.get(tok)
        if not item:
            return False
        if item.get("exp", 0) < time.time():
            self.map.pop(tok, None)
            return False
        return True

    def user(self, tok):
        item = self.map.get(tok)
        return str(item.get("user") or "") if item else ""

    def revoke(self, tok):
        self.map.pop(tok, None)

    def clear(self):
        self.map.clear()

    def gc(self):
        t = time.time()
        for k in [k for k, v in self.map.items() if v.get("exp", 0) < t]:
            self.map.pop(k, None)


sessions = Sessions()


class Auth:
    """访问令牌认证：30 位字母数字混合令牌，只保存在本机。"""

    ALGO = "token"
    LENGTH = 30
    ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    TOKEN_RE = re.compile(r"^[A-Za-z0-9]{30}$")

    def get(self):
        return cfg.get("auth", default={}) or {}

    def initialized(self):
        return bool(self.token())

    def username(self):
        return "admin"

    def token(self):
        return str(self.get().get("token") or "")

    @classmethod
    def token_errors(cls, tok):
        tok = str(tok or "")
        if not cls.TOKEN_RE.match(tok):
            return ["令牌需为 30 位字母与数字"]
        if not re.search(r"[A-Za-z]", tok) or not re.search(r"[0-9]", tok):
            return ["令牌需同时包含字母与数字"]
        return []

    @classmethod
    def random_token(cls, length=None):
        n = int(length or cls.LENGTH)
        while True:
            tok = "".join(secrets.choice(cls.ALPHABET) for _ in range(n))
            if re.search(r"[A-Za-z]", tok) and re.search(r"[0-9]", tok):
                return tok

    def set_token(self, tok=None):
        tok = str(tok or "").strip()
        if not tok:
            tok = self.random_token()
        errs = self.token_errors(tok)
        if errs:
            raise ValueError("；".join(errs))
        cfg.update({"auth": {
            "algo": self.ALGO,
            "token": tok,
            "updated_at": int(time.time()),
        }})
        self.save_copy(tok)
        return tok

    def ensure_token(self):
        tok = self.token()
        if tok and not self.token_errors(tok):
            self.save_copy(tok)
            return tok
        return self.set_token()

    def verify(self, tok):
        cur = self.token()
        if not cur:
            return False
        return hmac.compare_digest(str(tok or ""), cur)

    @staticmethod
    def save_copy(tok):
        try:
            fp = DATA / "TOKEN.txt"
            with open(str(fp), "w", encoding="utf-8") as f:
                f.write("YueWuBot 控制台访问令牌\n")
                f.write("令牌：%s\n" % tok)
                f.write("重置：./start.sh token --reset\n")
                f.write("生成时间：%s\n" % ts())
            os.chmod(str(fp), 0o600)
        except Exception:
            pass


auth = Auth()



PLATFORM_KINDS = ("qq",)
KIND_TITLES = {"qq": "QQ 官方机器人"}


def _segs_text(segments):
    out = []
    for s in (segments or []):
        if not isinstance(s, dict):
            continue
        t = s.get("type")
        d = s.get("data") or {}
        if t == "text":
            out.append(str(d.get("text") or ""))
        elif t == "at":
            out.append("@" + str(d.get("qq") or d.get("user_id") or ""))
        elif t == "image":
            u = str(d.get("url") or d.get("file") or "")
            if u.startswith("http"):
                out.append(u)
    return "".join(out).strip()


class PlatformLink:
    """QQ 官方机器人连接：走开放平台网关，回复经 HTTP 接口发出。"""

    def __init__(self, bot, entry):
        self.bot = bot
        self.id = str(entry.get("id") or "default")
        self.name = str(entry.get("name") or self.id)
        self.kind = "qq"
        self.direction = "client"
        self.enabled = bool(entry.get("enabled", True))
        self.self_id = str(entry.get("self_id") or "")
        self.app_id = str(entry.get("app_id") or "")
        self.app_secret = str(entry.get("app_secret") or "")
        self.intents = int(entry.get("intents") or (1 << 25))
        self.nickname = ""
        self.sockets = []
        self.online = False
        self.reconnects = 0
        self.fails = 0
        self.last_error = ""
        self.session = None
        self._task = None
        self._qq_token = ""
        self._qq_exp = 0
        self._qq_seq = 0
        self.last_seen = 0

    def entry(self):
        return {
            "id": self.id, "name": self.name, "kind": "qq",
            "direction": "client", "url": "", "access_token": "",
            "enabled": self.enabled, "self_id": self.self_id,
            "app_id": self.app_id, "app_secret": self.app_secret,
            "token": "", "intents": self.intents,
        }

    def snapshot(self):
        d = self.entry()
        d["app_secret"] = "******" if self.app_secret else ""
        d.update({
            "kind_title": KIND_TITLES.get(self.kind, self.kind),
            "online": self.online,
            "nickname": self.nickname,
            "active": len(self.sockets),
            "reconnects": self.reconnects,
            "fails": self.fails,
            "last_error": self.last_error,
            "last_seen": self.last_seen,
        })
        return d

    def start(self):
        if not self.enabled:
            return
        if not (self.app_id and self.app_secret):
            self.last_error = "缺少 AppID / AppSecret"
            return
        if self._task is None or self._task.done():
            self._task = get_loop().create_task(self._loop())

    async def stop(self):
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass
        for ws in list(self.sockets):
            try:
                await ws.close()
            except Exception:
                pass
        self.sockets.clear()
        self.online = False

    def ws_cleanup(self):
        self.sockets = [w for w in self.sockets if not w.closed]
        self.online = bool(self.sockets)

    async def _loop(self):
        delay = 5
        while self.enabled:
            ok = False
            try:
                ok = await self._qq_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = str(e)[:240]
                self.fails += 1
                if self.fails <= 3 or self.fails % 12 == 0:
                    log.warn("平台连接失败（%s 第 %d 次）：%s" % (self.name, self.fails, self.last_error))
            if not ok:
                self.online = False
            delay = 5 if ok else min(delay * 2, 60)
            await asyncio.sleep(delay)

    def _client(self):
        if self.session is None or self.session.closed:
            self.session = ClientSession()
        return self.session

    async def call(self, action, socket=None, **params):
        raise RuntimeError("QQ 官方机器人不支持 OneBot 接口：%s" % action)

    async def send_text(self, message_type, target, text, socket=None, **kw):
        return await self._qq_send(message_type, target, str(text), kw.get("reply_to") or "")

    async def send_image(self, message_type, target, src, socket=None, **kw):
        if not str(src).startswith(("http://", "https://")):
            raise RuntimeError("QQ 官方机器人图片发送需要公网 URL")
        return await self._qq_send(message_type, target, str(src), kw.get("reply_to") or "")

    async def send_rich(self, message_type, target, segments, socket=None, **kw):
        return await self.send_text(message_type, target, _segs_text(segments), socket=socket, **kw)

    async def _qq_access(self):
        if self._qq_token and self._qq_exp > time.time() + 60:
            return self._qq_token
        if not (self.app_id and self.app_secret):
            raise RuntimeError("缺少 AppID / AppSecret")
        async with self._client().post(
                "https://bots.qq.com/app/getAppAccessToken",
                json={"appId": self.app_id, "clientSecret": self.app_secret}, timeout=20) as r:
            body = await r.text()
        try:
            d = json.loads(body)
        except Exception:
            raise RuntimeError("QQ 开放平台令牌解析失败")
        tok = d.get("access_token")
        if not tok:
            raise RuntimeError(str(d.get("message") or d.get("error") or body[:160]))
        self._qq_token = str(tok)
        self._qq_exp = time.time() + int(d.get("expires_in") or 7200)
        return self._qq_token

    async def _qq_once(self):
        token = await self._qq_access()
        headers = {"Authorization": "QQBot " + token, "X-Union-Appid": self.app_id}
        async with self._client().get("https://api.sgroup.qq.com/gateway", headers=headers, timeout=20) as r:
            body = await r.text()
        try:
            gw = json.loads(body).get("url")
        except Exception:
            gw = ""
        if not gw:
            raise RuntimeError("获取 QQ 网关失败：%s" % body[:160])
        async with self._client().ws_connect(gw, heartbeat=None, max_msg_size=0) as ws:
            self.sockets.append(ws)
            self.online = True
            self.fails = 0
            self.reconnects += 1
            self.last_error = ""
            log.ok("平台已连接：%s（QQ 官方网关）" % self.name)
            hb_task = None
            try:
                async for msg in ws:
                    if msg.type != WSMsgType.TEXT:
                        if msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
                        continue
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue
                    op = data.get("op")
                    if op == 10:
                        interval = (data.get("d") or {}).get("heartbeat_interval") or 41250
                        await ws.send_json({"op": 2, "d": {
                            "token": "QQBot " + token, "intents": self.intents,
                            "shard": [0, 1],
                            "properties": {"$os": _platform.system(), "$browser": "YueWuBot", "$device": "server"},
                        }})
                        hb_task = get_loop().create_task(self._qq_heartbeat(ws, interval / 1000.0))
                    elif op == 0:
                        self.last_seen = int(time.time())
                        ev = self._qq_event(data.get("t"), data.get("d") or {})
                        if ev:
                            ev["_yw_link"] = self
                            ev["_yw_ws"] = ws
                            get_loop().create_task(self.bot.handle_event(ev))
                    elif op == 7:
                        raise RuntimeError("QQ 网关要求重连")
                    elif op == 9:
                        self._qq_token = ""
            finally:
                if hb_task:
                    hb_task.cancel()
                try:
                    self.sockets.remove(ws)
                except ValueError:
                    pass
                self.ws_cleanup()
        log.warn("平台连接已断开：%s" % self.name)
        return True

    async def _qq_heartbeat(self, ws, interval):
        self._qq_seq = 0
        while not ws.closed:
            await asyncio.sleep(max(5.0, interval))
            try:
                self._qq_seq += 1
                await ws.send_json({"op": 1, "d": self._qq_seq})
            except Exception:
                return

    def _qq_event(self, t, d):
        author = d.get("author") or {}
        content = str(d.get("content") or "").strip()
        mid = str(d.get("id") or "")
        if t == "C2C_MESSAGE_CREATE":
            uid = str(author.get("user_openid") or author.get("id") or "")
            if not uid:
                return None
            return {"post_type": "message", "message_type": "private", "user_id": uid,
                    "group_id": "", "message": [{"type": "text", "data": {"text": content}}],
                    "raw_message": content, "message_id": mid,
                    "sender": {"nickname": str(author.get("username") or "")},
                    "_qq_msg_id": mid, "_qq_target": uid}
        if t in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
            gid = str(d.get("group_openid") or "")
            uid = str(author.get("member_openid") or author.get("user_openid") or "")
            if not gid:
                return None
            return {"post_type": "message", "message_type": "group", "user_id": uid,
                    "group_id": gid, "message": [{"type": "text", "data": {"text": content}}],
                    "raw_message": content, "message_id": mid,
                    "sender": {"nickname": str(author.get("username") or "")},
                    "_qq_msg_id": mid, "_qq_target": gid}
        return None

    async def _qq_send(self, message_type, target, text, reply_to=""):
        token = await self._qq_access()
        url = ("https://api.sgroup.qq.com/v2/groups/%s/messages" % target
               if message_type == "group" else
               "https://api.sgroup.qq.com/v2/users/%s/messages" % target)
        payload = {"content": text[:1800], "msg_type": 0}
        if reply_to:
            payload["msg_id"] = reply_to
            payload["msg_seq"] = int(time.time()) % 100000
        headers = {"Authorization": "QQBot " + token, "X-Union-Appid": self.app_id,
                   "Content-Type": "application/json"}
        async with self._client().post(url, headers=headers, json=payload, timeout=20) as r:
            body = await r.text()
            if r.status >= 400:
                raise RuntimeError("QQ 发送失败 %s: %s" % (r.status, body[:160]))
        return {"status": "ok"}


class PlatformManager:
    def __init__(self, bot):
        self.bot = bot
        self.links = []
        self.load()

    def read_entries(self):
        raw = cfg.get("platform", "links", default=[]) or []
        if not isinstance(raw, list):
            raw = []
        out, seen = [], set()
        for i, item in enumerate(raw[:20]):
            if not isinstance(item, dict):
                continue
            cid = slugify(item.get("id") or ("conn-%d" % (i + 1)), "conn-%d" % (i + 1))
            if cid in seen:
                continue
            seen.add(cid)
            out.append({
                "id": cid, "name": str(item.get("name") or cid), "kind": "qq",
                "direction": "client", "url": "", "access_token": "",
                "enabled": bool(item.get("enabled", True)),
                "self_id": str(item.get("self_id") or ""),
                "app_id": str(item.get("app_id") or "").strip(),
                "app_secret": str(item.get("app_secret") or "").strip(),
                "token": "", "intents": int(item.get("intents") or (1 << 25)),
            })
        return out or [{"id": "default", "name": "默认连接", "kind": "qq",
                        "direction": "client", "url": "", "access_token": "",
                        "enabled": True, "self_id": "", "app_id": "", "app_secret": "",
                        "token": "", "intents": 1 << 25}]

    def load(self):
        self.links = [PlatformLink(self.bot, e) for e in self.read_entries()]

    async def reload(self):
        old = list(self.links)
        self.load()
        for link in old:
            await link.stop()
        for link in self.links:
            link.start()

    def start_all(self):
        for link in self.links:
            link.start()

    def primary(self):
        for link in self.links:
            if link.online and link.self_id:
                return link
        for link in self.links:
            if link.online:
                return link
        for link in self.links:
            if link.enabled:
                return link
        return self.links[0]

    def snapshots(self):
        return [x.snapshot() for x in self.links]



class LLM:
    def __init__(self):
        self.session = None
        self.fails = 0
        self.last_ms = 0
        self.protocol = "openai"

    def _proto(self):
        p = str(cfg.get("llm", "protocol", default="openai") or "openai").strip().lower()
        if p in ("anthropic", "claude", "messages", "anthropic-messages"):
            return "anthropic"
        return "openai"

    def _url(self, proto):
        base = (cfg.get("llm", "base_url", default="") or "").rstrip("/")
        if not base:
            raise RuntimeError("未配置模型地址")
        if proto == "anthropic":
            if base.endswith("/messages"):
                return base
            if base.endswith("/v1"):
                return base + "/messages"
            return base + "/v1/messages"
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    @staticmethod
    def _split_system(messages):
        """Anthropic 的 system 是顶层字段，需要从 messages 里拆出来"""
        system, rest = [], []
        for m in messages or []:
            if not isinstance(m, dict):
                continue
            if m.get("role") == "system":
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    system.append(c)
                continue
            rest.append(m)
        return "\n\n".join(system).strip(), rest

    @staticmethod
    def _anth_content(content):
        """把 OpenAI 风格的 content 转成 Anthropic 的块结构"""
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if isinstance(content, list):
            blocks = []
            for part in content:
                if isinstance(part, str):
                    blocks.append({"type": "text", "text": part})
                elif isinstance(part, dict) and part.get("type") == "text":
                    blocks.append({"type": "text", "text": str(part.get("text") or "")})
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    url = ((part.get("image_url") or {}).get("url") or "")
                    mm = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", url, re.S)
                    if mm:
                        mtype = "image/jpeg" if mm.group(1).lower() in ("image/jpg", "image/pjpeg") else mm.group(1)
                        blocks.append({"type": "image", "source": {
                            "type": "base64", "media_type": mtype, "data": mm.group(2)}})
            return blocks or [{"type": "text", "text": ""}]
        return [{"type": "text", "text": str(content or "")}]

    async def chat(self, messages, temperature=None, max_tokens=None):
        key = cfg.get("llm", "api_key", default="")
        if not key:
            raise RuntimeError("未配置 API Key")
        if self.session is None:
            self.session = ClientSession()
        proto = self._proto()
        self.protocol = proto
        model = cfg.get("llm", "model", default="deepseek-flash")
        temp = cfg.get("llm", "temperature", default=0.85) if temperature is None else temperature
        mtok = cfg.get("llm", "max_tokens", default=800) if max_tokens is None else max_tokens

        if proto == "anthropic":
            system, rest = self._split_system(messages)
            # Anthropic 要求 user / assistant 严格交替，相邻同角色消息需要合并
            anth_msgs = []
            for m in rest:
                role = str(m.get("role") or "user")
                if role not in ("user", "assistant"):
                    continue
                blocks = self._anth_content(m.get("content"))
                if anth_msgs and anth_msgs[-1]["role"] == role:
                    anth_msgs[-1]["content"].extend(blocks)
                else:
                    anth_msgs.append({"role": role, "content": blocks})
            if not anth_msgs:
                anth_msgs = [{"role": "user", "content": [{"type": "text", "text": "你好"}]}]
            payload = {
                "model": model,
                "messages": anth_msgs,
                "max_tokens": int(mtok or 800),
            }
            if system:
                payload["system"] = system
            if temp is not None:
                payload["temperature"] = temp
            headers = {
                "x-api-key": key,
                "anthropic-version": str(cfg.get("llm", "anthropic_version", default="2023-06-01") or "2023-06-01"),
                "Content-Type": "application/json",
            }
        else:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temp,
                "max_tokens": mtok,
                "stream": False,
            }
            headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

        try:
            attempts = max(1, min(int(cfg.get("llm", "retries", default=3) or 3), 5))
        except Exception:
            attempts = 3
        try:
            timeout = max(3.0, float(cfg.get("llm", "timeout", default=8) or 8))
        except Exception:
            timeout = 8.0
        last = None
        for i in range(attempts):
            t0 = time.time()
            try:
                async with self.session.post(
                    self._url(proto),
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                ) as resp:
                    body = await resp.text()
                    status = resp.status
            except Exception as e:
                last = RuntimeError("模型连接失败：%s" % str(e)[:160])
                if i + 1 < attempts:
                    await asyncio.sleep(0.35 * (i + 1))
                continue
            if status >= 400:
                err = RuntimeError("模型返回 %s: %s" % (status, body[:300]))
                if status < 500:
                    raise err
                last = err
                if i + 1 < attempts:
                    await asyncio.sleep(0.35 * (i + 1))
                continue
            self.last_ms = int((time.time() - t0) * 1000)
            try:
                data = json.loads(body)
            except Exception:
                raise RuntimeError("模型响应解析失败: %s" % body[:200])
            if proto == "anthropic":
                if isinstance(data.get("error"), dict):
                    raise RuntimeError(str(data["error"].get("message") or "模型返回错误")[:200])
                blocks = data.get("content")
                if isinstance(blocks, list):
                    text = "".join(str(b.get("text") or "") for b in blocks
                                   if isinstance(b, dict) and b.get("type") == "text")
                    return text.strip()
                raise RuntimeError("模型响应解析失败: %s" % body[:200])
            try:
                return (data["choices"][0]["message"]["content"] or "").strip()
            except Exception:
                raise RuntimeError("模型响应解析失败: %s" % body[:200])
        raise last or RuntimeError("模型调用失败")
llm = LLM()


class Ctx:
    def __init__(self, bot, event):
        self.bot = bot
        self.event = event
        self.ob = event.get("_yw_link") or bot.ob
        self._ws = None
        self._reply_id = str(event.get("_qq_msg_id") or "")
        self.group_id = str(event.get("group_id") or "")
        self.user_id = str(event.get("user_id") or "")
        self.message_type = "group" if self.group_id else "private"
        self.sender = (event.get("sender") or {}).get("nickname") or ""
        self.text = bot.extract_text(event)
        self.raw = event
        self.images = bot.extract_images(event)
        self.plugin = None
        self.cfg = {}

    @property
    def target(self):
        return self.group_id or self.user_id

    async def reply(self, text, at=False):
        text = str(text)
        clean, imgs = split_reply_images(text)
        if imgs and bool(cfg.get("reply", "auto_image", default=True)):
            res = None
            if clean:
                res = await self._send_text(clean, at)
            for url in imgs[:4]:
                try:
                    await self.send_image(url)
                except Exception as e:
                    log.warn("回复配图发送失败：%s" % str(e)[:100])
            return res
        return await self._send_text(text, at)

    async def _send_text(self, text, at=False):
        prefix = ("@" + str(self.sender) + " ") if (at and self.group_id and self.sender) else ""
        return await self.ob.send_text(self.message_type, self.target,
                                       prefix + str(text), reply_to=self._reply_id)

    async def send_image(self, src):
        return await self.ob.send_image(self.message_type, self.target, src, reply_to=self._reply_id)

    reply_image = send_image

    async def api(self, action, **params):
        raise RuntimeError("QQ 官方机器人不支持 OneBot 接口：%s" % action)

    async def llm(self, prompt, system=None):
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return await llm.chat(msgs)

    def save_config(self, values):
        self.bot.plugins.save_plugin_config(self.plugin, values)


HOOK_NAMES = ("on_startup", "on_shutdown", "on_filter", "on_message", "on_group_message",
              "on_private_message", "on_notice", "on_request", "on_tick")


def fire(res):
    if asyncio.iscoroutine(res):
        try:
            asyncio.ensure_future(res)
        except Exception:
            pass
    return res


class PluginManager:
    def __init__(self, bot):
        self.bot = bot
        self.meta = read_json(DATA / "plugins.json", {})
        self.mods = {}
        self.conf = read_json(DATA / "plugin_config.json", {})
        self.last_used = {}
        self.catalog = {}

    def discover(self):
        out = []
        for d in sorted(PLUGIN_DIR.iterdir() if PLUGIN_DIR.exists() else []):
            if not d.is_dir():
                continue
            m = read_json(d / "m.json", None)
            if not isinstance(m, dict):
                continue
            name = d.name
            st = self.meta.get(name, {})
            out.append({
                "name": name,
                "title": m.get("name") or name,
                "version": m.get("version") or "1.0.0",
                "author": m.get("author") or "unknown",
                "description": m.get("description") or "",
                "homepage": m.get("homepage") or "",
                "repository": m.get("repository") or "",
                "icon": "/api/plugins/%s/icon" % name if (d / (m.get("icon") or "icon.png")).exists() else "",
                "enabled": bool(st.get("enabled", True)),
                "admin_only": bool(st.get("admin_only", False)),
                "hooked": bool(m.get("hooks")),
                "commands": [c.get("name") for c in (m.get("commands") or []) if isinstance(c, dict)],
                "cmds": [c for c in (m.get("commands") or []) if isinstance(c, dict)],
                "hook_map": m.get("hooks") or {},
                "triggers": m.get("triggers") or {},
                "config_schema": m.get("config") or {},
                "loaded": name in self.mods,
                "size": human_size(sum(f.stat().st_size for f in d.rglob("*") if f.is_file())),
            })
        return out

    def refresh_catalog(self):
        out = self.discover()
        self.catalog = {e["name"]: e for e in out}
        return out

    def ensure_loaded(self, name):
        if name in self.mods:
            return True
        e = self.catalog.get(name)
        if e is None:
            self.refresh_catalog()
            e = self.catalog.get(name)
        if not e or not e.get("enabled", True):
            return False
        try:
            self.load(name, quiet=True)
        except Exception as ex:
            log.error("插件唤醒失败 %s: %s" % (name, ex))
            log.error(traceback.format_exc(limit=2))
            return False
        self.last_used[name] = time.time()
        log.info("插件已唤醒：%s" % name)
        try:
            fire(self.call_hook(name, "on_startup", self.bot))
        except Exception as ex:
            log.warn("插件 %s 启动钩子出错：%s" % (name, ex))
        return True

    @staticmethod
    def _cmd_match(entry, cmd):
        for c in (entry.get("cmds") or []):
            names = [c.get("name")] + list(c.get("aliases") or [])
            if cmd in [str(x) for x in names if x]:
                return True
        return False

    @staticmethod
    def _reclaimable(entry):
        hook_map = entry.get("hook_map") or {}
        runtime_hooks = [h for h in hook_map if h not in ("on_startup", "on_shutdown")]
        if not runtime_hooks:
            return True
        trg = entry.get("triggers") or {}
        if isinstance(trg, dict) and any(trg.get(h) for h in runtime_hooks):
            return True
        if isinstance(trg, list) and trg:
            return True
        return False

    def load(self, name, quiet=False):
        d = PLUGIN_DIR / name
        meta = read_json(d / "m.json", None)
        if not isinstance(meta, dict):
            raise RuntimeError("m.json 缺失或格式错误")
        if name in self.mods:
            self.unload(name, quiet=True)
        modname = "ywp_" + name
        spec = importlib.util.spec_from_file_location(modname, str(d / "main.py"))
        if spec is None or spec.loader is None:
            raise RuntimeError("main.py 无法加载")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        sys.path.insert(0, str(d))
        try:
            spec.loader.exec_module(mod)
        finally:
            try:
                sys.path.remove(str(d))
            except ValueError:
                pass
        self.mods[name] = {"mod": mod, "meta": meta}
        self.last_used[name] = time.time()
        if not quiet:
            log.ok("插件已加载：%s v%s" % (meta.get("name") or name, meta.get("version") or "?"))
        return mod

    def unload(self, name, quiet=False):
        info = self.mods.pop(name, None)
        modname = "ywp_" + name
        sys.modules.pop(modname, None)
        for k in [k for k in list(sys.modules) if k.startswith(modname + ".")]:
            sys.modules.pop(k, None)
        if info and not quiet:
            log.info("插件已卸载并回收内存：%s" % name)
        gc.collect()

    def loaded_count(self):
        return len(self.mods)

    def get(self, name):
        info = self.mods.get(name)
        return info["mod"] if info else None

    def plugin_config(self, name):
        info = self.mods.get(name)
        meta = info["meta"] if info else read_json(PLUGIN_DIR / name / "m.json", {})
        schema = meta.get("config") or {}
        saved = self.conf.get(name, {})
        out = {}
        for k, v in schema.items():
            if isinstance(v, dict) and "default" in v:
                out[k] = saved.get(k, v.get("default"))
            else:
                out[k] = saved.get(k)
        return out

    def save_plugin_config(self, name, values):
        self.conf[name] = deep_merge(self.conf.get(name, {}), values)
        write_json(DATA / "plugin_config.json", self.conf)

    def set_enabled(self, name, enabled):
        self.meta.setdefault(name, {})["enabled"] = bool(enabled)
        write_json(DATA / "plugins.json", self.meta)
        self.refresh_catalog()
        if enabled:
            try:
                self.load(name)
                fire(self.call_hook(name, "on_startup", self.bot))
            except Exception as e:
                log.error("插件启用失败 %s: %s" % (name, e))
                return False
        else:
            try:
                fire(self.call_hook(name, "on_shutdown", self.bot))
            except Exception:
                pass
            self.unload(name)
        return True

    def is_admin(self, uid):
        admins = cfg.get("admins", default=[]) or []
        uid = str(uid or "").strip()
        return bool(uid) and any(str(a).strip() == uid for a in admins if str(a).strip())

    def admin_only(self, name):
        return bool(self.meta.get(name, {}).get("admin_only", False))

    def set_admin_only(self, name, value):
        self.meta.setdefault(name, {})["admin_only"] = bool(value)
        write_json(DATA / "plugins.json", self.meta)
        self.refresh_catalog()

    @staticmethod
    def _trigger_hit_any(entry, text):
        trg = entry.get("triggers") or {}
        words = []
        if isinstance(trg, dict):
            for v in trg.values():
                if isinstance(v, (list, tuple)):
                    words.extend(v)
                elif isinstance(v, str):
                    words.append(v)
        elif isinstance(trg, (list, tuple)):
            words = list(trg)
        text = str(text or "")
        return any(w and str(w) in text for w in words)

    async def install_all_deps(self):
        for p in self.refresh_catalog():
            if not p.get("enabled", True):
                continue
            try:
                await install_plugin_deps(p["name"])
            except Exception as e:
                log.warn("插件 %s 依赖处理失败：%s" % (p["name"], str(e)[:120]))

    def start_all(self):
        for p in self.refresh_catalog():
            if not p["enabled"]:
                continue
            try:
                self.load(p["name"], quiet=True)
                fire(self.call_hook(p["name"], "on_startup", self.bot))
            except Exception as e:
                log.error("插件启动失败 %s: %s" % (p["name"], e))
        n = self.loaded_count()
        if n:
            log.ok("已加载 %d 个插件" % n)

    def call_hook(self, name, hook, *args):
        info = self.mods.get(name)
        if not info:
            return None
        meta = info["meta"]
        fn_name = (meta.get("hooks") or {}).get(hook)
        if not fn_name:
            return None
        fn = getattr(info["mod"], fn_name, None)
        if not callable(fn):
            return None
        self.last_used[name] = time.time()
        res = fn(*args)
        if asyncio.iscoroutine(res):
            return res
        return res

    async def _run_hook(self, name, hook, ctx, *extra):
        if hook not in ("on_filter", "on_notice", "on_request", "on_tick", "on_startup", "on_shutdown"):
            if self.admin_only(name) and not self.is_admin(ctx.user_id):
                return False
        try:
            ctx.plugin = name
            ctx.cfg = self.plugin_config(name)
            res = self.call_hook(name, hook, ctx, *extra)
            if res is not None and asyncio.iscoroutine(res):
                res = await res
        except Exception as e:
            log.error("插件 %s 的 %s 出错：%s" % (name, hook, e))
            log.error(traceback.format_exc(limit=2))
            return False
        return bool(res)

    async def dispatch_hook(self, hook, ctx, *extra):
        for name in list(self.mods.keys()):
            if await self._run_hook(name, hook, ctx, *extra):
                return True
        if not self.catalog:
            self.refresh_catalog()
        for name, e in list(self.catalog.items()):
            if name in self.mods or not e.get("enabled", True):
                continue
            if hook not in (e.get("hook_map") or {}):
                continue
            if not self._trigger_hit(e, hook, ctx):
                continue
            if self.ensure_loaded(name) and await self._run_hook(name, hook, ctx, *extra):
                return True
        return False

    @staticmethod
    def _trigger_hit(entry, hook, ctx):
        trg = entry.get("triggers") or {}
        if isinstance(trg, dict):
            words = trg.get(hook) or []
        elif isinstance(trg, list):
            words = trg
        else:
            words = []
        if not words:
            return False
        text = getattr(ctx, "text", "") or ""
        return any(w and str(w) in text for w in words)

    async def _exec_command(self, name, ctx, cmd, args):
        info = self.mods.get(name)
        if not info:
            return False
        for c in (info["meta"].get("commands") or []):
            if not isinstance(c, dict):
                continue
            names = [c.get("name")]
            names += list(c.get("aliases") or [])
            if cmd not in [str(x) for x in names if x]:
                continue
            if self.admin_only(name) and not self.is_admin(ctx.user_id):
                await ctx.reply("权限不足")
                return True
            fn = getattr(info["mod"], c.get("handler") or "", None)
            if not callable(fn):
                continue
            self.last_used[name] = time.time()
            ctx.plugin = name
            ctx.cfg = self.plugin_config(name)
            try:
                res = fn(ctx, args)
                if asyncio.iscoroutine(res):
                    res = await res
                return True
            except Exception as e:
                log.error("指令 /%s 执行出错：%s" % (cmd, e))
                log.error(traceback.format_exc(limit=2))
                try:
                    await ctx.reply("执行出错啦：%s" % str(e)[:120])
                except Exception:
                    pass
                return True
        return False

    async def dispatch_command(self, ctx, cmd, args):
        for name in list(self.mods.keys()):
            if await self._exec_command(name, ctx, cmd, args):
                return True
        if not self.catalog:
            self.refresh_catalog()
        for name, e in list(self.catalog.items()):
            if name in self.mods or not e.get("enabled", True):
                continue
            if not self._cmd_match(e, cmd):
                continue
            if self.ensure_loaded(name) and await self._exec_command(name, ctx, cmd, args):
                return True
        return False

    def all_commands(self):
        if not self.catalog:
            self.refresh_catalog()
        out = []
        for name, e in self.catalog.items():
            if not e.get("enabled", True):
                continue
            for c in (e.get("cmds") or []):
                out.append({"plugin": name, "name": c.get("name"), "aliases": c.get("aliases") or [],
                            "usage": c.get("usage") or "", "description": c.get("description") or "",
                            "loaded": name in self.mods})
        return out

    def idle_reclaim(self):
        minutes = int(cfg.get("runtime", "plugin_idle_minutes", default=10) or 0)
        if minutes <= 0:
            return 0
        now = time.time()
        n = 0
        for name in list(self.mods.keys()):
            e = self.catalog.get(name)
            if e and not self._reclaimable(e):
                continue
            last = self.last_used.get(name, now)
            if now - last > minutes * 60:
                self.unload(name, quiet=True)
                n += 1
        if n:
            log.info("闲置插件已回收 %d 个，释放内存中" % n)
            gc.collect()
        return n


class Bot:
    def __init__(self):
        self.platforms = PlatformManager(self)
        self.plugins = PluginManager(self)
        self.ctxs = {}
        self.stats = {"recv": 0, "sent": 0, "llm": 0, "cmd": 0, "err": 0}
        self.started_at = time.time()
        self.last_activity = time.time()
        self._cool = {}

    def segments(self, event):
        msg = event.get("message")
        if isinstance(msg, list):
            return msg
        raw = event.get("raw_message") or ""
        segs = []
        for part in re.split(r"(\[CQ:[^\]]+\])", raw):
            if not part:
                continue
            m = re.match(r"\[CQ:([a-zA-Z_]+)(?:,([^\]]*))?\]", part)
            if not m:
                segs.append({"type": "text", "data": {"text": part}})
                continue
            data = {}
            if m.group(2):
                for kv in m.group(2).split(","):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        data[k] = v
            segs.append({"type": m.group(1), "data": data})
        return segs

    def extract_text(self, event):
        segs = self.segments(event)
        parts = []
        for s in segs:
            t = s.get("type")
            if t == "text":
                parts.append(s.get("data", {}).get("text", ""))
            elif t == "image":
                parts.append("[图片]")
            elif t == "face":
                parts.append("[表情]")
        return "".join(parts).strip()

    def extract_images(self, event):
        out = []
        for s in self.segments(event):
            if s.get("type") != "image":
                continue
            d = s.get("data") or {}
            url = d.get("url") or d.get("file") or ""
            out.append(url)
        return out

    @property
    def ob(self):
        return self.platforms.primary()

    def is_at_me(self, segs, self_id=""):
        for s in segs:
            if s.get("type") == "at" and str(s.get("data", {}).get("qq")) == str(self_id or self.ob.self_id):
                return True
        return False

    async def handle_event(self, event):
        try:
            pt = event.get("post_type")
            if pt == "message":
                self.stats["recv"] += 1
                self.last_activity = time.time()
                if await self.plugins.dispatch_hook("on_filter", Ctx(self, event)):
                    return
                await self.on_message(event)
            elif pt == "notice":
                ctx = Ctx(self, event)
                await self.plugins.dispatch_hook("on_notice", ctx)
            elif pt == "request":
                ctx = Ctx(self, event)
                await self.plugins.dispatch_hook("on_request", ctx)
            elif pt == "meta_event":
                if event.get("meta_event_type") == "lifecycle":
                    sid = str(event.get("self_id") or "")
                    if sid:
                        link = event.get("_yw_link")
                        if link is not None:
                            link.self_id = sid
                        else:
                            self.ob.self_id = sid
        except Exception as e:
            self.stats["err"] += 1
            log.error("事件处理异常：%s" % e)
            log.error(traceback.format_exc(limit=3))

    async def guard_plugins(self, ctx, text):
        pm = self.plugins
        if not pm.catalog:
            pm.refresh_catalog()
        locked = [n for n, e in pm.catalog.items()
                  if e.get("enabled", True) and pm.admin_only(n)]
        if not locked or pm.is_admin(ctx.user_id):
            return False
        cmd, _args = self.parse_command(text)
        for name in locked:
            e = pm.catalog.get(name, {})
            if cmd and pm._cmd_match(e, cmd):
                await ctx.reply("权限不足")
                return True
            if pm._trigger_hit_any(e, text):
                await ctx.reply("权限不足")
                return True
        return False

    async def on_message(self, event):
        uid = str(event.get("user_id") or "")
        gid = str(event.get("group_id") or "")
        event_link = event.get("_yw_link")
        event_self_id = str(getattr(event_link, "self_id", "") or self.ob.self_id or "")
        if uid and uid == event_self_id:
            return
        segs = self.segments(event)
        text = self.extract_text(event)
        if not text and not self.extract_images(event):
            return

        if re.match(r"^[＃#]\s*YueWu(?:Bot)?\s*$", text.strip(), re.I):
            ctx = Ctx(self, event)
            ctx.text = text.strip()
            await ctx.reply(status_text())
            return

        hash_chat = False
        if (not gid) or cfg.get("reply", "group_hash_chat", default=True):
            m = re.match(r"^[#＃]\s*(.+)$", text.strip(), re.S)
            if m and m.group(1).strip():
                hash_chat = True
                text = m.group(1).strip()

        at_me = False
        hit_prefix = None
        free_ok = False
        if gid:
            if not cfg.get("reply", "group_at", default=True):
                return
            at_me = self.is_at_me(segs, event_self_id)
            if not hash_chat:
                for p in (cfg.get("reply", "prefixes", default=[]) or []):
                    if p and text.startswith(p):
                        hit_prefix = p
                        break
                if hit_prefix:
                    text = text[len(hit_prefix):].strip()
            if not at_me and not hit_prefix and not hash_chat:
                free_ok = bool(cfg.get("reply", "group_command_any", default=True))
                if not free_ok:
                    return
        else:
            if not cfg.get("reply", "private", default=True):
                return

        clean = re.sub(r"\[图片\]|\[表情\]", "", text).strip()
        if not clean:
            return

        ctx = Ctx(self, event)
        ctx.text = clean
        if await self.guard_plugins(ctx, clean):
            return
        if clean.lower() in ("yuewu", "yuewubot") and (hash_chat or at_me or hit_prefix or not gid):
            await ctx.reply(status_text())
            return
        cmd, args = self.parse_command(clean)
        no_at = bool(gid) and not at_me and not hit_prefix
        if hash_chat or (no_at and not self.is_slash_command(clean)):
            cmd = None

        if cmd:
            self.stats["cmd"] += 1
            if await self.run_builtin(ctx, cmd, args):
                return
            if await self.plugins.dispatch_command(ctx, cmd, args):
                return
            if no_at:
                return
            await ctx.reply("没有这个指令哦，发 /help 看看能干啥")
            return

        if hash_chat:
            key = gid or uid
            cd = float(cfg.get("reply", "cooldown", default=1.2) or 0)
            now = time.time()
            if now - self._cool.get(key, 0) < cd:
                return
            self._cool[key] = now
            await self.chat_reply(ctx)
            return

        hooked = await self.plugins.dispatch_hook(
            "on_group_message" if gid else "on_private_message", ctx)
        if hooked:
            return
        if await self.plugins.dispatch_hook("on_message", ctx):
            return
        if no_at:
            return

        key = gid or uid
        cd = float(cfg.get("reply", "cooldown", default=1.2) or 0)
        now = time.time()
        if now - self._cool.get(key, 0) < cd:
            return
        self._cool[key] = now

        await self.chat_reply(ctx)

    @staticmethod
    def is_slash_command(text):
        return bool(re.match(r"^[/／][a-zA-Z0-9_\u4e00-\u9fa5]{1,20}(?:\s|$)", (text or "").strip()))

    def parse_command(self, text):
        m = re.match(r"^[/／#!！]([a-zA-Z0-9_\u4e00-\u9fa5]{1,20})\s*(.*)$", text.strip(), re.S)
        if not m:
            return None, ""
        return m.group(1).lower(), m.group(2).strip()

    HELP_TEXT = ("欢迎阅读小鸟游星野使用手册:https://chumianyi.github.io/bot/docs.html\n"
                 "如果访问不了，请执行/helps noweb查看所有插件。")

    TITLE_FIX = {"signin": "每日签到",
                 "wallpaper": "随机壁纸", "weather": "天气查询"}

    def plugin_list_text(self):
        cmds = self.plugins.all_commands()
        groups, order = {}, []
        for c in cmds:
            p = str(c.get("plugin") or "other")
            if p not in groups:
                groups[p] = []
                order.append(p)
            groups[p].append(c)
        if not order:
            return "还没有装插件。"
        total = sum(len(v) for v in groups.values())
        lines = ["小鸟游星野 · 插件列表",
                 "共 %d 个插件 · %d 条指令" % (len(order), total), ""]
        for i, p in enumerate(order, 1):
            e = self.plugins.catalog.get(p) or {}
            title = str(e.get("title") or p)
            if title == p and p in self.TITLE_FIX:
                title = self.TITLE_FIX[p]
            desc = str(e.get("description") or "").strip()
            if len(desc) > 64:
                desc = desc[:63] + "…"
            names, seen = [], set()
            for c in groups[p]:
                n = str(c.get("name") or "")
                if n and n not in seen:
                    seen.add(n)
                    names.append("/" + n)
            more = len(names) - 6
            if more > 0:
                names = names[:6] + ["…另 %d 条" % more]
            lines.append("%02d  %s" % (i, title))
            if desc:
                lines.append("    " + desc)
            for j in range(0, len(names), 4):
                lines.append("    " + "  ".join(names[j:j + 4]))
            lines.append("")
        lines.append("完整说明：https://chumianyi.github.io/bot/docs.html")
        return "\n".join(lines)

    async def run_builtin(self, ctx, cmd, args):
        if cmd in ("help", "帮助", "菜单"):
            await ctx.reply(self.HELP_TEXT)
            return True
        if cmd == "helps":
            if str(args or "").strip().lower() == "noweb":
                await ctx.reply(self.plugin_list_text())
            else:
                await ctx.reply(self.HELP_TEXT)
            return True
        if cmd in ("ping", "ping!"):
            await ctx.reply("pong")
            return True
        if cmd in ("yuewubot", "yuewu", "info", "信息", "状态"):
            await ctx.reply(status_text())
            return True
        if cmd in ("update", "更新检测", "检查更新"):
            data = await check_update(force=True)
            if data.get("error"):
                await ctx.reply("更新检测失败：%s" % data.get("error"))
            elif data.get("need"):
                await ctx.reply("发现新版本 %s（当前 %s）\n请打开控制台一键更新：%s" % (
                    data.get("latest"), VERSION, cfg.get("update", "url", default="")))
            else:
                await ctx.reply("您无需更新，当前版本 %s 已是最新。" % VERSION)
            return True
        if cmd in ("balance", "余额"):
            if not self.plugins.is_admin(ctx.user_id):
                await ctx.reply("仅管理员可查看余额")
                return True
            data = await fetch_balance()
            if not data.get("supported"):
                await ctx.reply("余额查询不可用：%s" % (data.get("message") or "平台不支持"))
            else:
                await ctx.reply("模型余额：%.2f %s" % (float(data.get("total") or 0),
                                                   data.get("currency") or "CNY"))
            return True
        if cmd in ("reset", "清空", "重置"):
            self.ctxs.pop(self.ctx_key(ctx.event), None)
            await ctx.reply("上下文清空啦")
            return True
        if cmd in ("me", "我的信息"):
            await ctx.reply("你的QQ：%s\n昵称：%s\n群：%s" % (ctx.user_id, ctx.sender or "-", ctx.group_id or "私聊"))
            return True
        return False

    def ctx_key(self, event):
        gid = str(event.get("group_id") or "")
        uid = str(event.get("user_id") or "")
        return ("g:" + gid + ":" + uid) if gid else ("p:" + uid)

    def history(self, key):
        if key not in self.ctxs:
            n = int(cfg.get("reply", "max_context", default=12) or 12)
            self.ctxs[key] = collections.deque(maxlen=max(2, n))
        return self.ctxs[key]

    async def chat_reply(self, ctx):
        key = self.ctx_key(ctx.event)
        hist = self.history(key)
        system = cfg.get("llm", "system_prompt", default=DEFAULT_PERSONA)
        msgs = [{"role": "system", "content": system}]
        for role, content in hist:
            msgs.append({"role": role, "content": content})

        content = ctx.text
        vision = bool(cfg.get("llm", "vision", default=True))
        if vision and ctx.images:
            parts = [{"type": "text", "text": ctx.text or "看看这张图"}]
            got = 0
            for src in ctx.images[:4]:
                data = await fetch_image_data_url(src)
                if not data:
                    continue
                parts.append({"type": "image_url", "image_url": {"url": data}})
                got += 1
            if got:
                content = parts
                log.info("随消息携带 %d 张图片给模型" % got)
        msgs.append({"role": "user", "content": content})

        try:
            reply = await llm.chat(msgs)
            self.stats["llm"] += 1
        except Exception as e:
            self.stats["err"] += 1
            log.error("模型调用失败：%s" % e)
            try:
                await ctx.reply("刚没接上信号，麻烦再说一遍嘛～")
            except Exception:
                pass
            return

        if not reply:
            return
        hist.append(("user", ctx.text))
        hist.append(("assistant", reply))

        text, images = split_reply_images(reply)
        limit = int(cfg.get("reply", "max_reply_chars", default=900) or 900)
        for chunk in self.split_text(text, limit):
            try:
                await ctx.reply(chunk)
                self.stats["sent"] += 1
                await asyncio.sleep(0.35)
            except Exception as e:
                self.stats["err"] += 1
                log.error("发送失败：%s" % e)
                break
        if images and bool(cfg.get("reply", "auto_image", default=True)):
            for url in images[:4]:
                try:
                    await ctx.send_image(url)
                    self.stats["sent"] += 1
                    await asyncio.sleep(0.3)
                except Exception as e:
                    log.warn("自动发图失败：%s" % str(e)[:120])

    def split_text(self, text, limit):
        text = (text or "").strip()
        if len(text) <= limit:
            return [text] if text else []
        parts = []
        while text:
            if len(text) <= limit:
                parts.append(text)
                break
            cut = text.rfind("\n", 0, limit)
            if cut < limit * 0.5:
                cut = max(text.rfind("。", 0, limit), text.rfind("！", 0, limit), text.rfind("？", 0, limit))
            if cut < limit * 0.5:
                cut = limit
            parts.append(text[:cut + 1].strip())
            text = text[cut + 1:].strip()
        return [p for p in parts if p]

    async def maintain(self):
        self._cfg_mtime = self._mtime(cfg.path)
        self._pw_item = None
        while True:
            await asyncio.sleep(5)
            try:
                sessions.gc()
                self.plugins.idle_reclaim()
                self.gc_ctx()
                self.gc_images()
                self.tick_external_config()
                self.tick_password_reset()
                if rss_mb() > float(cfg.get("runtime", "memory_soft_mb", default=120) or 120):
                    gc.collect()
            except Exception as e:
                log.warn("维护任务异常：%s" % e)

    @staticmethod
    def _mtime(path):
        try:
            return os.stat(str(path)).st_mtime
        except Exception:
            return 0

    def tick_external_config(self):
        mt = self._mtime(cfg.path)
        if not mt or mt <= getattr(self, "_cfg_mtime", 0):
            return
        old_hash = str((cfg.get("auth", default={}) or {}).get("hash") or "")
        cfg.load()
        new_hash = str((cfg.get("auth", default={}) or {}).get("hash") or "")
        self._cfg_mtime = mt
        if old_hash != new_hash:
            sessions.clear()
            log.warn("检测到密码已变更，所有控制台会话已失效")

    def tick_password_reset(self):
        fp = DATA / "password-reset.txt"
        if not fp.is_file():
            if self._pw_item:
                log.remove(self._pw_item)
                log.info("重置密码的明文记录已清除")
            self._pw_item = None
            return
        data = read_json(fp, {})
        if not data:
            return
        exp = int(data.get("expire") or 0)
        if self._pw_item is None:
            item = log.add("warn", "密码已重置：账号 %s 新密码 %s（约 %d 秒后自动从日志清除）" % (
                data.get("user") or "-", data.get("password") or "-",
                max(0, exp - int(time.time()))))
            self._pw_item = item.get("id") if item else None
        if exp and time.time() >= exp:
            if self._pw_item:
                log.remove(self._pw_item)
            self._pw_item = None
            try:
                fp.unlink()
            except Exception:
                pass
            log.info("重置密码的明文记录已清除")

    def gc_ctx(self):
        if len(self.ctxs) > 200:
            for k in list(self.ctxs.keys())[:100]:
                self.ctxs.pop(k, None)

    def gc_images(self):
        days = int(cfg.get("runtime", "image_keep_days", default=7) or 7)
        if days <= 0:
            return
        cutoff = time.time() - days * 86400
        for f in IMAGES.glob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                pass



# ---------------------------------------------------------------- 图片与系统信息
IMG_MAX = 8 * 1024 * 1024
_img_cache = {}


async def fetch_image_data_url(src):
    """把图片地址/本地文件转成 data URL，失败返回空串。"""
    src = str(src or "").strip()
    if not src:
        return ""
    if src in _img_cache:
        return _img_cache[src]
    try:
        if src.startswith(("http://", "https://")):
            if llm.session is None or llm.session.closed:
                llm.session = ClientSession()
            async with llm.session.get(src, timeout=20, headers={"User-Agent": "YueWuBot/" + VERSION}) as r:
                if r.status >= 400:
                    return ""
                ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if not ctype.startswith("image/"):
                    ctype = "image/jpeg"
                if int(r.headers.get("Content-Length") or 0) > IMG_MAX:
                    return ""
                blob = await r.content.read(IMG_MAX + 1)
        else:
            fp = Path(src)
            if not fp.is_file() or fp.stat().st_size > IMG_MAX:
                return ""
            blob = fp.read_bytes()
            ctype = {"png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(
                fp.suffix.lower().lstrip("."), "image/jpeg")
        if not blob:
            return ""
        url = "data:%s;base64,%s" % (ctype, base64.b64encode(blob).decode())
        if len(_img_cache) > 24:
            _img_cache.clear()
        _img_cache[src] = url
        return url
    except Exception as e:
        log.warn("图片读取失败：%s" % str(e)[:120])
        return ""


IMG_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)|<img[^>]+src=[\"'](https?://[^\"']+)[\"'][^>]*>", re.I)


def split_reply_images(text):
    """把回复里的 Markdown/HTML 图片拆出来，返回 (纯文本, 图片列表)。"""
    text = str(text or "")
    urls = []
    for m in IMG_RE.finditer(text):
        u = m.group(1) or m.group(2) or ""
        if u and u not in urls:
            urls.append(u)
    clean = IMG_RE.sub("", text).strip()
    return clean, urls


def os_info():
    name = ""
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    name = line.split("=", 1)[1].strip().strip('"')
                    break
    except Exception:
        pass
    if not name:
        name = "%s %s" % (_platform.system(), _platform.release())
    return "%s · %s" % (name, _platform.machine())


def load_text():
    try:
        a, b, c = os.getloadavg()
        return "%.2f / %.2f / %.2f" % (a, b, c)
    except Exception:
        return "-"


# ---------------------------------------------------------------- 更新检测
UPDATE_FILE = DATA / "update.json"
UPDATE_PKG_MAX = 120 * 1024 * 1024


def ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4])


def update_cache():
    d = read_json(UPDATE_FILE, {})
    return d if isinstance(d, dict) else {}


def update_save(patch):
    d = update_cache()
    d.update(patch or {})
    d["checked_at"] = int(time.time())
    write_json(UPDATE_FILE, d)
    return d


def update_is_newer(latest, current=None):
    a, b = ver_tuple(latest), ver_tuple(current or VERSION)
    return a > b


def update_line():
    d = update_cache()
    if d.get("error"):
        return "检测失败"
    if not d.get("latest"):
        return "尚未检测"
    if d.get("need") or update_is_newer(d.get("latest")):
        return "您需要更新（%s → %s）" % (VERSION, d.get("latest"))
    return "您无需更新"


async def check_update(force=False):
    d = update_cache()
    if not force and d.get("checked_at") and time.time() - int(d["checked_at"]) < 600:
        return d
    url = str(cfg.get("update", "url", default="") or "").strip()
    if not url:
        return update_save({"error": "未配置更新地址", "latest": "", "need": False})
    try:
        if llm.session is None or llm.session.closed:
            llm.session = ClientSession()
        async with llm.session.get(url, timeout=15,
                                   headers={"User-Agent": "YueWuBot/" + VERSION,
                                            "Cache-Control": "no-cache"}) as r:
            body = await r.text()
            if r.status >= 400:
                raise RuntimeError("HTTP %d" % r.status)
        data = json.loads(body)
        if not isinstance(data, dict):
            raise RuntimeError("格式错误")
        latest = str(data.get("version") or "").strip()
        if not latest:
            raise RuntimeError("缺少 version")
        notes = data.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        patch = {
            "latest": latest,
            "need": update_is_newer(latest),
            "notes": [str(x) for x in notes][:20],
            "package": str(data.get("package") or data.get("url") or ""),
            "sha256": str(data.get("sha256") or "").lower(),
            "date": str(data.get("date") or ""),
            "error": "",
        }
        out = update_save(patch)
        if patch["need"]:
            log.warn("发现新版本 %s（当前 %s）" % (latest, VERSION))
        else:
            log.info("检查更新：已是最新版本 %s" % VERSION)
        return out
    except Exception as e:
        log.warn("检查更新失败：%s" % str(e)[:120])
        return update_save({"error": str(e)[:160], "need": False})


async def apply_update():
    d = update_cache()
    if not d.get("latest"):
        d = await check_update(force=True)
    if not d.get("latest"):
        raise RuntimeError("还没有获取到版本信息")
    if not update_is_newer(d.get("latest")):
        raise RuntimeError("当前已是最新版本")
    pkg = str(d.get("package") or "").strip()
    if not pkg.startswith("https://"):
        raise RuntimeError("更新包地址无效")
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="ywup_"))
    try:
        zp = tmp / "update.zip"
        total = 0
        if llm.session is None or llm.session.closed:
            llm.session = ClientSession()
        async with llm.session.get(pkg, timeout=300,
                                   headers={"User-Agent": "YueWuBot/" + VERSION}) as r:
            if r.status >= 400:
                raise RuntimeError("下载更新包失败：HTTP %d" % r.status)
            with open(str(zp), "wb") as fh:
                async for chunk in r.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > UPDATE_PKG_MAX:
                        raise RuntimeError("更新包超过 120MB")
                    fh.write(chunk)
        want = str(d.get("sha256") or "").lower()
        if want:
            got = hashlib.sha256(zp.read_bytes()).hexdigest()
            if not hmac.compare_digest(got, want):
                raise RuntimeError("更新包校验失败，已中止")
        unz = tmp / "x"
        unz.mkdir()
        with zipfile.ZipFile(str(zp)) as zf:
            if sum(i.file_size for i in zf.infolist()) > 400 * 1024 * 1024:
                raise RuntimeError("更新包解压后过大")
            _safe_extract(zf, unz)
        root = unz
        if not (root / "yuewubot.py").is_file():
            cand = [p.parent for p in unz.rglob("yuewubot.py")]
            if not cand:
                raise RuntimeError("更新包里没有 yuewubot.py")
            root = cand[0]
        backup = DATA / "backups" / ("%s-%s" % (VERSION, time.strftime("%Y%m%d-%H%M%S")))
        backup.mkdir(parents=True, exist_ok=True)
        keep = {"data", "plugins", ".venv", "__pycache__", ".git"}
        for item in root.iterdir():
            if item.name in keep:
                continue
            dest = ROOT / item.name
            try:
                if dest.exists():
                    bak = backup / item.name
                    if dest.is_dir():
                        if bak.exists():
                            shutil.rmtree(str(bak), ignore_errors=True)
                        shutil.copytree(str(dest), str(bak))
                    else:
                        shutil.copy2(str(dest), str(bak))
            except Exception:
                pass
        for item in root.iterdir():
            if item.name in keep:
                continue
            dest = ROOT / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(str(dest), ignore_errors=True)
                shutil.copytree(str(item), str(dest))
            else:
                shutil.copy2(str(item), str(dest))
        try:
            os.chmod(str(ROOT / "start.sh"), 0o755)
            os.chmod(str(ROOT / "reset-token.sh"), 0o755)
        except Exception:
            pass
        update_save({"need": False, "installed_at": int(time.time())})
        log.ok("更新完成：%s → %s，备份在 %s" % (VERSION, d.get("latest"), backup))
        return {"from": VERSION, "to": d.get("latest"), "backup": str(backup)}
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


# ---------------------------------------------------------------- 余额
BALANCE_GUESS = (
    ("deepseek", "/user/balance"),
    ("moonshot", "/v1/users/me/balance"),
    ("siliconflow", "/v1/user/info"),
    ("openai", "/dashboard/billing/credit_grants"),
)


def _find_num(obj, keys):
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                try:
                    return float(obj[k])
                except Exception:
                    pass
        for v in obj.values():
            r = _find_num(v, keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_num(v, keys)
            if r is not None:
                return r
    return None


async def fetch_balance():
    base = str(cfg.get("llm", "base_url", default="") or "").rstrip("/")
    custom = str(cfg.get("llm", "balance_url", default="") or "").strip()
    url = custom
    if not url:
        low = base.lower()
        for key, path in BALANCE_GUESS:
            if key in low:
                url = base + path
                break
    if not url:
        return {"supported": False, "message": "该平台暂不支持余额查询，可手动填写余额接口"}
    key = str(cfg.get("llm", "api_key", default="") or "")
    if not key:
        return {"supported": False, "message": "尚未配置 API Key"}
    try:
        if llm.session is None or llm.session.closed:
            llm.session = ClientSession()
        async with llm.session.get(url, timeout=15, headers={
                "Authorization": "Bearer " + key, "User-Agent": "YueWuBot/" + VERSION}) as r:
            body = await r.text()
            status = r.status
        if status >= 400:
            return {"supported": False, "message": "接口返回 %d：%s" % (status, body[:120])}
        data = json.loads(body)
        total = _find_num(data, ("total_balance", "available_balance", "balance",
                                 "totalBalance", "availableBalance", "chargeBalance"))
        granted = _find_num(data, ("granted_balance", "voucher_balance", "gift_balance"))
        used = _find_num(data, ("used_balance", "used", "consumed"))
        currency = "CNY"
        if isinstance(data, dict):
            bi = data.get("balance_infos")
            if isinstance(bi, list) and bi and isinstance(bi[0], dict):
                currency = str(bi[0].get("currency") or "CNY")
            currency = str(data.get("currency") or currency)
        if total is None:
            return {"supported": False, "message": "返回中没有余额字段", "raw": body[:300]}
        return {"supported": True, "total": total, "granted": granted,
                "used": used, "currency": currency, "url": url,
                "checked_at": int(time.time())}
    except Exception as e:
        return {"supported": False, "message": str(e)[:160]}


# ---------------------------------------------------------------- 插件依赖
REQ_LINE_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]<>=!~+, ]{1,120}$")
PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def plugin_requirements(name):
    d = PLUGIN_DIR / name
    lines = []
    fp = d / "requirements.txt"
    if fp.is_file():
        try:
            lines = [x.strip() for x in fp.read_text("utf-8").splitlines()]
        except Exception:
            lines = []
    meta = read_json(d / "m.json", {}) or {}
    extra = meta.get("requirements") or []
    if isinstance(extra, list):
        lines += [str(x).strip() for x in extra]
    out, bad = [], []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if len(out) >= 40:
            break
        if line.startswith("-"):
            bad.append(line)
            continue
        if any(t in line for t in ("--", "://", "/", "\\", "@", ";")):
            bad.append(line)
            continue
        if not REQ_LINE_RE.match(line):
            bad.append(line)
            continue
        if line not in out:
            out.append(line)
    return out, bad


def deps_record():
    d = read_json(DATA / "plugin_deps.json", {})
    return d if isinstance(d, dict) else {}


async def install_plugin_deps(name, force=False):
    reqs, bad = plugin_requirements(name)
    if bad:
        log.warn("插件 %s 的依赖行被忽略：%s" % (name, "、".join(bad[:3])))
    if not reqs:
        return {"ok": True, "skipped": True, "requirements": []}
    digest = hashlib.sha256("\n".join(reqs).encode("utf-8")).hexdigest()[:16]
    rec = deps_record()
    if not force and rec.get(name, {}).get("digest") == digest and rec.get(name, {}).get("ok"):
        return {"ok": True, "cached": True, "requirements": reqs}
    if not bool(cfg.get("runtime", "auto_deps", default=True)):
        return {"ok": False, "disabled": True, "requirements": reqs,
                "message": "自动安装依赖已关闭"}
    mirror = str(cfg.get("runtime", "pip_mirror", default="") or "").strip() or PIP_INDEX
    d = PLUGIN_DIR / name
    req_file = d / ".yw-requirements.txt"
    try:
        req_file.write_text("\n".join(reqs) + "\n", "utf-8")
        cmd = [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file),
               "--disable-pip-version-check", "--timeout", "60", "--retries", "2",
               "-i", mirror]
        log.info("插件 %s 安装依赖：%s" % (name, "、".join(reqs)))
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 300)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("依赖安装超时")
        if proc.returncode != 0:
            msg = (err or b"").decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError((msg[-1] if msg else "pip 执行失败")[:200])
        rec[name] = {"digest": digest, "ok": True, "at": int(time.time()), "requirements": reqs}
        write_json(DATA / "plugin_deps.json", rec)
        log.ok("插件 %s 依赖安装完成" % name)
        return {"ok": True, "installed": True, "requirements": reqs}
    except Exception as e:
        rec[name] = {"digest": digest, "ok": False, "at": int(time.time()),
                     "error": str(e)[:200], "requirements": reqs}
        write_json(DATA / "plugin_deps.json", rec)
        log.error("插件 %s 依赖安装失败：%s" % (name, str(e)[:160]))
        return {"ok": False, "error": str(e)[:200], "requirements": reqs}
    finally:
        try:
            req_file.unlink()
        except Exception:
            pass


def status_text():
    up = max(0, int(time.time() - STARTED_AT))
    state = "运行中" if bot.platforms.primary().online else "待连接"
    return ("YueWuBot信息\n"
            "运行时间: %d天%d时%d分%d秒\n"
            "运行状态: %s\n"
            "操作系统: %s\n"
            "当前负载: %s · 内存 %sMB\n"
            "当前版本: %s\n"
            "更新检测: %s") % (
        up // 86400, up % 86400 // 3600, up % 3600 // 60, up % 60,
        state, os_info(), load_text(), rss_mb(), VERSION, update_line())


bot = Bot()


MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def jok(data=None, **kw):
    out = {"ok": True}
    if isinstance(data, dict):
        out.update(data)
    elif data is not None:
        out["data"] = data
    out.update(kw)
    return web.json_response(out)


def jerr(msg, status=400):
    return web.json_response({"ok": False, "error": str(msg)}, status=status)


@web.middleware
async def auth_mw(request, handler):
    p = request.path
    if not p.startswith("/api/"):
        return await handler(request)
    if p in ("/api/auth/login", "/api/auth/state", "/api/auth/setup"):
        return await handler(request)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        auth = auth[7:]
    if not auth:
        auth = request.query.get("t", "")
    if not sessions.valid(auth):
        return jerr("登录已失效，请重新登录", 401)
    return await handler(request)


_login_guard = {}


def _guard_ok(ip):
    now = time.time()
    items = [t for t in _login_guard.get(ip, []) if now - t < 60]
    _login_guard[ip] = items
    return len(items) < 8


def _guard_hit(ip):
    _login_guard.setdefault(ip, []).append(time.time())


async def api_auth_state(request):
    return jok({
        "initialized": auth.initialized(),
        "mode": "token",
        "need_login": True,
        "version": VERSION,
        "bot": cfg.get("platform", "self_id", default=""),
    })


async def api_setup(request):
    if auth.initialized():
        return jerr("访问令牌已存在，请直接登录", 403)
    tok = auth.set_token()
    s = sessions.create("admin")
    log.ok("已生成控制台访问令牌")
    return jok({"session": s, "token": tok, "created": True, "auth_user": "admin"})


async def api_login(request):
    ip = request.remote or "-"
    if not _guard_ok(ip):
        return jerr("尝试过于频繁，请稍后再试", 429)
    try:
        body = await request.json()
    except Exception:
        return jerr("请求格式错误")
    tok = str(body.get("token") or body.get("password") or "").strip()
    if not auth.initialized():
        return jerr("尚未生成访问令牌，请重启一次服务", 403)
    if not auth.verify(tok):
        _guard_hit(ip)
        log.warn("控制台登录失败，来源 %s" % ip)
        return jerr("访问令牌不正确", 401)
    s = sessions.create("admin")
    log.ok("控制台登录成功，来源 %s" % ip)
    return jok({"session": s, "auth_user": "admin"})


async def api_rotate_token(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        tok = auth.set_token(str(body.get("token") or "").strip() or None)
    except ValueError as e:
        return jerr(str(e))
    sessions.clear()
    new_s = sessions.create("admin")
    log.ok("控制台访问令牌已更新")
    return jok({"token": tok, "session": new_s, "auth_user": "admin"})


async def api_token_show(request):
    return jok({"token": auth.token()})


async def api_logout(request):
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    sessions.revoke(auth)
    return jok()


async def api_onboard(request):
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        done = bool(body.get("done", True))
        cfg.update({"ui": {"onboard_done": done, "onboard_at": int(time.time())}})
        return jok({"done": done})
    return jok({"done": bool(cfg.get("ui", "onboard_done", default=False))})


async def api_overview(request):
    up = int(time.time() - bot.started_at)
    primary = bot.ob
    platforms = bot.platforms.snapshots()
    return jok({
        "version": VERSION,
        "uptime": up,
        "uptime_text": "%d天%d时%d分%d秒" % (up // 86400, up % 86400 // 3600, up % 3600 // 60, up % 60),
        "uptime_short": "%d天%d时%d分" % (up // 86400, up % 86400 // 3600, up % 3600 // 60),
        "rss_mb": rss_mb(),
        "online": primary.online,
        "self_id": primary.self_id or cfg.get("platform", "self_id", default=""),
        "nickname": primary.nickname,
        "platform_mode": "client",
        "platform_url": "",
        "platform_name": primary.name,
        "platforms": platforms,
        "platform_online": sum(1 for x in platforms if x.get("online")),
        "platform_total": len(platforms),
        "model": cfg.get("llm", "model", default=""),
        "has_key": bool(cfg.get("llm", "api_key", default="")),
        "stats": bot.stats,
        "plugins": {"total": len(bot.plugins.catalog or bot.plugins.refresh_catalog()),
                    "loaded": bot.plugins.loaded_count()},
        "llm_ms": llm.last_ms,
        "last_activity": int(bot.last_activity),
        "os": os_info(),
        "load": load_text(),
        "protocol": llm.protocol,
        "auth_user": auth.username(),
        "onboard_done": bool(cfg.get("ui", "onboard_done", default=False)),
        "update": update_cache(),
        "platform_kinds": [{"id": k, "title": KIND_TITLES[k]} for k in PLATFORM_KINDS],
    })


async def api_config_get(request):
    data = json.loads(json.dumps(cfg.data))
    data.pop("token", None)
    data.pop("auth", None)
    llm = data.get("llm") or {}
    if llm.get("api_key"):
        llm["api_key"] = "__KEEP__"
    data["llm"] = llm
    upd = data.get("update") or {}
    upd.pop("sha256", None)
    data["update"] = upd
    return jok({"config": data})


async def api_config_set(request):
    try:
        patch = await request.json()
    except Exception:
        return jerr("请求格式错误")
    patch.pop("token", None)
    patch.pop("auth", None)
    llm = patch.get("llm")
    if isinstance(llm, dict):
        if str(llm.get("api_key") or "") in ("__KEEP__", ""):
            llm.pop("api_key", None)
    cfg.update(patch)
    if "platform" in patch:
        await bot.platforms.reload()
    log.ok("配置已更新")
    out = json.loads(json.dumps(cfg.data))
    out.pop("token", None)
    out.pop("auth", None)
    if (out.get("llm") or {}).get("api_key"):
        out["llm"]["api_key"] = "__KEEP__"
    return jok({"config": out})


async def api_platforms(request):
    return jok({"connections": bot.platforms.snapshots(), "active": bot.platforms.snapshots(),
                "kinds": [{"id": k, "title": KIND_TITLES[k]} for k in PLATFORM_KINDS]})


async def api_platforms_save(request):
    try:
        body = await request.json()
    except Exception:
        return jerr("请求格式错误")
    items = body.get("connections")
    if not isinstance(items, list):
        return jerr("connections 必须是数组")
    if len(items) > 20:
        return jerr("最多支持 20 条连接")
    old = {x["id"]: x for x in bot.platforms.read_entries()}
    out, seen = [], set()

    def keep(val, prev):
        val = str(val or "").strip()
        if val in ("__KEEP__", ""):
            return prev
        return val

    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        cid = slugify(raw.get("id") or ("conn-%d" % (i + 1)), "conn-%d" % (i + 1))
        if cid in seen:
            return jerr("连接 ID 重复：%s" % cid)
        seen.add(cid)
        prev = old.get(cid, {})
        app_id = keep(raw.get("app_id"), prev.get("app_id"))
        app_secret = keep(raw.get("app_secret"), prev.get("app_secret"))
        if not app_id or not app_secret:
            return jerr("QQ 官方机器人 %s 需要 AppID 与 AppSecret" % cid)
        try:
            intents = int(raw.get("intents") or prev.get("intents") or (1 << 25))
        except Exception:
            intents = 1 << 25
        out.append({
            "id": cid, "name": str(raw.get("name") or cid), "kind": "qq",
            "direction": "client", "url": "", "access_token": "",
            "enabled": bool(raw.get("enabled", True)),
            "self_id": str(raw.get("self_id") or prev.get("self_id") or ""),
            "app_id": app_id, "app_secret": app_secret, "token": "", "intents": intents,
        })
    if not out:
        return jerr("至少保留一条连接")
    cfg.update({"platform": {"links": out, "mode": "client", "url": "", "access_token": ""}})
    await bot.platforms.reload()
    log.ok("平台连接配置已更新")
    return jok({"connections": bot.platforms.snapshots(), "active": bot.platforms.snapshots()})


async def api_platforms_reconnect(request):
    await bot.platforms.reload()
    log.warn("平台连接已重启")
    return jok({"active": bot.platforms.snapshots()})


async def api_plugins(request):
    return jok({"plugins": bot.plugins.refresh_catalog(), "commands": bot.plugins.all_commands(),
                "admins": cfg.get("admins", default=[]) or []})


def _safe_extract(zf, dest):
    dest = dest.resolve()
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            continue
        target = (dest / name).resolve()
        try:
            target.relative_to(dest)
        except Exception:
            continue
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(str(target), "wb") as out:
            shutil.copyfileobj(src, out)


def _zip_install(blob, source_name="upload.zip"):
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="ywpz_"))
    try:
        zpath = tmp / "pkg.zip"
        zpath.write_bytes(blob)
        try:
            zf = zipfile.ZipFile(str(zpath))
        except Exception:
            raise RuntimeError("不是有效的 zip 文件")
        total = sum(i.file_size for i in zf.infolist())
        if total > 10 * 1024 * 1024:
            raise RuntimeError("压缩包解压后超过 10MB")
        if len(zf.infolist()) > 300:
            raise RuntimeError("压缩包文件数过多")
        _safe_extract(zf, tmp / "x")
        base = None
        if (tmp / "x" / "m.json").is_file():
            base = tmp / "x"
        else:
            for d in (tmp / "x").iterdir():
                if d.is_dir() and (d / "m.json").is_file():
                    base = d
                    break
        if base is None:
            raise RuntimeError("压缩包里没有 m.json")
        if not (base / "main.py").is_file():
            raise RuntimeError("压缩包里没有 main.py")
        meta = read_json(base / "m.json", None)
        if not isinstance(meta, dict):
            raise RuntimeError("m.json 不是合法的 JSON 对象")
        for f in base.rglob("*"):
            if f.is_file() and (f.name.startswith(".") or "__pycache__" in str(f)):
                try:
                    f.unlink()
                except Exception:
                    pass
        name = slugify(meta.get("name") or Path(source_name).stem, "plugin")
        dest = PLUGIN_DIR / name
        if dest.exists():
            shutil.rmtree(str(dest))
        shutil.copytree(str(base), str(dest))
        meta.setdefault("name", name)
        write_json(dest / "m.json", meta)
        pmeta = read_json(DATA / "plugins.json", {})
        pmeta.setdefault(name, {})["enabled"] = True
        pmeta[name]["installed_at"] = ts()
        write_json(DATA / "plugins.json", pmeta)
        bot.plugins.meta = pmeta
        bot.plugins.refresh_catalog()
        bot.plugins.load(name)
        fire(bot.plugins.call_hook(name, "on_startup", bot))
        return name, meta
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


async def api_plugin_upload(request):
    reader = await request.multipart()
    blob = None
    fname = "upload.zip"
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "file":
            fname = part.filename or fname
            blob = await part.read()
            break
    if not blob:
        return jerr("没有收到文件")
    try:
        name, meta = _zip_install(blob, fname)
    except Exception as e:
        return jerr(str(e))
    log.ok("插件已安装：%s" % name)
    deps = await install_plugin_deps(name)
    return jok({"name": name, "title": meta.get("name"), "deps": deps})


async def api_plugin_toggle(request):
    name = request.match_info["name"]
    body = await request.json()
    enabled = bool(body.get("enabled"))
    if not (PLUGIN_DIR / name).is_dir():
        return jerr("插件不存在", 404)
    bot.plugins.set_enabled(name, enabled)
    return jok({"enabled": enabled})


async def api_plugin_delete(request):
    name = request.match_info["name"]
    d = PLUGIN_DIR / name
    if not d.is_dir():
        return jerr("插件不存在", 404)
    bot.plugins.unload(name)
    shutil.rmtree(str(d), ignore_errors=True)
    meta = read_json(DATA / "plugins.json", {})
    meta.pop(name, None)
    write_json(DATA / "plugins.json", meta)
    bot.plugins.meta = meta
    bot.plugins.refresh_catalog()
    log.warn("插件已删除：%s" % name)
    return jok()


async def api_plugin_icon(request):
    name = request.match_info["name"]
    d = PLUGIN_DIR / name
    meta = read_json(d / "m.json", {})
    icon = d / (meta.get("icon") or "icon.png")
    if not icon.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(str(icon))


async def api_plugin_config(request):
    name = request.match_info["name"]
    if not (PLUGIN_DIR / name / "m.json").is_file():
        return jerr("插件不存在", 404)
    if request.method == "GET":
        meta = read_json(PLUGIN_DIR / name / "m.json", {})
        return jok({"schema": meta.get("config") or {}, "values": bot.plugins.plugin_config(name)})
    body = await request.json()
    bot.plugins.save_plugin_config(name, body.get("values") or {})
    mod = bot.plugins.get(name)
    if mod and hasattr(mod, "on_config_changed"):
        try:
            r = mod.on_config_changed(bot.plugins.plugin_config(name))
            if asyncio.iscoroutine(r):
                await r
        except Exception as e:
            log.warn("插件配置回调失败：%s" % e)
    return jok({"values": bot.plugins.plugin_config(name)})


async def api_admins(request):
    if request.method == "GET":
        return jok({"admins": cfg.get("admins", default=[]) or []})
    try:
        body = await request.json()
    except Exception:
        return jerr("请求格式错误")
    raw = body.get("admins")
    if not isinstance(raw, list):
        return jerr("admins 必须是数组")
    out = []
    for x in raw:
        s = str(x).strip()
        if s.isdigit() and 4 <= len(s) <= 16 and s not in out:
            out.append(s)
    if len(out) > 50:
        return jerr("最多 50 个管理员")
    cfg.data["admins"] = out
    cfg.save()
    log.ok("管理员列表已更新，共 %d 人" % len(out))
    return jok({"admins": out})


async def api_plugin_admin(request):
    name = request.match_info["name"]
    if not (PLUGIN_DIR / name).is_dir():
        return jerr("插件不存在", 404)
    try:
        body = await request.json()
    except Exception:
        return jerr("请求格式错误")
    value = bool(body.get("admin_only"))
    bot.plugins.set_admin_only(name, value)
    log.ok("插件 %s 管理员限制：%s" % (name, "开启" if value else "关闭"))
    return jok({"admin_only": value})


async def api_logs(request):
    try:
        since = int(request.query.get("since", "0"))
    except Exception:
        since = 0
    items = [x for x in log.buf if x["id"] > since]
    return jok({"items": items[-300:], "seq": log.seq})


async def api_logs_stream(request):
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)
    q = asyncio.Queue()
    log.subs.add(q)
    try:
        for item in log.tail(80):
            await resp.write(("data: " + json.dumps(item, ensure_ascii=False) + "\n\n").encode())
        while True:
            try:
                item = await asyncio.wait_for(q.get(), 25)
                await resp.write(("data: " + json.dumps(item, ensure_ascii=False) + "\n\n").encode())
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
    except Exception:
        pass
    finally:
        log.subs.discard(q)
    return resp


async def api_test_chat(request):
    body = await request.json()
    messages = body.get("messages") or []
    system = cfg.get("llm", "system_prompt", default=DEFAULT_PERSONA)
    msgs = [{"role": "system", "content": system}]
    for m in messages[-12:]:
        content = str(m.get("content", ""))
        imgs = m.get("images") or []
        if imgs:
            parts = [{"type": "text", "text": content or "看看这张图"}]
            for src in imgs[:4]:
                data = await fetch_image_data_url(src)
                if data:
                    parts.append({"type": "image_url", "image_url": {"url": data}})
            if len(parts) > 1:
                content = parts
        msgs.append({"role": m.get("role", "user"), "content": content})
    try:
        reply = await llm.chat(msgs)
    except Exception as e:
        return jerr(str(e), 500)
    return jok({"reply": reply, "ms": llm.last_ms})


async def api_llm_probe(request):
    body = await request.json()
    base = (body.get("base_url") or "").strip().rstrip("/")
    key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()
    proto = str(body.get("protocol") or "openai").strip().lower()
    anth = proto.startswith("anthropic") or proto in ("claude", "messages")
    if not base or not key or not model:
        return jerr("请先填写地址、Key 和模型")
    if anth:
        if base.endswith("/messages"):
            url = base
        elif base.endswith("/v1"):
            url = base + "/messages"
        else:
            url = base + "/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
        payload = {"model": model, "max_tokens": 16,
                   "messages": [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]}
    else:
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8}
    if llm.session is None:
        llm.session = ClientSession()
    last = "模型连接失败"
    for i in range(3):
        try:
            async with llm.session.post(url, headers=headers, json=payload, timeout=8) as r:
                txt = await r.text()
                if r.status >= 400:
                    last = "HTTP %s: %s" % (r.status, txt[:200])
                    if r.status < 500:
                        return jerr(last)
                    await asyncio.sleep(0.35 * (i + 1))
                    continue
            try:
                data = json.loads(txt)
                if anth:
                    blocks = data.get("content") or []
                    reply = "".join(str(b.get("text") or "") for b in blocks
                                    if isinstance(b, dict) and b.get("type") == "text").strip() or "OK"
                else:
                    reply = (data["choices"][0]["message"]["content"] or "").strip() or "OK"
            except Exception:
                reply = txt[:200]
            return jok({"reply": reply[:200], "protocol": "anthropic" if anth else "openai"})
        except Exception as e:
            last = str(e)[:200]
            await asyncio.sleep(0.35 * (i + 1))
    return jerr(last, 500)


async def api_restart(request):
    log.warn("收到重启指令，2 秒后退出由 systemd 拉起")
    asyncio.get_event_loop().call_later(2, lambda: os._exit(3))
    return jok()


async def api_send_test(request):
    body = await request.json()
    target = str(body.get("target") or "").strip()
    text = str(body.get("text") or "").strip()
    mtype = body.get("type") or ("group" if body.get("group_id") else "private")
    if not target:
        return jerr("缺少目标")
    try:
        if mtype == "image":
            if not text:
                return jerr("缺少图片地址")
            await bot.ob.send_image("group" if body.get("group_id") else "private", target, text)
        else:
            if not text:
                return jerr("缺少内容")
            await bot.ob.send_text("group" if body.get("group_id") else "private", target, text)
        return jok()
    except Exception as e:
        return jerr(str(e), 500)



async def api_update(request):
    if request.method == "POST":
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        if body.get("check"):
            data = await check_update(force=True)
            return jok({"update": data, "current": VERSION})
    return jok({"update": update_cache(), "current": VERSION})


async def api_update_apply(request):
    try:
        info = await apply_update()
    except Exception as e:
        log.error("更新失败：%s" % str(e)[:160])
        return jerr(str(e), 500)
    log.warn("更新完成，2 秒后重启")
    get_loop().call_later(2, lambda: os._exit(3))
    return jok({"applied": True, "info": info})


async def api_balance(request):
    data = await fetch_balance()
    return jok({"balance": data, "current": VERSION})


async def api_plugin_deps(request):
    name = request.match_info["name"]
    if not (PLUGIN_DIR / name).is_dir():
        return jerr("插件不存在", 404)
    if request.method == "GET":
        reqs, bad = plugin_requirements(name)
        rec = deps_record().get(name, {})
        return jok({"requirements": reqs, "invalid": bad, "record": rec})
    try:
        body = await request.json()
    except Exception:
        body = {}
    res = await install_plugin_deps(name, force=bool(body.get("force")))
    return jok({"result": res})


def _wx_xml_reply(text):
    return text


async def index_handler(request):
    return web.Response(body=_index_html(), headers={"Content-Type": "text/html; charset=utf-8",
                                                     "Cache-Control": "no-store"})


def _ver():
    try:
        return str(int((WEB / "app.js").stat().st_mtime))
    except Exception:
        return "1"


def _index_html():
    return (WEB / "index.html").read_text("utf-8").replace("__V__", _ver())


async def static_handler(request):
    if request.path.startswith("/api/"):
        return jerr("接口不存在", 404)
    rel = request.match_info.get("path", "")
    if rel in ("docs", "docs/"):
        rel = "docs.html"
    fp = (WEB / rel).resolve()
    try:
        fp.relative_to(WEB.resolve())
    except Exception:
        raise web.HTTPForbidden()
    if fp.is_dir():
        fp = fp / "index.html"
    if not fp.is_file():
        if "." in Path(rel).name:
            raise web.HTTPNotFound()
        fp = WEB / "index.html"
    ctype = MIME.get(fp.suffix.lower(), "application/octet-stream")
    if fp.suffix.lower() in (".html", ".js", ".css"):
        body = fp.read_bytes()
        try:
            body = body.decode("utf-8").replace("__V__", _ver()).encode("utf-8")
        except Exception:
            pass
        return web.Response(body=body, headers={"Content-Type": ctype, "Cache-Control": "no-store"})
    return web.Response(body=fp.read_bytes(), headers={"Content-Type": ctype, "Cache-Control": "no-cache"})


# ---------------------------------------------------------------- 插件市场
MARKET_KIND = "pjson"
MARKET_URL = os.environ.get("YW_MARKET_URL") or "https://chumianyi.github.io/p.json"
MARKET_LABEL = "YueWuBot 官方插件仓库"
MARKET_TIMEOUT = 20
_mkt_session = None


def _mkt_client():
    global _mkt_session
    if _mkt_session is None or _mkt_session.closed:
        _mkt_session = ClientSession()
    return _mkt_session


def _mkt_url(page, size, q):
    import urllib.parse
    if MARKET_KIND == "astrbot":
        url = MARKET_URL + ("&" if "?" in MARKET_URL else "?")
        url += "page=%d&page_size=%d" % (page, size)
        if q:
            url += "&search=" + urllib.parse.quote(q)
        return url
    return MARKET_URL


async def _mkt_get(url):
    if not str(url).startswith("https://"):
        raise RuntimeError("插件索引地址必须是 https")
    try:
        async with _mkt_client().get(url, timeout=MARKET_TIMEOUT,
                                     headers={"User-Agent": "YueWuBot/" + VERSION}) as r:
            body = await r.text()
            if r.status >= 400:
                raise RuntimeError("拉取插件索引失败：HTTP %d" % r.status)
    except asyncio.TimeoutError:
        raise RuntimeError("拉取插件索引超时")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError("拉取插件索引失败：%s" % str(e)[:120])
    try:
        return json.loads(body)
    except Exception:
        raise RuntimeError("插件索引不是合法 JSON")


def _mkt_installed(item):
    if MARKET_KIND == "astrbot":
        name = str(item.get("name") or "")
        if name and (ASTRBOT_DIR / name).is_dir():
            return name
        cat = bot.astrbot.catalog or bot.astrbot.scan()
        for k in cat:
            if k.lower() == name.lower():
                return k
        return ""
    name = slugify(str(item.get("dir") or item.get("id") or ""), "")
    if name and (PLUGIN_DIR / name).is_dir():
        return name
    return ""


def _mkt_norm(data, q=""):
    items, total = [], 0
    if MARKET_KIND == "astrbot":
        d = (data or {}).get("data") or {}
        total = int(d.get("total") or 0)
        raw = d.get("items") or []
        for x in raw:
            if not isinstance(x, dict):
                continue
            pid = str(x.get("plugin_id") or x.get("slug") or x.get("plugin_name") or "")
            items.append({
                "id": pid,
                "name": str(x.get("plugin_name") or x.get("name") or pid),
                "title": str(x.get("display_name") or x.get("plugin_name") or pid),
                "desc": str(x.get("description") or ""),
                "author": str(x.get("author_name") or x.get("author") or ""),
                "version": str(x.get("version") or ""),
                "repo": str(x.get("repository_url") or ""),
                "download": str(x.get("download_url") or ""),
                "dir": "",
                "branch": "",
                "icon": str(x.get("logo_url") or ""),
                "tags": [str(t) for t in (x.get("tags") or [])][:4],
            })
    else:
        for x in (data or {}).get("plugins") or []:
            if not isinstance(x, dict):
                continue
            pid = str(x.get("id") or x.get("name") or "").strip()
            repo = str(x.get("repo") or x.get("repository") or "").strip()
            if not pid or not repo:
                continue
            items.append({
                "id": pid,
                "name": pid,
                "title": str(x.get("title") or x.get("name") or pid),
                "desc": str(x.get("description") or x.get("desc") or ""),
                "author": str(x.get("author") or "YueWu"),
                "version": str(x.get("version") or "1.0.0"),
                "repo": repo,
                "download": "",
                "dir": str(x.get("dir") or ""),
                "branch": str(x.get("branch") or ""),
                "icon": str(x.get("icon") or ""),
                "tags": [str(t) for t in (x.get("tags") or [])][:4],
            })
        total = len(items)
        if q:
            low = q.lower()
            items = [i for i in items if low in (i["title"] + i["name"] + i["desc"]).lower()]
            total = len(items)
    for it in items:
        got = _mkt_installed(it)
        it["installed"] = bool(got)
        it["installed_as"] = got
    return items, total


async def _git_run(args, cwd=None, timeout=180):
    try:
        env = dict(os.environ)
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", "1000")
        env.setdefault("GIT_HTTP_LOW_SPEED_TIME", "20")
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=str(cwd) if cwd else None, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError("本机没有安装 git")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError("git 操作超时")
    if proc.returncode != 0:
        lines = (err or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError((lines[-1] if lines else "git 执行失败")[:180])
    return (out or b"").decode("utf-8", "replace")


async def _mkt_download(url, dest, limit=40 * 1024 * 1024):
    if not str(url).startswith("https://"):
        raise RuntimeError("下载地址必须是 https")
    async with _mkt_client().get(url, timeout=120, headers={"User-Agent": "YueWuBot/" + VERSION}) as r:
        if r.status >= 400:
            raise RuntimeError("下载插件包失败：HTTP %d" % r.status)
        total = 0
        with open(str(dest), "wb") as fh:
            async for chunk in r.content.iter_chunked(65536):
                total += len(chunk)
                if total > limit:
                    raise RuntimeError("插件包过大（超过 40MB）")
                fh.write(chunk)
    return total


def _mkt_zipball(repo, branch):
    m = re.match(r"^https://github\.com/([^/]+)/([^/#?]+)", str(repo))
    if not m:
        raise RuntimeError("只能用 GitHub 仓库地址")
    return "https://codeload.github.com/%s/%s/zip/refs/heads/%s" % (m.group(1), m.group(2), branch or "main")


def _mkt_find_dir(root, marker, hint=""):
    root = Path(root)
    cand = []
    if hint:
        cand += [root / hint, root / "plugins" / hint]
        if root.is_dir():
            for d in root.iterdir():
                if d.is_dir():
                    cand += [d / hint, d / "plugins" / hint, d]
    for c in cand:
        if (c / marker).is_file():
            return c
    if (root / marker).is_file():
        return root
    for p in sorted(root.rglob(marker)):
        return p.parent
    return None


def _mkt_clean(base):
    for f in Path(base).rglob("*"):
        if f.is_file() and (f.name.startswith(".") or "__pycache__" in str(f)):
            try:
                f.unlink()
            except Exception:
                pass


async def api_market(request):
    q = (request.query.get("q") or "").strip()
    try:
        page = max(1, int(request.query.get("page") or 1))
    except Exception:
        page = 1
    try:
        size = min(48, max(6, int(request.query.get("size") or 24)))
    except Exception:
        size = 24
    try:
        data = await _mkt_get(_mkt_url(page, size, q))
    except Exception as e:
        return jerr(str(e), 502)
    items, total = _mkt_norm(data, q)
    return jok({"items": items, "total": total, "page": page, "size": size,
                "source": MARKET_LABEL, "kind": MARKET_KIND})


async def api_market_install(request):
    import tempfile
    try:
        body = await request.json()
    except Exception:
        return jerr("请求格式错误")
    item = body.get("item") if isinstance(body, dict) else None
    if not isinstance(item, dict):
        return jerr("缺少插件信息")
    repo = str(item.get("repo") or "").strip()
    if not repo.startswith("https://"):
        return jerr("插件仓库地址无效")
    hint = str(item.get("dir") or "").strip()
    name_hint = slugify(str(item.get("id") or item.get("name") or ""), "")
    if not name_hint:
        return jerr("插件标识无效")

    already = _mkt_installed(item)
    if already:
        return jok({"name": already, "installed": True, "already": True})

    marker = "metadata.yaml" if MARKET_KIND == "astrbot" else "m.json"
    tmp = Path(tempfile.mkdtemp(prefix="ywmk_"))
    log.info("开始安装插件：%s" % (item.get("title") or name_hint))
    try:
        src = tmp / "src"
        mode = "zip"  # [patch: zip-first] 压缩包优先，git 兜底
        try:
            url = item.get("download") or _mkt_zipball(repo, item.get("branch") or "main")
            zp = tmp / "pkg.zip"
            await _mkt_download(url, zp)
            unz = tmp / "unz"
            unz.mkdir()
            with zipfile.ZipFile(str(zp)) as zf:
                total = sum(i.file_size for i in zf.infolist())
                if total > 40 * 1024 * 1024:
                    raise RuntimeError("插件包解压后超过 40MB")
                _safe_extract(zf, unz)
            src = unz
            if _mkt_find_dir(src, marker, hint) is None:
                raise RuntimeError("压缩包里没有找到 %s" % marker)
        except Exception as e:
            log.warn("压缩包安装失败（%s），改用 git 克隆" % str(e)[:80])
            mode = "git"
            src = tmp / "src"
            await _git_run(["clone", "--depth", "1", "--single-branch"] +
                           (["-b", str(item.get("branch"))] if item.get("branch") else []) +
                           [repo, str(src)], timeout=45)
        base = _mkt_find_dir(src, marker, hint)
        if base is None:
            raise RuntimeError("仓库里没有找到 %s" % marker)
        if not (base / "main.py").is_file():
            raise RuntimeError("插件缺少 main.py")
        name = _mkt_install_plugin(base, item, repo)
        deps = await install_plugin_deps(name)
        log.ok("插件已安装：%s（%s）" % (name, mode))
        return jok({"name": name, "installed": True, "mode": mode, "deps": deps})
    except Exception as e:
        log.error("插件安装失败 %s：%s" % (name_hint, str(e)[:160]))
        return jerr(str(e)[:200])
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def _mkt_install_plugin(base, item, repo):
    _mkt_clean(base)
    meta = read_json(base / "m.json", {})
    if not isinstance(meta, dict):
        meta = {}
    name = slugify(str(item.get("dir") or item.get("id") or ""), "")
    if not name:
        name = slugify(meta.get("name") or "plugin", "plugin")
    dest = PLUGIN_DIR / name
    if dest.exists():
        shutil.rmtree(str(dest))
    shutil.copytree(str(base), str(dest))
    meta.setdefault("name", name)
    meta["source"] = "market"
    write_json(dest / "m.json", meta)
    pmeta = read_json(DATA / "plugins.json", {})
    pmeta.setdefault(name, {})["enabled"] = True
    pmeta[name]["installed_at"] = ts()
    pmeta[name]["source"] = "market"
    write_json(DATA / "plugins.json", pmeta)
    bot.plugins.meta = pmeta
    bot.plugins.refresh_catalog()
    bot.plugins.load(name)
    fire(bot.plugins.call_hook(name, "on_startup", bot))
    return name

def build_app():
    app = web.Application(middlewares=[auth_mw], client_max_size=32 * 1024 * 1024)
    r = app.router
    r.add_get("/api/platforms", api_platforms)
    r.add_post("/api/platforms", api_platforms_save)
    r.add_post("/api/platforms/reconnect", api_platforms_reconnect)
    r.add_get("/api/auth/state", api_auth_state)
    r.add_post("/api/auth/login", api_login)
    r.add_post("/api/auth/logout", api_logout)
    r.add_get("/api/overview", api_overview)
    r.add_get("/api/config", api_config_get)
    r.add_post("/api/config", api_config_set)
    r.add_get("/api/onboard", api_onboard)
    r.add_post("/api/onboard", api_onboard)
    r.add_post("/api/auth/setup", api_setup)
    r.add_get("/api/auth/token", api_token_show)
    r.add_post("/api/auth/token", api_rotate_token)
    r.add_get("/api/update", api_update)
    r.add_post("/api/update", api_update)
    r.add_post("/api/update/apply", api_update_apply)
    r.add_get("/api/balance", api_balance)
    r.add_get("/api/plugins/{name}/deps", api_plugin_deps)
    r.add_post("/api/plugins/{name}/deps", api_plugin_deps)
    r.add_get("/api/plugins", api_plugins)
    r.add_post("/api/plugins/upload", api_plugin_upload)
    r.add_get("/api/market", api_market)
    r.add_post("/api/market/install", api_market_install)
    r.add_post("/api/plugins/{name}/toggle", api_plugin_toggle)
    r.add_post("/api/plugins/{name}/admin", api_plugin_admin)
    r.add_get("/api/admins", api_admins)
    r.add_post("/api/admins", api_admins)
    r.add_delete("/api/plugins/{name}", api_plugin_delete)
    r.add_get("/api/plugins/{name}/icon", api_plugin_icon)
    r.add_get("/api/plugins/{name}/config", api_plugin_config)
    r.add_post("/api/plugins/{name}/config", api_plugin_config)
    r.add_get("/api/logs", api_logs)
    r.add_get("/api/logs/stream", api_logs_stream)
    r.add_post("/api/chat", api_test_chat)
    r.add_post("/api/llm/probe", api_llm_probe)
    r.add_post("/api/send", api_send_test)
    r.add_post("/api/restart", api_restart)
    r.add_get("/", index_handler)
    r.add_get("/{path:.*}", static_handler)
    r.add_post("/{path:.*}", static_handler)
    return app


async def update_watcher():
    await asyncio.sleep(8)
    while True:
        try:
            if bool(cfg.get("update", "auto_check", default=True)):
                await check_update(force=True)
        except Exception as e:
            log.warn("更新巡检异常：%s" % str(e)[:120])
        await asyncio.sleep(3 * 3600)


async def amain():
    loop = asyncio.get_event_loop()
    app = build_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    for h in [x.strip() for x in HOST.split(",") if x.strip()]:
        await web.TCPSite(runner, h, PORT).start()
    log.ok("YueWuBot v%s 已启动，控制台监听 %s:%d" % (VERSION, HOST, PORT))
    tok = auth.ensure_token()
    log.info("控制台访问令牌：%s" % tok)
    log.info("令牌文件：%s" % (DATA / "TOKEN.txt"))

    try:
        await bot.plugins.install_all_deps()
    except Exception as e:
        log.warn("插件依赖检查失败：%s" % str(e)[:120])
    bot.plugins.start_all()
    bot.platforms.start_all()
    loop.create_task(bot.maintain())
    loop.create_task(update_watcher())

    stop = loop.create_future()

    def _bye():
        if not stop.done():
            stop.set_result(1)

    for sig in (2, 15):
        try:
            loop.add_signal_handler(sig, _bye)
        except NotImplementedError:
            pass
    await stop
    log.warn("正在退出……")
    try:
        for name in list(bot.plugins.mods.keys()):
            bot.plugins.call_hook(name, "on_shutdown", bot)
    except Exception:
        pass
    for link in bot.platforms.links:
        try:
            await link.stop()
        except Exception:
            pass
    await runner.cleanup()


def cli_token(argv):
    """命令行管理访问令牌：--show-token / --set-token [值] / --reset-token"""
    mode = "show"
    value = ""
    for a in list(argv)[1:]:
        x = str(a)
        if x in ("--show-token", "--token", "--account"):
            if mode != "set":
                mode = "show"
        elif x in ("--reset-token", "--reset-password"):
            mode = "reset"
        elif x in ("--set-token", "--set-account"):
            mode = "set"
        elif x.startswith("--set-token="):
            mode = "set"
            value = x.split("=", 1)[1].strip()
        elif not x.startswith("-") and mode == "set" and not value:
            value = x.strip()

    cur = auth.token()
    if mode == "show" and cur and not Auth.token_errors(cur):
        tok = cur
    else:
        try:
            tok = auth.set_token(value)
        except ValueError as e:
            print("  × %s" % e)
            return 1

    line = "=" * 64
    print()
    print(line)
    print("  YueWuBot 控制台访问令牌")
    print("  令牌：%s" % tok)
    print("  长度：30 位，字母与数字混合")
    print("  登录：打开控制台，把这个令牌粘进去即可")
    print("  重置：./start.sh token --reset")
    print(line)
    print()
    return 0


if __name__ == "__main__":
    _tok_flags = ("--set-token", "--show-token", "--reset-token", "--token",
                  "--set-account", "--reset-password", "--account")
    if any(x in sys.argv for x in _tok_flags) or any(
            str(x).startswith("--set-token=") for x in sys.argv):
        sys.exit(cli_token(sys.argv))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(amain())
    except KeyboardInterrupt:
        pass
