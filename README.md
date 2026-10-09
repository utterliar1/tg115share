# tg115share · TG 转分享机器人

把发给 Telegram 机器人的 **115 分享链接**，通过 **MCP(115lite)** 转存到小号并创建成**自己的分享**。

## 流程

```
TG 收链接 → MCP preview_share（回预览+确认按钮）
          → 用户点确认
          → MCP receive_share（转存到小号）
          → MCP share_files（建分享，v1 固定 15 天）
          → 回执分享链接
          → 轮询 preview_share 直到 share_state==1（审核通过）
          → MCP delete_files（删小号副本，进回收站）
          → 回执最终结果
```

## 与用户确认过的口径

| 项 | 取值 |
|---|---|
| 运行位置 | NAS（fnOS），建议 `/vol1/1000/scripts/tg115share/` |
| TG 交互 | 先预览 + 内联按钮确认，不自动执行 |
| 有效期 | v1 纯 MCP，**15 天**（MCP 无「改分享」接口，长期待 115lite 补 `share_update`） |
| 删副本 | **分享确认有效后**才删（回收站可恢复） |
| 通道 | 纯 MCP。不直连 115 webapi |

## 前置

1. **TG 机器人**：找 @BotFather 建 bot 拿 token；用 @userinfobot 拿自己的 chat_id。
2. **网络**：大陆机器访问 `api.telegram.org` 一般需代理，配 `telegram.proxy`（如 iKuai 的 `http://192.168.123.1:7890`）。
3. **MCP 凭据**：从本机 `~/.workbuddy/mcp.json` 抄 `115-lite-web` 的 `url` 与 `Authorization`。
   - NAS 上跑时把 url 改成 `http://127.0.0.1:11510/mcp-server`（同机）。

## Docker 部署（推荐）

镜像由 GitHub Actions 自动构建并推送到 GHCR：

```bash
docker pull ghcr.io/utterliar1/tg115share:latest
```

用 compose 跑（仓库里的 `docker-compose.yml`）：

```bash
# 1) 准备目录
mkdir -p /vol1/1000/scripts/tg115share && cd /vol1/1000/scripts/tg115share

# 2) 放配置 + compose 文件
cp config.example.json config.json
vi config.json                     # 填 bot_token / allowed_chat_ids / proxy / mcp.token
mkdir -p data && sudo chown 1000:1000 data   # 容器内以 uid 1000 写日志

# 3) 启动
docker compose up -d
docker compose logs -f
```

> **关键点：MCP 地址**。compose 用 `network_mode: host`，容器内的 `127.0.0.1` 就是宿主本体，
> 因此 `config.json` 的 `mcp.url` 保持 `http://127.0.0.1:11510/mcp-server` 即可（与 115lite 同机）。
> 若你的环境不支持 host 网络，改用 compose 注释里的 bridge + `extra_hosts` 方案，
> 并把 `mcp.url` 改成 `http://host.docker.internal:11510/mcp-server`。
>
> **配置与凭据不进镜像**：`config.json` 只读挂载进容器（`/app/config.json`），日志落在 `./data`。

自检（不进容器、只验连通）：

```bash
docker compose run --rm tg115share python tg115share.py --selftest
```

### 自行构建镜像（可选）

```bash
docker build -t tg115share .
# 国内网络可换基础镜像与 pip 源：
docker build \
  --build-arg BASE_IMAGE=docker.1ms.run/library/python:3.12-slim \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  -t tg115share .
```

## 手动部署（venv，备选）

```bash
# 1) 拷目录到 NAS
#    本地 D:\Documents\WorkBuddy\飞牛\tg115share  →  NAS /vol1/1000/scripts/tg115share

cd /vol1/1000/scripts/tg115share

# 2) 建 venv 装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3) 写配置
cp config.example.json config.json
vi config.json     # 填 bot_token / allowed_chat_ids / proxy / mcp.token

# 4) 自检（只连不通不执行任何动作）
.venv/bin/python tg115share.py --selftest

# 5) 常驻
.venv/bin/python tg115share.py
```

### 用 systemd 常驻

```ini
# /etc/systemd/system/tg115share.service
[Unit]
Description=TG 115 转分享机器人
After=network-online.target

[Service]
WorkingDirectory=/vol1/1000/scripts/tg115share
ExecStart=/vol1/1000/scripts/tg115share/.venv/bin/python tg115share.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now tg115share
journalctl -u tg115share -f
```

## 使用

给 bot 发一条含分享链接的消息，例如：

```
https://115cdn.com/s/swsjxrh3fn6?password=8688
```

bot 回预览 → 点「✅ 转存并分享」→ 等回执。

## 配置项

| 键 | 说明 |
|---|---|
| `telegram.proxy` | 访问 TG 的代理，留空则直连 |
| `account.expect_user_id` | **护栏**：MCP 绑定账号不是它就直接中止（防误操作主号） |
| `behavior.receive_cid` | 转存到哪个目录，`0` = 根目录 |
| `behavior.share_duration_days` | 分享天数，v1 用 15 |
| `behavior.throttle_sec` | 每次 MCP 调用间隔，防风控 |
| `behavior.verify_poll_sec` | 审核轮询间隔，默认 180s（**别调太小**，115 对 preview_share 有频控） |
| `behavior.verify_timeout_hours` | 审核等待上限，超时则保留副本并提示 |
| `behavior.delete_after_verified` | 确认有效后是否删副本 |

## 已知限制 / 注意事项

- **MCP 账号跟随 Web UI 当前账号**。脚本每次任务前会 `get_account_info` 断言，若不是 `expect_user_id` 会中止——但这意味着 **Web UI 切到主号时脚本会拒绝工作**，需要你在 Web UI 切回小号。
- **MCP 无「改分享」接口**，所以 v1 只能 15 天。等 115lite 补上 `share_update` 后可改成长期。
- 转存/建分享有平台风控，脚本已加 `throttle_sec`；**别把 `verify_poll_sec` 调到 60s 以下**。
- 分享审核 10min–24h 属正常，超时不会丢东西（副本保留）。
- 删除只进回收站，可恢复；彻底释放需另行清空回收站。
