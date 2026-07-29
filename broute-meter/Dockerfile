# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

COPY config/settings.example.yaml ./config/settings.example.yaml

RUN groupadd --gid 10001 broute \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --no-create-home \
        --home-dir /app \
        --shell /usr/sbin/nologin \
        broute \
    && mkdir -p /data /logs \
    && chown -R 10001:10001 /app

USER 10001:10001

FROM base AS test

USER root

RUN python -m pip install --no-cache-dir ".[dev]"

COPY --chown=10001:10001 tests ./tests

USER 10001:10001

ENTRYPOINT ["python", "-m", "pytest"]
CMD ["tests/integration", "-q", "-p", "no:cacheprovider"]


FROM base AS runtime

ENTRYPOINT ["python", "-m", "broute_meter"]
CMD ["--settings", "config/settings.example.yaml", "setup-adapter", "--mock"]
