#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
# Deploys the repository to a Linux box over ssh and builds the image there
# with the legacy builder and host networking, because the reference ARM
# appliance has neither BuildKit nor a bridge network. Then brings the hub up
# with compose and waits for /health.
# Usage: scripts/implantar.sh usuario@host [dir_remoto=~/tuya-ip-hub]
# dir_remoto without a leading slash is relative to the remote home.
set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "usage: $0 usuario@host [dir_remoto=~/tuya-ip-hub]" >&2
    exit 2
fi

destino="$1"
dir_remoto="${2-tuya-ip-hub}"
# "~/" only expands in the remote shell, and only unquoted; stripping it
# here makes the path relative to the remote home in both rsync and ssh.
dir_remoto="${dir_remoto#\~/}"
# The rsync below runs with --delete, so a destination like "." or "~"
# would erase the remote home instead of a project folder of its own.
case "$dir_remoto" in
    "" | "." | ".." | "/" | "~" | */. | */..)
        echo "FAIL dir_remoto '${2-}' is not a project folder: rsync --delete would erase the remote home" >&2
        exit 2
        ;;
esac
imagem="ghcr.io/queroautomacao/tuya-ip-hub:latest"

cd "$(dirname "$0")/.."

# The repository rules decide what travels, so a manufacturer document or
# a local env file that git keeps out never reaches the customer appliance; a
# hand written list would drift from.gitignore at the first new rule there.
#
# The protect rules come FIRST and are not a repetition of the ignore file: what
# .gitignore does is keep a file from travelling, and what "P" does is keep the one that is
# ALREADY on the appliance from being deleted by --delete. The .env of an appliance is
# written once, at the factory or by hand, and carries the address of the relay of the
# fleet; a deploy that erased it would take the remote access of that hub down with it, and
# it did, until this line existed.
rsync -a --delete \
    --filter='P /.env' \
    --filter='P /.env.*' \
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
