# ── Stage 1: Builder (install deps, compile bytecode) ────────

FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime (slim, non-root) ──────────────────────

FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN groupadd -r karsa && useradd -r -g karsa -d /app -s /sbin/nologin karsa

WORKDIR /app

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN chown -R karsa:karsa /app

USER karsa

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["/entrypoint.sh"]
