#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
#
# Bench smoke for milestone 0. Brings the hub up from zero with compose and
# checks /health, the security headers, the Host rule, the panel and the
# non-root user, then tears everything down.
# Usage: scripts/fumaca.sh [imagem]
#   with an image name: that image is used as is, nothing is built
#   without: docker compose build runs first
# COMPOSE_FILE is honored, so on Docker Desktop run:
#   COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml scripts/fumaca.sh
#
# Fumaça de bancada do marco 0. Sobe o hub do zero com compose e confere o
# /health, os cabeçalhos de segurança, a regra de Host, o painel e o usuário
# não-root, depois derruba tudo.
# Uso: scripts/fumaca.sh [imagem]
#   com nome de imagem: essa imagem é usada como está, nada é construído
#   sem: docker compose build roda antes
# COMPOSE_FILE é respeitado, então no Docker Desktop rode:
#   COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml scripts/fumaca.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# Why: a project name of its own keeps the smoke volume apart from a real
# installation on the same box, so the "down -v" below never touches real data.
# An inherited name of another installation would aim that "down -v" at it, so
# the smoke refuses to run instead of destroying that data volume.
# Por que: um nome de projeto próprio separa o volume da fumaça de uma
# instalação real na mesma máquina, então o "down -v" abaixo nunca toca dado real.
# Um nome herdado de outra instalação apontaria esse "down -v" para ela, então a
# fumaça recusa rodar em vez de destruir aquele volume de dados.
projeto="tuya-ip-hub-fumaca"
if [ -n "${COMPOSE_PROJECT_NAME:-}" ] && [ "${COMPOSE_PROJECT_NAME}" != "$projeto" ]; then
    echo "FAIL COMPOSE_PROJECT_NAME is '$COMPOSE_PROJECT_NAME' and the smoke runs 'docker compose down -v'; unset it or set it to '$projeto' and run again" >&2
    exit 2
fi
export COMPOSE_PROJECT_NAME="$projeto"

base="http://127.0.0.1:8080"
falhas=0

pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; falhas=$((falhas + 1)); }

limpar() { docker compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap limpar EXIT

if [ $# -ge 1 ]; then
    export IPHUB_IMAGEM="$1"
else
    # Why: the legacy builder is the one the ARM appliance has, so the bench proves it too.
    # Por que: o builder legado é o que o appliance ARM tem, então a bancada prova ele também.
    DOCKER_BUILDKIT=0 docker compose build
fi

# Why: --no-build overrides pull_policy build; without it compose would rebuild
# the image just built, or try to build over the one passed by name.
# Por que: --no-build sobrepõe pull_policy build; sem ele o compose reconstruiria
# a imagem recém-construída, ou tentaria construir por cima da passada por nome.
# Why: the hub binds a fixed port, so a hub already running (host network on the
# appliance, published port on Docker Desktop) makes the smoke time out with a
# message about health that says nothing about the real cause.
# Por que: o hub ocupa uma porta fixa, então um hub já rodando (rede do host no
# appliance, porta publicada no Docker Desktop) faz a fumaça estourar o tempo com
# uma mensagem sobre saúde que não diz nada da causa real.
if (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null; then
    exec 3>&-
    echo "FAIL port 8080 is already in use; stop the running hub before the smoke" >&2
    exit 1
fi

# Why: the reference ARM appliance ships Compose v2.16, which has --wait but not
# --wait-timeout, and an unknown flag aborts before anything is started.
# Por que: o appliance ARM de referência tem o Compose v2.16, que tem --wait mas não
# --wait-timeout, e uma opção desconhecida aborta antes de qualquer coisa subir.
espera=(--wait)
if docker compose up --help 2>&1 | grep -q -- '--wait-timeout'; then
    espera+=(--wait-timeout 90)
fi

if ! docker compose up -d "${espera[@]}" --no-build; then
    docker compose logs --no-color iphub || true
    fail "compose up --wait did not reach healthy in 90 s"
    echo "FAIL $falhas check(s) failed"
    exit 1
fi

corpo="$(curl -sS --max-time 5 "$base/health" || true)"
if printf '%s' "$corpo" | grep -q '"ok": *true' && printf '%s' "$corpo" | grep -q '"versao"'; then
    pass "GET /health: $corpo"
else
    fail "GET /health: expected ok true and versao, got '$corpo'"
fi

cabecalhos="$(curl -sS -D - -o /dev/null --max-time 5 "$base/health" || true)"
for esperado in \
    "X-Frame-Options: DENY" \
    "X-Content-Type-Options: nosniff" \
    "Referrer-Policy: no-referrer" \
    "Content-Security-Policy: frame-ancestors 'none'"; do
    if printf '%s' "$cabecalhos" | grep -Fqi "$esperado"; then
        pass "header $esperado"
    else
        fail "header missing: $esperado"
    fi
done

codigo="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 -H 'Host: evil.example.com' "$base/health" || true)"
if [ "$codigo" = "421" ]; then
    pass "Host evil.example.com: 421"
else
    fail "Host evil.example.com: expected 421, got '$codigo'"
fi

painel="$(curl -sS -o /dev/null -w '%{http_code} %{content_type}' --max-time 5 "$base/" || true)"
case "$painel" in
    "200 text/html"*) pass "GET /: $painel" ;;
    *) fail "GET /: expected 200 text/html, got '$painel'" ;;
esac

uid_container="$(docker compose exec -T iphub id -u 2>/dev/null | tr -d '[:space:]' || true)"
if [ -n "$uid_container" ] && [ "$uid_container" != "0" ]; then
    pass "container runs as uid $uid_container (non-root)"
else
    fail "container user: expected non-root, got '$uid_container'"
fi

if [ "$falhas" -eq 0 ]; then
    echo "PASS all checks passed"
    exit 0
fi
echo "FAIL $falhas check(s) failed"
exit 1
