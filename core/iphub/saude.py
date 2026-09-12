# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Container healthcheck: exit 0 only when GET /health answers 200 with "ok": true."""

import json
import sys
import urllib.request

from iphub.ambiente import Ambiente

TIMEOUT_S = 4.0
CURINGAS = frozenset({"", "0.0.0.0", "::", "*"})


def alvo_da_sonda(bind: str, porta: int) -> str:
    """A wildcard bind is probed on loopback; any other address is probed where it listens."""
    endereco = "127.0.0.1" if bind in CURINGAS else bind
    if ":" in endereco:
        endereco = f"[{endereco}]"
    return f"http://{endereco}:{porta}/health"


def verificar(url: str, timeout_s: float = TIMEOUT_S) -> bool:
    # A proxy variable inherited from the host must not route a loopback probe elsewhere.
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with abridor.open(url, timeout=timeout_s) as resposta:
            if resposta.status != 200:
                return False
            corpo = json.loads(resposta.read().decode("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(corpo, dict) and corpo.get("ok") is True


def main() -> int:
    amb = Ambiente.do_ambiente()
    return 0 if verificar(alvo_da_sonda(amb.bind, amb.porta)) else 1


if __name__ == "__main__":
    sys.exit(main())
