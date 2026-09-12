#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
# Bench smoke. Brings the hub up from zero with compose and walks
# the whole path a bench walks: /health, the security headers, the Host rule, the
# panel, the non-root user, then the password, the catalogue, discovery, a
# registration, a command, a scene and the DP-bus, and tears everything down.
# with an image name: that image is used as is, nothing is built
# without: docker compose build runs first
# COMPOSE_FILE is honored, so on Docker Desktop run:
# COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml scripts/fumaca.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# A project name of its own keeps the smoke volume apart from a real
# installation on the same box, so the "down -v" below never touches real data.
# An inherited name of another installation would aim that "down -v" at it, so
# the smoke refuses to run instead of destroying that data volume.
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
    # The legacy builder is the one the ARM appliance has, so the bench proves it too.
    DOCKER_BUILDKIT=0 docker compose build
fi

# --no-build overrides pull_policy build; without it compose would rebuild
# the image just built, or try to build over the one passed by name.
# The hub binds a fixed port, so a hub already running (host network on the
# appliance, published port on Docker Desktop) makes the smoke time out with a
# message about health that says nothing about the real cause.
if (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null; then
    exec 3>&-
    echo "FAIL port 8080 is already in use; stop the running hub before the smoke" >&2
    exit 1
fi

# The reference ARM appliance ships Compose v2.16, which has --wait but not
# --wait-timeout, and an unknown flag aborts before anything is started.
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

# Says the smoke goes all the way to a scene, so from here on it
# walks the path an integrator walks on the bench. The registered equipment points
# at an address where nothing answers on purpose: a command that comes back
# eq_offline proves the whole path, from the route to the driver and back, without
# the smoke needing hardware.
campo() { grep -o "\"$1\": *\"[^\"]*\"" | head -1 | sed 's/.*: *"\(.*\)"$/\1/'; }

senha="fumaca-de-bancada"
posse="$(curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' \
    -d "{\"senha\": \"$senha\"}" "$base/api/posse" || true)"
token="$(printf '%s' "$posse" | campo token)"
if [ -n "$token" ]; then
    pass "POST /api/posse: the first access set the password and opened a session"
else
    fail "POST /api/posse: expected a token, got '$posse'"
fi

autorizado=(-H "Authorization: Bearer $token")

corpo="$(curl -sS --max-time 10 "${autorizado[@]}" "$base/api/catalogo" || true)"
if printf '%s' "$corpo" | grep -q '"multiroom_linkplay"' \
    && printf '%s' "$corpo" | grep -q '"projetor_pjlink"'; then
    pass "GET /api/catalogo: the native drivers of the image loaded"
else
    fail "GET /api/catalogo: expected the native drivers of the image, got '$corpo'"
fi

codigo="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 60 -X POST "${autorizado[@]}" "$base/api/descoberta" || true)"
if [ "$codigo" = "200" ]; then
    pass "POST /api/descoberta: 200 (finding nothing on this network is the expected answer)"
else
    fail "POST /api/descoberta: expected 200, got '$codigo'"
fi

# The address is the documentation range of RFC 5737, which never routes, so
# the smoke cannot reach a device of somebody else on the bench network.
cadastro='{"tipo": "multiroom_linkplay", "identidade": "fumaca-uuid-1", "nome": "Fumaca", "ip": "192.0.2.10", "campos": {}}'
codigo="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST \
    -H 'Content-Type: application/json' "${autorizado[@]}" -d "$cadastro" "$base/api/equipamentos" || true)"
if [ "$codigo" = "200" ]; then
    pass "POST /api/equipamentos: a multiroom equipment was registered"
else
    fail "POST /api/equipamentos: expected 200, got '$codigo'"
fi

# A number on the app belongs to a licence, so the smoke creates one licence of
# audio and video and gives the equipment its number 1 there.
corpo="$(curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' "${autorizado[@]}" \
    -d '{"produto": "av", "id": "av1", "nome": "Fumaca"}' "$base/api/licencas" || true)"
if printf '%s' "$corpo" | grep -q '"id": *"av1"'; then
    pass "POST /api/licencas: a licence of audio and video was created"
else
    fail "POST /api/licencas: expected the licence av1, got '$corpo'"
fi

codigo="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST \
    -H 'Content-Type: application/json' "${autorizado[@]}" -d '{"numeros": ["fumaca-uuid-1"]}' "$base/api/licencas/av1/numeros" || true)"
if [ "$codigo" = "200" ]; then
    pass "POST /api/licencas/av1/numeros: the equipment took number 1 on the app"
else
    fail "POST /api/licencas/av1/numeros: expected 200, got '$codigo'"
fi

corpo="$(curl -sS --max-time 10 "${autorizado[@]}" "$base/api/licencas/av1/qr" || true)"
if printf '%s' "$corpo" | grep -q '"code": *"licenca_incompleta"'; then
    pass "GET /api/licencas/av1/qr: no uuid and pid yet, so licenca_incompleta and never a key"
else
    fail "GET /api/licencas/av1/qr: expected licenca_incompleta, got '$corpo'"
fi

corpo="$(curl -sS --max-time 20 -X POST -H 'Content-Type: application/json' "${autorizado[@]}" \
    -d '{"acao": "volume", "valor": 30}' "$base/api/equipamentos/fumaca-uuid-1/acao" || true)"
if printf '%s' "$corpo" | grep -q '"code": *"eq_offline"'; then
    pass "POST /api/equipamentos/.../acao: eq_offline, the stable code"
else
    fail "POST /api/equipamentos/.../acao: expected eq_offline, got '$corpo'"
fi

cena='{"cenas": [{"nome": "Fumaca", "passos": [{"equipamento": "fumaca-uuid-1", "acao": "volume", "valor": 30, "espera_ms": 0}]}]}'
codigo="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST \
    -H 'Content-Type: application/json' "${autorizado[@]}" -d "$cena" "$base/api/cenas" || true)"
if [ "$codigo" = "200" ]; then
    pass "POST /api/cenas: a scene of one step was saved"
else
    fail "POST /api/cenas: expected 200, got '$codigo'"
fi

codigo="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -X POST \
    "${autorizado[@]}" "$base/api/cenas/1/executar" || true)"
if [ "$codigo" = "200" ]; then
    pass "POST /api/cenas/1/executar: the scene ran"
else
    fail "POST /api/cenas/1/executar: expected 200, got '$codigo'"
fi

# The remote door of the image: closed, with the three things it still needs listed (no
# relay is set on the bench). A hub that answered "ready" here would be a hub whose door
# opens onto no second factor.
corpo="$(curl -sS --max-time 20 "${autorizado[@]}" "$base/api/remoto" || true)"
case "$corpo" in
    *'"armado": false'*|*'"armado":false'*)
        case "$corpo" in
            *sem_relay*) pass "GET /api/remoto: the remote door is closed and says what it needs" ;;
            *) fail "GET /api/remoto: expected the missing pieces, got '$corpo'" ;;
        esac
        ;;
    *) fail "GET /api/remoto: expected a closed door, got '$corpo'" ;;
esac

# Says the first frame authenticates with the token and the licence, that a
# socket without it is closed with 4401, and that the token never travels in the url. The check runs inside
# the container because the api_token never leaves it, and it is the machine
# credential the DP-bus takes, not the panel session.
saida="$(docker compose exec -T iphub python - <<'PYFIM' 2>&1 || true
import asyncio, pathlib, aiohttp
from iphub import cofre

async def principal():
    # Sealed on disk by the vault; only the key of the daemon opens it.
    escrito = pathlib.Path("/data/api-token.txt").read_text(encoding="utf-8").strip()
    token = cofre.abrir(pathlib.Path("/data")).decifrar(escrito)
    async with aiohttp.ClientSession() as sessao:
        async with sessao.ws_connect("http://127.0.0.1:8080/dpbus") as ws:
            await ws.send_json({"t": "auth", "token": "errado", "licenca": "av1"})
            await ws.receive()
            print("mudo" if ws.close_code == 4401 else f"fechou com {ws.close_code}")
        async with sessao.ws_connect("http://127.0.0.1:8080/dpbus") as ws:
            await ws.send_json({"t": "auth", "token": token, "licenca": "nao-existe"})
            await ws.receive()
            print("sem-licenca" if ws.close_code == 4401 else f"fechou com {ws.close_code}")
        async with sessao.ws_connect("http://127.0.0.1:8080/dpbus") as ws:
            await ws.send_json({"t": "auth", "token": token, "licenca": "av1"})
            await ws.send_json({"t": "consulta", "id": 1})
            quadro = await ws.receive_json()
            print(quadro.get("t"))

asyncio.run(principal())
PYFIM
)"
if printf '%s' "$saida" | grep -q '^mudo$' && printf '%s' "$saida" | grep -q '^sem-licenca$' \
    && printf '%s' "$saida" | grep -q 'snapshot'; then
    pass "/dpbus: a wrong token and an unknown licence close with 4401, and a consulta gets the snapshot"
else
    fail "/dpbus: expected two 4401 closes and a snapshot, got '$saida'"
fi

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
