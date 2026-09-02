#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
#
# Deploys the repository to a Linux box over ssh and builds the image there
# with the legacy builder and host networking, because the reference ARM
# appliance has neither BuildKit nor a bridge network. Then brings the hub up
# with compose and waits for /health.
# Usage: scripts/implantar.sh usuario@host [dir_remoto=~/tuya-ip-hub]
#   dir_remoto without a leading slash is relative to the remote home.
#
# Implanta o repositório numa máquina Linux por ssh e constrói a imagem lá com
# o builder legado e rede do host, porque o appliance ARM de referência não
# tem BuildKit nem rede bridge. Depois sobe o hub com compose e espera o /health.
# Uso: scripts/implantar.sh usuario@host [dir_remoto=~/tuya-ip-hub]
#   dir_remoto sem barra inicial é relativo à home remota.
set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "usage: $0 usuario@host [dir_remoto=~/tuya-ip-hub]" >&2
    exit 2
fi

destino="$1"
dir_remoto="${2-tuya-ip-hub}"
# Why: "~/" only expands in the remote shell, and only unquoted; stripping it
# here makes the path relative to the remote home in both rsync and ssh.
# Por que: "~/" só expande no shell remoto, e só sem aspas; tirar aqui deixa o
# caminho relativo à home remota tanto no rsync quanto no ssh.
dir_remoto="${dir_remoto#\~/}"
# Why: the rsync below runs with --delete, so a destination like "." or "~"
# would erase the remote home instead of a project folder of its own.
# Por que: o rsync abaixo roda com --delete, então um destino como "." ou "~"
# apagaria a home remota em vez de uma pasta de projeto própria.
case "$dir_remoto" in
    "" | "." | ".." | "/" | "~" | */. | */..)
        echo "FAIL dir_remoto '${2-}' is not a project folder: rsync --delete would erase the remote home" >&2
        exit 2
        ;;
esac
imagem="ghcr.io/queroautomacao/tuya-ip-hub:latest"

cd "$(dirname "$0")/.."

# Why: the repository rules decide what travels, so a manufacturer document or
# a local env file that git keeps out never reaches the customer appliance; a
# hand written list would drift from .gitignore at the first new rule there.
# Por que: as regras do repositório decidem o que viaja, então um documento de
# fabricante ou um arquivo de ambiente local que o git deixa de fora nunca chega
# ao appliance do cliente; uma lista escrita à mão se afastaria do .gitignore na
# primeira regra nova de lá.
rsync -a --delete \
    --filter=':- .gitignore' \
    --exclude .git \
    --exclude interno \
    ./ "$destino:$dir_remoto/"

ssh "$destino" "bash -s -- $(printf '%q ' "$dir_remoto" "$imagem")" <<'REMOTO'
set -euo pipefail
dir="$1"
imagem="$2"
cd "$dir"
DOCKER_BUILDKIT=0 docker build --network host -t "$imagem" .
docker compose up -d --no-build
url="http://127.0.0.1:8080/health"
for _ in $(seq 1 60); do
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 3 "$url" >/dev/null 2>&1 && { echo "PASS $url"; exit 0; }
    else
        python3 -c "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('$url',timeout=3).status==200 else 1)" >/dev/null 2>&1 && { echo "PASS $url"; exit 0; }
    fi
    sleep 2
done
echo "FAIL $url did not answer in 120 s"
docker compose logs --no-color --tail 50 iphub || true
exit 1
REMOTO
