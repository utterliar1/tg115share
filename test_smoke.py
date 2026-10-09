#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线回归测试 —— 只测不依赖网络的纯函数（链接解析 / 体积格式化 / SSE 解码）。

CI 与本地同一口径：python test_smoke.py
（不触碰 MCP 与 TG，因此可在任何环境无凭据运行。）
"""
import sys

import tg115share as m

FAILED = []


def check(name, got, want):
    ok = got == want
    print("%s %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        print("     got =%r" % (got,))
        print("     want=%r" % (want,))
        FAILED.append(name)


def main():
    # ---------- parse_share：链接 + 提取码识别 ----------
    check("115cdn + ?password=",
          m.parse_share("https://115cdn.com/s/swsjxrh3fn6?password=8688"),
          ("swsjxrh3fn6", "8688"))

    check("115.com + 提取码:",
          m.parse_share("https://115.com/s/xyz789 提取码: ab12"),
          ("xyz789", "ab12"))

    check("anxia + pwd=",
          m.parse_share("看这个 https://anxia.com/s/q1w2e3 pwd=ZZ99"),
          ("q1w2e3", "ZZ99"))

    check("无协议头",
          m.parse_share("115cdn.com/s/abc123?password=0000"),
          ("abc123", "0000"))

    check("有链接无提取码",
          m.parse_share("https://115cdn.com/s/onlycode"),
          ("onlycode", ""))

    check("无链接",
          m.parse_share("这是一条没有链接的消息"),
          None)

    check("空输入",
          m.parse_share(""),
          None)

    # ---------- fmt_size：体积格式化 ----------
    check("fmt_size 0", m.fmt_size(0), "0 B")
    check("fmt_size 500", m.fmt_size(500), "500 B")
    check("fmt_size 1024", m.fmt_size(1024), "1.00 KB")
    check("fmt_size 1GiB", m.fmt_size(1024 ** 3), "1.00 GB")
    check("fmt_size 46.45GiB",
          m.fmt_size(int(46.45 * 1024 ** 3)), "46.45 GB")
    check("fmt_size None", m.fmt_size(None), "0 B")

    # ---------- sse_data_text：MCP 的 SSE 响应必须按 UTF-8 解 ----------
    # 真实形态：Content-Type: text/event-stream（无 charset），JSON 内含中文。
    sse = (
        "event: message\r\n"
        "data: {\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[{\"type\":\"text\","
        "\"text\":\"{\\n  \\\"state\\\": true,\\n  \\\"data\\\": {\\n    \\\"user\\\": {\\n"
        "      \\\"user_name\\\": \\\"我誓言\\\"\\n    }\\n  }\\n}\"}]}}\r\n\r\n"
    ).encode("utf-8")

    got = m.sse_data_text(sse)
    check("sse_data_text 取到 data 段", got.startswith('{"jsonrpc"'), True)
    check("sse_data_text 中文完好", ("我誓言" in got), True)

    # 反向对照：旧 bug 是拿 r.text（latin-1）解，同样的字节会变乱码。
    # 这条断言把「中文必须能原样读出」钉死在回归里。
    bad = sse.decode("latin-1")
    check("latin-1 解同一响应会乱码（说明为什么不能用 r.text）",
          ("我誓言" in bad), False)

    print("-" * 40)
    if FAILED:
        print("FAILED: %d 项 -> %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
