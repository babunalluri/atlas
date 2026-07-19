FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations

FROM base AS development
RUN pip install -e ".[dev]"
EXPOSE 7777
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7777", "--reload"]

FROM base AS production
RUN pip install . \
  && addgroup --system app \
  && adduser --system --ingroup app app
USER app
EXPOSE 7777
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7777"]
