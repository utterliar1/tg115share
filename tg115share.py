#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TG → 115 转分享机器人

把用户发给 Telegram 机器人的 115 分享链接，通过 MCP(115lite) 转存到指定账号，
并创建属于自己的分享。

流程：
    收链接 → MCP preview_share → 回预览 + 内联确认按钮
           → 用户确认 → MCP receive_share（转存到小号）
           → MCP share_files（建分享，v1 默认 15 天）
           → 回执分享链接
           → 轮询 preview_share 直到 share_state==1（平台审核通过）
           → MCP delete_files（删小号副本，进回收站）
           → 回执最终结果

设计约束（与用户确认）：
    * 纯 MCP，不直连 115 webapi（长期有效待 115lite 补 share_update 工具后再说）
    * 先预览再确认，不自动执行
    * 分享确认有效后才删副本

用法：
    python3 tg115share.py            # 常驻
    python3 tg115share.py --selftest # 自检：连通 MCP / TG，打印账号
"""
import argparse
import io
import json
import logging
import os
import re
import sys
import threading
import time
import traceback

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
# 可用环境变量覆盖，便于容器化部署（见 Dockerfile）：
#   CONFIG_PATH  配置文件的绝对路径（默认 <脚本目录>/config.json）
#   LOG_DIR      日志目录（默认 <脚本目录>/logs）
CONFIG_PATH = os.environ.get("CONFIG_PATH") or os.path.join(BASE, "config.json")
LOG_DIR = os.environ.get("LOG_DIR") or os.path.join(BASE, "logs")

DEFAULTS = {
    "telegram": {"bot_token": "", "allowed_chat_ids": [], "proxy": "", "poll_timeout": 50},
    "mcp": {"url": "http://127.0.0.1:11510/mcp-server", "token": "", "timeout": 60},
    "account": {"expect_user_id": "9697664", "expect_user_name": "我誓言"},
    "behavior": {
        "receive_cid": "0",
        "share_duration_days": 15,
        "throttle_sec": 1.5,
        "verify_poll_sec": 180,
        "verify_timeout_hours": 26,
        "delete_after_verified": True,
        "max_size_tib": 0,
    },
}

LINK_RE = re.compile(
    r"(?:https?://)?(?:115cdn\.com|115cdn\.net|115\.com|anxia\.com)/s/([0-9a-zA-Z]+)", re.I)
PWD_RE = re.compile(r"(?:password=|pwd=|passcode=|提取码[:：]?\s*#?)([0-9a-zA-Z]{4,8})", re.I)


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit("缺少配置文件 %s（可从 config.example.json 复制）" % CONFIG_PATH)
    user = json.load(io.open(CONFIG_PATH, encoding="utf-8"))
    cfg = {}
    for k, v in DEFAULTS.items():
        cfg[k] = dict(v)
        cfg[k].update(user.get(k) or {})
    return cfg


def fmt_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.2f %s" % (n, unit))
        n /= 1024.0
    return "%.2f PB" % n


def parse_share(text):
    """从任意文本里抽出 (share_code, receive_code)。识别不到返回 None。"""
    m = LINK_RE.search(text or "")
    if not m:
        return None
    code = m.group(1)
    p = PWD_RE.search(text)
    return code, (p.group(1) if p else "")


# --------------------------------------------------------------------------- #
# MCP 客户端（HTTP 流式 / JSON-RPC）
# --------------------------------------------------------------------------- #
class MCP(object):
    def __init__(self, url, token, timeout=60):
        self.url = url
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": token if token.lower().startswith("bearer") else "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        self.s.trust_env = False          # 内网直达，绕过系统代理
        self._id = 0
        self._lock = threading.Lock()
        self._init_session()

    def _rpc(self, method, params=None, notify=False):
        with self._lock:
            self._id += 1
            body = {"jsonrpc": "2.0", "method": method}
            if not notify:
                body["id"] = self._id
            if params is not None:
                body["params"] = params
            r = self.s.post(self.url, json=body, timeout=self.timeout)
            r.raise_for_status()
            sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
            if sid:
                self.s.headers["Mcp-Session-Id"] = sid
            # 响应是 SSE，且 JSON 内含裸换行 → 必须取 data: 之后整段解析
            i = r.text.find("data:")
            if i < 0:
                return {}
            return json.loads(r.text[i + 5:].strip())

    def _init_session(self):
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "tg115share", "version": "1.0"},
        })
        self._rpc("notifications/initialized", None, notify=True)

    def call(self, name, _retry=True, **args):
        """调用一个 MCP 工具，返回其 JSON 结果（dict）。"""
        try:
            resp = self._rpc("tools/call", {"name": name, "arguments": args or {}})
        except Exception:
            if _retry:
                time.sleep(2)
                self._init_session()
                return self.call(name, _retry=False, **args)
            raise
        if "error" in resp:
            raise RuntimeError("MCP 错误 %s: %s" % (name, json.dumps(resp["error"], ensure_ascii=False)))
        res = resp.get("result") or {}
        blocks = res.get("content") or []
        text = blocks[0].get("text", "") if blocks else ""
        if res.get("isError"):
            raise RuntimeError("工具 %s 执行失败: %s" % (name, text[:400]))
        try:
            return json.loads(text)
        except Exception:
            return {"state": None, "_raw": text}


# --------------------------------------------------------------------------- #
# Telegram 客户端（长轮询，无需公网）
# --------------------------------------------------------------------------- #
class TG(object):
    def __init__(self, token, proxy=""):
        self.base = "https://api.telegram.org/bot%s" % token
        self.s = requests.Session()
        self.s.trust_env = False
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}

    def _call(self, method, timeout=70, **data):
        r = self.s.post("%s/%s" % (self.base, method), json=data, timeout=timeout)
        j = r.json()
        if not j.get("ok"):
            logging.warning("TG %s 失败: %s", method, json.dumps(j, ensure_ascii=False)[:300])
        return j

    def get_updates(self, offset, timeout):
        r = self.s.get("%s/getUpdates" % self.base,
                       params={"offset": offset, "timeout": timeout, "allowed_updates":
                               json.dumps(["message", "callback_query"])},
                       timeout=timeout + 20)
        return r.json()

    def send(self, chat_id, text, keyboard=None):
        data = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if keyboard:
            data["reply_markup"] = {"inline_keyboard": keyboard}
        return self._call("sendMessage", **data)

    def edit(self, chat_id, message_id, text, keyboard=None):
        data = {"chat_id": chat_id, "message_id": message_id, "text": text,
                "disable_web_page_preview": True}
        if keyboard is not None:
            data["reply_markup"] = {"inline_keyboard": keyboard}
        return self._call("editMessageText", **data)

    def answer_cb(self, cb_id, text=""):
        return self._call("answerCallbackQuery", callback_query_id=cb_id, text=text)


# --------------------------------------------------------------------------- #
# 业务流程
# --------------------------------------------------------------------------- #
def account_guard(mcp, cfg):
    """返回 (ok, 描述)。断言 MCP 绑定的是预期账号，避免误操作主号。"""
    acc = mcp.call("get_account_info")
    u = ((acc.get("data") or {}).get("user") or {})
    uid, uname = str(u.get("user_id") or ""), u.get("user_name") or ""
    exp = str(cfg["account"].get("expect_user_id") or "")
    if exp and uid != exp:
        return False, "MCP 当前绑定账号 %s(%s)，预期 %s —— 已中止。" % (uname, uid, exp)
    return True, "%s(%s)" % (uname, uid)


def do_preview(mcp, share_code, receive_code):
    p = mcp.call("preview_share", share_code=share_code, receive_code=receive_code,
                 cid="0", limit=20, offset=0)
    d = p.get("data") or {}
    info = d.get("shareinfo") or {}
    if not info:
        return None, "无法读取该分享（分享码或提取码可能不对）。"
    items = (d.get("list") or [])[:8]
    names = "\n".join("  · %s" % (it.get("n") or "") for it in items)
    txt = (
        "发现分享，请确认是否转存：\n\n"
        "标题：%s\n"
        "体积：%s\n"
        "顶层条目：%d 项\n%s\n\n"
        "确认后我会：转存到小号 → 建分享(15 天) → 审核通过后删副本。"
        % (info.get("share_title") or "-", fmt_size(info.get("file_size")),
           d.get("count") or 0, names)
    )
    return {"code": share_code, "pwd": receive_code,
            "title": info.get("share_title") or "",
            "size": info.get("file_size") or 0}, txt


def run_pipeline(tg, mcp, cfg, chat_id, share_code, receive_code, reply_msg_id):
    bh = cfg["behavior"]
    throttle = float(bh.get("throttle_sec") or 1.5)

    def status(text):
        try:
            tg.edit(chat_id, reply_msg_id, text)
        except Exception:
            tg.send(chat_id, text)

    try:
        # 0) 账号护栏
        ok, who = account_guard(mcp, cfg)
        if not ok:
            status("⚠️ " + who)
            return
        logging.info("[%s] 账号确认: %s", chat_id, who)

        # 1) 转存前记录根目录，便于定位新落盘目录
        before = mcp.call("list_files", cid=bh.get("receive_cid") or "0", limit=200, offset=0)
        before_ids = {f.get("file_id") for f in ((before.get("data") or {}).get("files") or [])}

        status("开始转存…")
        rc = mcp.call("receive_share", share_code=share_code, receive_code=receive_code,
                      cid=bh.get("receive_cid") or "0")
        title = ((rc.get("data") or {}).get("receive_title") or "").strip()
        time.sleep(throttle)

        # 2) 定位新目录
        after = mcp.call("list_files", cid=bh.get("receive_cid") or "0", limit=200, offset=0)
        files = (after.get("data") or {}).get("files") or []
        new_items = [f for f in files if f.get("file_id") not in before_ids]
        fid = None
        for f in new_items:
            if title and f.get("name") == title:
                fid = f.get("file_id")
                break
        if not fid and len(new_items) == 1:
            fid = new_items[0].get("file_id")
        if not fid:
            status("⚠️ 转存完成但没能定位到新目录，请到网盘手动确认。\n标题：%s" % (title or "-"))
            return
        time.sleep(throttle)

        # 3) 建分享（v1：15 天）
        status("转存完成，正在创建分享…")
        kwargs = {"file_ids": [fid]}
        if bh.get("share_duration_days"):
            kwargs["share_duration"] = int(bh["share_duration_days"])
        sh = mcp.call("share_files", **kwargs)
        sd = sh.get("data") or {}
        new_code, new_pwd = sd.get("share_code"), sd.get("receive_code")
        url = sd.get("share_url") or ("https://115cdn.com/s/%s" % new_code)
        if not new_code:
            status("⚠️ 创建分享失败：%s" % json.dumps(sh, ensure_ascii=False)[:300])
            return

        status("✅ 分享已创建（平台审核中）\n\n%s\n提取码：%s\n\n"
               "标题：%s\n体积：%s\n\n审核通过后会自动删除小号副本，请稍候…"
               % (url, new_pwd, title or "-", fmt_size(sd.get("total_size"))))

        # 4) 轮询直到有效
        deadline = time.time() + float(bh.get("verify_timeout_hours") or 26) * 3600
        interval = float(bh.get("verify_poll_sec") or 180)
        state = None
        while time.time() < deadline:
            time.sleep(interval)
            try:
                p = mcp.call("preview_share", share_code=new_code, receive_code=new_pwd,
                             cid="0", limit=1, offset=0)
            except Exception as e:
                logging.warning("轮询失败: %s", e)
                continue
            d = p.get("data") or {}
            info = d.get("shareinfo") or {}
            state = d.get("share_state", info.get("share_state"))
            if str(info.get("forbid_reason") or "").strip():
                status("⚠️ 分享被平台驳回：%s\n小号副本已保留，未删除。" % info.get("forbid_reason"))
                return
            if state == 1:
                break
        else:
            status("⏳ 分享仍在审核中（超过 %.0f 小时）。\n%s\n提取码：%s\n\n"
                   "小号副本已保留，未删除。稍后可回复 /check %s 手动确认。"
                   % (float(bh.get("verify_timeout_hours") or 26), url, new_pwd, new_code))
            return

        # 5) 确认有效 → 删副本
        if bh.get("delete_after_verified", True):
            try:
                mcp.call("delete_files", file_ids=[fid], parent_id=bh.get("receive_cid") or "0")
            except Exception as e:
                logging.exception("删副本失败")
                status("✅ 分享已确认有效。\n%s\n提取码：%s\n\n⚠️ 但删除小号副本失败：%s"
                       % (url, new_pwd, e))
                return
            status("✅ 全部完成\n\n分享：%s\n提取码：%s\n标题：%s\n体积：%s\n\n"
                   "分享已确认有效，小号副本已删除（回收站可恢复）。"
                   % (url, new_pwd, title or "-", fmt_size(sd.get("total_size"))))
        else:
            status("✅ 分享已确认有效\n%s\n提取码：%s" % (url, new_pwd))

    except Exception as e:
        logging.error("流程异常: %s\n%s", e, traceback.format_exc())
        status("⚠️ 处理出错：%s" % str(e)[:300])


# --------------------------------------------------------------------------- #
# 主循环
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="只做连通性自检")
    args = ap.parse_args()

    cfg = load_config()
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(os.path.join(LOG_DIR, "tg115share.log"), encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])

    mcp = MCP(cfg["mcp"]["url"], cfg["mcp"]["token"], cfg["mcp"].get("timeout", 60))
    ok, who = account_guard(mcp, cfg)
    logging.info("MCP 连接成功，当前账号：%s（%s）", who, "符合预期" if ok else "**不符合预期**")

    if args.selftest:
        tg = TG(cfg["telegram"]["bot_token"], cfg["telegram"].get("proxy") or "")
        me = tg._call("getMe")
        logging.info("TG 自检：%s", json.dumps(me.get("result") or me, ensure_ascii=False)[:200])
        print("selftest done: account_ok=%s account=%s" % (ok, who))
        return

    tg = TG(cfg["telegram"]["bot_token"], cfg["telegram"].get("proxy") or "")
    allowed = {str(x) for x in (cfg["telegram"].get("allowed_chat_ids") or [])}
    pending = {}   # chat_id -> 预览信息
    busy = set()   # 正在处理的 chat_id
    lock = threading.Lock()

    logging.info("机器人启动，开始长轮询…")
    offset = 0
    while True:
        try:
            res = tg.get_updates(offset, int(cfg["telegram"].get("poll_timeout") or 50))
        except Exception as e:
            logging.warning("getUpdates 失败: %s", e)
            time.sleep(5)
            continue
        if not res.get("ok"):
            time.sleep(3)
            continue

        for upd in res.get("result") or []:
            offset = upd["update_id"] + 1

            if "message" in upd:
                msg = upd["message"]
                chat_id = str(msg["chat"]["id"])
                text = msg.get("text") or ""
                if allowed and chat_id not in allowed:
                    continue
                parsed = parse_share(text)
                if not parsed:
                    if text.strip() and not text.startswith("/"):
                        tg.send(chat_id, "把 115 分享链接发给我即可（可带 ?password= 提取码）。")
                    continue
                code, pwd = parsed
                try:
                    info, preview_text = do_preview(mcp, code, pwd)
                except Exception as e:
                    tg.send(chat_id, "读取分享失败：%s" % str(e)[:200])
                    continue
                if not info:
                    tg.send(chat_id, preview_text)
                    continue
                pending[chat_id] = info
                kb = [[{"text": "✅ 转存并分享", "callback_data": "go|%s|%s" % (code, pwd)},
                       {"text": "❌ 取消", "callback_data": "no|%s|" % code}]]
                sent = tg.send(chat_id, preview_text, kb)
                pending[chat_id]["msg_id"] = ((sent.get("result") or {}).get("message_id"))

            elif "callback_query" in upd:
                cq = upd["callback_query"]
                chat_id = str(cq["message"]["chat"]["id"])
                cb_id = cq["id"]
                data = cq.get("data") or ""
                msg_id = (cq.get("message") or {}).get("message_id")
                if allowed and chat_id not in allowed:
                    continue
                parts = data.split("|")
                tg.answer_cb(cb_id)

                if parts[0] == "no":
                    tg.edit(chat_id, msg_id, "已取消。")
                    pending.pop(chat_id, None)
                    continue

                if parts[0] == "go":
                    _, code, pwd = parts
                    with lock:
                        if chat_id in busy:
                            tg.send(chat_id, "上一个任务还在跑，请稍候。")
                            continue
                        busy.add(chat_id)
                    threading.Thread(
                        target=lambda: (run_pipeline(tg, mcp, cfg, chat_id, code, pwd, msg_id),
                                        busy.discard(chat_id)),
                        daemon=True).start()
                    pending.pop(chat_id, None)


if __name__ == "__main__":
    main()
