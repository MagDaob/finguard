FROM ghcr.io/astral-sh/uv:0.11.14 AS uv
FROM python:3.14-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_CACHE_DIR=/tmp/uv-cache PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev && uv cache clean \
    && useradd --uid 10001 --create-home finguard \
    && mkdir -p /app/artifacts /tmp/uv-cache \
    && chown -R finguard:finguard /app /tmp/uv-cache
USER finguard
EXPOSE 8000
CMD ["uv", "run", "--locked", "--no-sync", "finguard", "serve", "--host", "0.0.0.0", "--port", "8000"]
