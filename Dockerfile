# ============================================================
# tg115share —— TG → 115 转分享机器人（飞牛 NAS 部署用镜像）
#
# 依赖只有 requests（见 requirements.txt）。配置不打进镜像，
# 通过挂载 /app/config.json 注入（内含 TG token 与 MCP 凭据）。
#
# 国内构建若慢/不通，可换基础镜像与 pip 源：
#   docker build \
#     --build-arg BASE_IMAGE=docker.1ms.run/library/python:3.12-slim \
#     --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#     -t tg115share .
# ============================================================
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# 版本号：CI 打 tag 时由 git tag 注入（见 .github/workflows/ci.yml），本地构建用默认值。
ARG APP_VERSION=dev
# 留空则用官方 PyPI；国内构建可传国内镜像。
ARG PIP_INDEX_URL=

LABEL org.opencontainers.image.title="tg115share" \
      org.opencontainers.image.description="Telegram → 115 转分享机器人：把 TG 里的 115 分享链接转存并创建成自己的分享" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/utterliar1/tg115share" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    CONFIG_PATH=/app/config.json \
    LOG_DIR=/data/logs

# tzdata 让 TZ 生效（slim 自带没有）。装不上也不影响运行，仅日志时间可能不准。
RUN set -eux; \
    apt-get update \
      && apt-get install -y --no-install-recommends tzdata \
      && rm -rf /var/lib/apt/lists/* \
    || echo "tzdata install failed, ignored";

WORKDIR /app

# 非 root 运行；/data 用于持久化日志
RUN useradd -u 1000 -m -s /usr/sbin/nologin appuser \
    && mkdir -p /data && chown -R appuser:appuser /data

COPY requirements.txt ./
RUN if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

COPY tg115share.py ./
RUN chown -R appuser:appuser /app

USER appuser
VOLUME ["/data"]

# 构建期无法做真连通性自检（需真实 MCP/TG）；缺 config.json 时程序会打印
# 「缺少配置文件」并以非 0 退出，属预期行为（ci.yml 的冒烟作业正是校验这一点）。
CMD ["python", "tg115share.py"]
