# MakerHub 3D container image. Two stages: build the virtual environment, then copy only
# what is needed to run. Base image is pinned by digest; Dependabot proposes updates.

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Same uv version as in the GitHub Actions workflows.
RUN pip install --no-cache-dir uv==0.13.7

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Only locked versions, no development tools (tests, ruff).
RUN uv sync --locked --no-dev --no-install-project


FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The base image can lag behind Debian security fixes (Trivy fails on fixable HIGH/CRITICAL findings),
# so installed system packages are updated once while building.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Normal user without login shell; /data is the only place the app may write to.
RUN useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin makerhub \
    && mkdir /data \
    && chown makerhub:makerhub /data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"]

# Inside the container the app listens on all interfaces; compose publishes the port on 127.0.0.1 only.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
