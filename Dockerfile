# ChorePilot in a container: `chorepilot run` (device events, Telegram, MCP, dashboard).
#   docker compose run --rm chorepilot setup      # once: you become the owner
#   docker compose up -d
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    CHOREPILOT_CONFIG_DIR=/data/config \
    CHOREPILOT_CACHE_DIR=/data/cache \
    CHOREPILOT_WEB_HOST=0.0.0.0 \
    CHOREPILOT_MCP_HOST=0.0.0.0 \
    CHOREPILOT_NOTIFY_DESKTOP=0

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY chorepilot ./chorepilot
RUN pip install --no-cache-dir . \
 && useradd --create-home --uid 1000 chorepilot \
 && mkdir -p /data && chown chorepilot:chorepilot /data \
 && rm -rf /src

USER chorepilot
WORKDIR /home/chorepilot
VOLUME ["/data"]
# 8771 dashboard, 8770 MCP (streamable HTTP). Publish them on 127.0.0.1 only (see compose).
EXPOSE 8771 8770
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8771/healthz', timeout=4)" || exit 1
ENTRYPOINT ["chorepilot"]
CMD ["run"]
