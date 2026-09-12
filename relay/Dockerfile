# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
# The rendezvous of the remote access. It holds no data of any customer and no credential of
# any kind: what it keeps in memory is a map of short lived addresses to open sockets.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RELAY_PORTA=8090

RUN groupadd --system --gid 10002 relay \
    && useradd --system --uid 10002 --gid 10002 --home-dir /nonexistent \
       --shell /usr/sbin/nologin --no-create-home relay \
    && mkdir -p /app && chown root:root /app && chmod 755 /app

COPY relay/pyproject.toml /src/pyproject.toml
RUN pip install --no-cache-dir --retries 5 --timeout 60 $(python -c "import tomllib;print(' '.join(tomllib.load(open('/src/pyproject.toml','rb'))['project']['dependencies']))")

WORKDIR /app
COPY relay/relay /app/relay
COPY LICENSE NOTICE /app/

LABEL org.opencontainers.image.source="https://github.com/queroautomacao/tuya-ip-hub" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.title="Tuya IP Hub relay"

USER relay
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python","-c","import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/saude',timeout=3).status==200 else 1)"]
CMD ["python","-m","relay"]
