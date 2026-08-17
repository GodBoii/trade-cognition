# =============================================================================
# Trade Cognition backend
#
# IMPORTANT - read before deploying:
#
# The live MetaTrader 5 gateway needs the vendor `MetaTrader5` package, which is
# 64-bit Windows only and drives a *running* MT5 terminal on the same machine.
# Neither exists in a Linux container, so this image can only run the simulator
# (`TC_MT5_GATEWAY=mock`). The requirements file already scopes the dependency
# with `sys_platform == "win32"`, so the build succeeds here without it.
#
# For real trading, run the backend on a Windows host (or Windows container with
# a terminal available) - see docs/06-mt5-integration.md. This image is for
# development, CI, demos, and for the non-trading parts of a staging stack.
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY pyproject.toml ./

# Run as a non-root user, and give it ownership of the state directory only.
RUN useradd --create-home --uid 10001 trader \
    && mkdir -p /data \
    && chown -R trader:trader /data /app
USER trader

# SQLite database and the credential encryption key live here; mount a volume so
# connected accounts and trade history survive a container replacement.
ENV TC_DATABASE_URL=sqlite:////data/trade_cognition.db \
    TC_MT5_GATEWAY=mock \
    TC_MONITOR_ENABLED=true

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import sys,urllib.request;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# One worker on purpose: the position monitor is a single background task and the
# MT5 gateway is a process-wide singleton, so replicas would duplicate
# management passes. Scale by running one process per MT5 account instead.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
