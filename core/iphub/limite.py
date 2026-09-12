# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Login rate limit: five failures per IP block for fifteen minutes, sixty tries a minute.

Order the routes follow, because one slot of the global window pays for one PBKDF2 on an
ARM board: read the body, check the state of the hub, call permitido(ip), and only then,
immediately before the credential is really checked, call registrar_tentativa(). A request
refused earlier checks no secret and must not spend the window, or any device on the LAN
locks the owner out of login with cheap malformed requests.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

FALHAS_ATE_BLOQUEIO = 5
BLOQUEIO_S = 15 * 60
TETO_GLOBAL = 60
JANELA_GLOBAL_S = 60


@dataclass
class _Tentativas:
    falhas: int = 0
    visto_em: float = 0.0
    bloqueado_ate: float = 0.0


def _esquecivel(entrada: _Tentativas, agora: float) -> bool:
    return entrada.bloqueado_ate <= agora and agora - entrada.visto_em >= BLOQUEIO_S


class Limite:
    """Failure counters per IP plus a global window, in memory, pruned on every call."""

    # The counters live only in memory and are never persisted, so the clock that never
    # walks backwards is the right one; a backwards NTP step on the wall clock would keep the
    # global window shut for the size of the step.
    def __init__(self, agora: Callable[[], float] = time.monotonic) -> None:
        self._agora = agora
        self._por_ip: dict[str, _Tentativas] = {}
        self._global: deque[float] = deque()

    def permitido(self, ip: str) -> bool:
        agora = self._agora()
        self._podar(agora)
        if self._bloqueado_ate(ip, agora) is not None:
            return False
        # Every attempt the routes register costs one PBKDF2 of 200 thousand iterations on
        # an ARM board, so the global window is what keeps a rotating attacker from stalling
        # the daemon, and only such an attempt may fill it.
        return len(self._global) < TETO_GLOBAL

    def registrar_tentativa(self) -> None:
        agora = self._agora()
        self._podar(agora)
        self._global.append(agora)

    def registrar_falha(self, ip: str) -> None:
        agora = self._agora()
        self._podar(agora)
        entrada = self._por_ip.setdefault(ip, _Tentativas())
        entrada.falhas += 1
        entrada.visto_em = agora
        if entrada.falhas >= FALHAS_ATE_BLOQUEIO:
            entrada.falhas = 0
            entrada.bloqueado_ate = agora + BLOQUEIO_S

    def registrar_sucesso(self, ip: str) -> None:
        self._podar(self._agora())
        self._por_ip.pop(ip, None)

    def bloqueado_ate(self, ip: str) -> float | None:
        agora = self._agora()
        self._podar(agora)
        return self._bloqueado_ate(ip, agora)

    def _bloqueado_ate(self, ip: str, agora: float) -> float | None:
        entrada = self._por_ip.get(ip)
        if entrada is None or entrada.bloqueado_ate <= agora:
            return None
        return entrada.bloqueado_ate

    def _podar(self, agora: float) -> None:
        while self._global and agora - self._global[0] >= JANELA_GLOBAL_S:
            self._global.popleft()
        # Without this the map grows with every distinct IP that ever tried to log in.
        for ip in [i for i, e in self._por_ip.items() if _esquecivel(e, agora)]:
            del self._por_ip[ip]
