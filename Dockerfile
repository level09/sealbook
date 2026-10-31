# syntax=docker/dockerfile:1.4
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build deps + uv in one layer (git, for dependencies pinned to a commit)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential python3-dev git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Install dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --extra full --frozen --no-dev --no-install-project

# Runtime
FROM python:3.12-slim

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install runtime deps and create user
# ffmpeg reads video and audio (frames, fingerprints, speech); Chromium serves web research.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends curl libexpat1 ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 stk

# c2patool verifies C2PA manifests at ingest.
# ponytail: upstream ships x86_64 Linux only; arm hosts need a source build.
ARG C2PATOOL_VERSION=0.27.22
ARG C2PATOOL_SHA256=6f138ad53da1a62f1cd6ee71c60727efa27d13b811f4c0ea8c2a8528968b358b
RUN curl -fsSL -o /tmp/c2patool.tgz "https://github.com/contentauth/c2pa-rs/releases/download/c2patool-v${C2PATOOL_VERSION}/c2patool-v${C2PATOOL_VERSION}-x86_64-unknown-linux-gnu.tar.gz" \
    && echo "${C2PATOOL_SHA256}  /tmp/c2patool.tgz" | sha256sum -c - \
    && tar -xzf /tmp/c2patool.tgz -C /tmp c2patool/c2patool \
    && install /tmp/c2patool/c2patool /usr/local/bin/c2patool \
    && rm -rf /tmp/c2patool /tmp/c2patool.tgz

# Copy venv and app
COPY --from=builder --chown=stk:stk /app/.venv ./.venv
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY --chown=stk:stk . .

# Create instance dir with correct ownership
RUN mkdir -p /app/instance && chown stk:stk /app/instance

USER stk

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

CMD ["uvicorn", "sealbook.app:app", "--host", "0.0.0.0", "--port", "5000", "--timeout-graceful-shutdown", "3"]
