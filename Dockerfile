# syntax=docker/dockerfile:1
# check=skip=SecretsUsedInArgOrEnv
# (the skipped check flags CTRADER_TOKEN_STORE / CTRADER_HEALTH_FILE: paths, not secrets)
# cTrader Open API trading bot (claudeApp_v1) — V1-T43, TASKS.txt TASK 20.
#
# Two layers: dependencies (rebuilt only when deploy/requirements.docker.txt
# changes), then the application code. Secrets are NOT in the image: pass
# CTRADER_* at runtime (env file). Writable state lives on two volumes:
#   /app/logs   trade logs, trades CSV, ml capture
#   /app/state  refreshed OAuth tokens + health file
# Build:  docker build --build-arg BOT_UID=$(id -u) -t ctrader-bot .
# Run:    see env_setup.txt ("Docker") or compose.yaml.

FROM python:3.12-slim AS deps

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

COPY deploy/requirements.docker.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# ---------------------------------------------------------------- app layer
FROM deps AS app

# Match the host user that owns the mapped folders (default 10001)
ARG BOT_UID=10001
RUN useradd --uid "${BOT_UID}" --create-home --shell /usr/sbin/nologin bot \
 && mkdir -p /app/logs /app/state \
 && chown bot:bot /app/logs /app/state

WORKDIR /app
COPY --chown=bot:bot csys/ csys/
COPY --chown=bot:bot capp/ capp/
COPY --chown=bot:bot tools/ tools/
COPY --chown=bot:bot main.py ./
COPY --chown=bot:bot deploy/healthcheck.py deploy/

ENV CTRADER_TOKEN_STORE=/app/state/.ctrader_tokens.json \
    CTRADER_HEALTH_FILE=/app/state/health.json

USER bot

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD ["python", "deploy/healthcheck.py"]

# Exec form: python is PID 1 and gets SIGTERM; Twisted stops the reactor and
# the trade log flushes its period summary. Extra args go after the image
# name, e.g. `docker run ... ctrader-bot --dry-run --log-level debug`.
ENTRYPOINT ["python", "main.py"]
CMD []
