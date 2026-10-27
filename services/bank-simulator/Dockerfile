FROM ghcr.io/astral-sh/uv:0.11.14 AS uv
FROM python:3.14-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_CACHE_DIR=/tmp/uv-cache PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY packages/bank-contracts ./packages/bank-contracts
COPY services/bank-simulator ./services/bank-simulator
RUN uv sync --locked --no-dev --package finguard-bank-simulator \
    && uv cache clean \
    && useradd --uid 10002 --create-home simbank \
    && mkdir -p /app/artifacts /tmp/uv-cache \
    && chown -R simbank:simbank /app /tmp/uv-cache
USER simbank
WORKDIR /app/services/bank-simulator
EXPOSE 8001
CMD ["/app/.venv/bin/finguard-bank-simulator", "serve", "--host", "0.0.0.0", "--port", "8001"]
