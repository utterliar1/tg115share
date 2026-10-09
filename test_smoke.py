#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线回归测试 —— 只测不依赖网络的纯函数（链接解析 / 体积格式化）。

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

    print("-" * 40)
    if FAILED:
        print("FAILED: %d 项 -> %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
