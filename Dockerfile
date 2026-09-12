# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

# Stage 1: the panel is built here and only its static output reaches the image.
FROM node:22-slim AS painel
WORKDIR /src/painel
COPY painel/package.json painel/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY painel/ ./
RUN npm run build

# Stage 2: runtime. Kept compatible with the legacy builder because the
# reference ARM appliance has no BuildKit.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IPHUB_DATA=/data \
    IPHUB_PAINEL=/app/painel \
    IPHUB_PORTA=8080

# "python -m iphub" puts /app first on sys.path, so a daemon able to write
# there could drop a package that shadows a real one on the next start; the
# service user owns only /data and its home is outside /app.
RUN groupadd --system --gid 10001 iphub \
    && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent \
       --shell /usr/sbin/nologin --no-create-home iphub \
    && mkdir -p /app /data \
    && chown root:root /app \
    && chmod 755 /app \
    && chown iphub:iphub /data

# The dependency layer sits before the code layer so a code change does
# not download wheels again on an ARM board with a slow link.
COPY core/pyproject.toml /src/pyproject.toml
# The README sends a stranger to build this on a Raspberry Pi over a home link, and
# the reference appliance downloads slowly; one timed out wheel must not fail the build.
RUN pip install --no-cache-dir --retries 5 --timeout 60 $(python -c "import tomllib;print(' '.join(tomllib.load(open('/src/pyproject.toml','rb'))['project']['dependencies']))")

WORKDIR /app
COPY core/iphub /app/iphub
COPY --from=painel /src/painel/dist /app/painel
COPY LICENSE NOTICE /app/
# The minifier drops the license headers of the bundled dependencies, so
# the notice the panel build generates travels with the image next to NOTICE.
COPY --from=painel /src/painel/dist/.vite/license.md /app/NOTICE-painel.md

LABEL org.opencontainers.image.source="https://github.com/queroautomacao/tuya-ip-hub" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.title="Tuya IP Hub"

USER iphub
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=45s --retries=3 CMD ["python","-m","iphub.saude"]
CMD ["python","-m","iphub"]
