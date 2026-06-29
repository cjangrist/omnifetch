# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim@sha256:dc6831ca75771711b69e2fcaf47f2b4938bcfd7721daf254c1131791249d000d AS build

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim-trixie@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280 AS runtime

RUN useradd --create-home --uid 10001 app

WORKDIR /app

COPY --from=build --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMNIFETCH_TRANSPORT=http \
    OMNIFETCH_HOST=0.0.0.0 \
    OMNIFETCH_PORT=8000 \
    OMNIFETCH_REST_WEB_FETCH=true

USER app

EXPOSE 8000

ENTRYPOINT ["omnifetch"]
CMD ["--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
