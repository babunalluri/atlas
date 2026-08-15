FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations

FROM base AS development
RUN uv pip install -e ".[dev]" "mcp>=1.28.1,<2" \
  && python -c "import mcp"
EXPOSE 7777
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7777", "--reload"]

FROM base AS production
RUN uv pip install . "mcp>=1.28.1,<2" \
  && python -c "import mcp" \
  && addgroup --system app \
  && adduser --system --ingroup app app
USER app
EXPOSE 7777
ENV WEB_CONCURRENCY=2
CMD ["sh", "-c", "gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:7777 app.main:app"]
