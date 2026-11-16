"""投稿插件（本服专用，不对外发布）

用法：
  /投稿 插件名称 插件功能     或  投稿 插件名称 插件功能
  /全部投稿                   管理员查看全部投稿
  /已完成 编号                管理员结项
  /未通过 编号                管理员驳回
"""

import json
import re
import time
from pathlib import Path

DEFAULT_ADMIN = "3835347820"
DB = Path(__file__).parent / "data" / "submissions.json"

NAME_MAX = 32
DESC_MAX = 300
LIST_MAX = 20


# ------------------------------------------------------------------ 存储
def _load():
    data = {}
    if DB.exists():
        try:
            data = json.loads(DB.read_text("utf-8"))
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("items"), list):
        data["items"] = []
    if not isinstance(data.get("history"), list):
        data["history"] = []
    try:
        data["seq"] = int(data.get("seq") or 0)
    except Exception:
        data["seq"] = 0
    return data


def _save(data):
    DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(DB)


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ 权限
def _admin(ctx):
    uid = str(getattr(ctx, "user_id", "") or "").strip()
    if not uid:
        return False
    try:
        conf = ctx.cfg or {}
        owner = str(conf.get("admin_qq") or DEFAULT_ADMIN).strip()
    except Exception:
        owner = DEFAULT_ADMIN
    if uid == owner:
        return True
    # 控制台「管理员」列表里的账号同样有效
    try:
        return bool(ctx.bot.plugins.is_admin(uid))
    except Exception:
        return False


def _limit(ctx):
    try:
        n = int((ctx.cfg or {}).get("max_items") or 300)
    except Exception:
        n = 300
    return max(10, min(n, 5000))


# ------------------------------------------------------------------ 投稿
async def _submit(ctx, args):
    args = (args or "").strip()
    if not args:
        return await ctx.reply("用法：/投稿 插件名称 插件功能")

    parts = args.split(None, 1)
    name = parts[0].strip()
    desc = parts[1].strip() if len(parts) > 1 else ""
    if not desc:
        return await ctx.reply("还差插件功能哦，格式：/投稿 插件名称 插件功能")

    if len(name) > NAME_MAX:
        name = name[:NAME_MAX]
    if len(desc) > DESC_MAX:
        desc = desc[:DESC_MAX]

    data = _load()
    if len(data["items"]) >= _limit(ctx):
        return await ctx.reply("投稿箱已满，等主人清理后再投吧")

    for item in data["items"]:
        if (str(item.get("user")) == str(ctx.user_id)
                and item.get("name") == name and item.get("desc") == desc):
            return await ctx.reply("这条已经投过啦，编号 #%s" % item.get("id"))

    data["seq"] += 1
    item = {
        "id": data["seq"],
        "name": name,
        "desc": desc,
        "user": str(ctx.user_id or ""),
        "nick": str(getattr(ctx, "sender", "") or ""),
        "group": str(getattr(ctx, "group_id", "") or ""),
        "time": _now(),
    }
    data["items"].append(item)
    _save(data)
    return await ctx.reply("关于您的投稿，我们会接纳您的意见的。\n投稿编号：#%d" % item["id"])


async def cmd_submit(ctx, args):
    return await _submit(ctx, args)


# ------------------------------------------------------------------ 列表
async def cmd_list(ctx, args):
    if not _admin(ctx):
        return await ctx.reply("权限不足")

    data = _load()
    items = data["items"]
    if not items:
        return await ctx.reply("还没有收到任何投稿")

    shown = items[-LIST_MAX:]
    lines = ["投稿列表（共 %d 条）" % len(items)]
    for it in shown:
        lines.append("")
        lines.append("#%s %s" % (it.get("id"), it.get("name") or ""))
        lines.append("　功能：%s" % (it.get("desc") or ""))
        who = it.get("nick") or "群友"
        lines.append("　投稿人：%s（%s）· %s" % (who, it.get("user") or "?", it.get("time") or ""))
    if len(items) > len(shown):
        lines.append("")
        lines.append("……仅显示最近 %d 条，另有 %d 条更早的" % (len(shown), len(items) - len(shown)))
    return await ctx.reply("\n".join(lines))


# ------------------------------------------------------------------ 结项
async def _close(ctx, args, status):
    if not _admin(ctx):
        return await ctx.reply("权限不足")

    label = "已完成" if status == "done" else "未通过"
    usage = "用法：/%s 编号" % label

    raw = (args or "").strip().lstrip("#＃").strip()
    if not raw:
        return await ctx.reply(usage)
    if not raw.isdigit():
        return await ctx.reply("编号要填数字，比如：/%s 3" % label)

    target = int(raw)
    data = _load()
    for idx, item in enumerate(data["items"]):
        if int(item.get("id") or 0) == target:
            data["items"].pop(idx)
            item["status"] = status
            item["closed_at"] = _now()
            item["closed_by"] = str(ctx.user_id or "")
            data["history"].append(item)
            _save(data)
            return await ctx.reply("投稿 #%d 已标记为%s，已从列表移除" % (target, label))

    return await ctx.reply("没有找到编号 #%d 的投稿" % target)


async def cmd_done(ctx, args):
    return await _close(ctx, args, "done")


async def cmd_reject(ctx, args):
    return await _close(ctx, args, "reject")


# ------------------------------------------------------------------ 无斜杠入口
async def on_message(ctx):
    text = (getattr(ctx, "text", "") or "").strip()
    if not text:
        return False

    m = re.match(r"^投稿(?:\s+(.*))?$", text, re.S)
    if m:
        await _submit(ctx, m.group(1) or "")
        return True

    if re.match(r"^全部投稿$", text):
        await cmd_list(ctx, "")
        return True

    m = re.match(r"^(已完成|未通过)\s+(\S+)$", text)
    if m:
        status = "done" if m.group(1) == "已完成" else "reject"
        await _close(ctx, m.group(2), status)
        return True

    return False


async def on_startup(bot):
    print("[tougao] 投稿插件已加载")
