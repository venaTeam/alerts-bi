FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

# Resolve the locked dependencies before copying application code for better layer reuse.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY config/teams.schema.json config/teams.json config/
COPY docs/Alerting_Guide_Appchi_EN.md docs/what_is_an_incorrect_alert_EN.md docs/
COPY alembic.ini ./

RUN uv sync --frozen --no-dev \
    && mkdir -p /app/out \
    && chgrp -R 0 /app \
    && chmod -R g=u /app

# OpenShift's restricted-v2 SCC replaces this UID with an arbitrary UID in group 0.
USER 1001

EXPOSE 8000

# Override the command for other app surfaces, for example `portal`, `admin`, or `weekly`.
CMD ["alerts-bi", "serve"]
