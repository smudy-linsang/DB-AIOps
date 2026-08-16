# syntax=docker/dockerfile:1.7
FROM python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1 AS wheel-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN python -m pip wheel --require-hashes --wheel-dir /wheels -r requirements.lock

FROM python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_ENV=production \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl tini unixodbc \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv \
    && addgroup --system --gid 10001 dbmonitor \
    && adduser --system --uid 10001 --ingroup dbmonitor --home /app dbmonitor

COPY --from=wheel-builder /wheels /wheels
COPY requirements.lock /tmp/requirements.lock
RUN /opt/venv/bin/pip install --require-hashes --no-index --find-links=/wheels \
      -r /tmp/requirements.lock \
    && rm -rf /wheels /tmp/requirements.lock

WORKDIR /app
COPY --chown=dbmonitor:dbmonitor . /app
RUN mkdir -p /app/logs /app/staticfiles \
    && chown -R dbmonitor:dbmonitor /app/logs /app/staticfiles

USER dbmonitor
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--worker-class", "gthread", "--threads", "8", "--worker-tmp-dir", "/dev/shm", "--timeout", "120", "dbmonitor.wsgi:application"]
