# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Panel sessions: random token, kept as a hash, idle validity and absolute cap.

A session born through the relay carries one more date: the end of the window it was opened
in. The code of the window is checked once, at the login, so without this date a token taken
from a browser would keep working in every window opened afterwards, with no code asked; with
it, the next window asks for its own code again.
"""

import hashlib
import logging
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from iphub.arquivos import escrever_json, ler_json
from iphub.versao import SCHEMA_VERSION

ARQUIVO = "sessoes.json"
VALIDADE_S = 24 * 3600
TETO_S = 30 * 24 * 3600
PERSISTIR_APOS_S = 60

log = logging.getLogger("iphub.sessoes")


def gerar_token() -> str:
    return secrets.token_urlsafe(32)


def impressao(token: str) -> str:
    """Fingerprint written to disk, so reading the file gives no usable token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _registro(entrada: object, agora: float) -> dict[str, float] | None:
    if not isinstance(entrada, dict):
        return None
    criada = entrada.get("criada_em")
    usada = entrada.get("usada_em")
    if not isinstance(criada, (int, float)) or not isinstance(usada, (int, float)):
        return None
    # A session written by an older daemon carries one factor, which is what it had.
    fatores = entrada.get("fatores", 1)
    fatores = fatores if isinstance(fatores, int) and not isinstance(fatores, bool) else 1
    # A record dated ahead of now never goes idle, so a file written by a clock that ran
    # forward, or planted by hand, would hold a session that no expiry ever reaches.
    usada_em = min(float(usada), agora)
    valida_ate = entrada.get("valida_ate")
    if not isinstance(valida_ate, (int, float)) or isinstance(valida_ate, bool):
        valida_ate = 0.0
    return {
        "criada_em": min(float(criada), agora),
        "usada_em": usada_em,
        "gravada_em": usada_em,
        "fatores": max(1, min(2, fatores)),
        "valida_ate": float(valida_ate),
    }


def _em_disco(entrada: dict[str, float]) -> dict[str, float]:
    """Only what the format of the file declares; gravada_em lives in memory alone."""
    return {
        "criada_em": entrada["criada_em"],
        "usada_em": entrada["usada_em"],
        "fatores": entrada["fatores"],
        "valida_ate": entrada["valida_ate"],
    }


def _remota(entrada: dict[str, float]) -> bool:
    """Whether this session was opened through the relay: those carry the end of a window."""
    return entrada["valida_ate"] > 0


def _vencida(entrada: dict[str, float], agora: float) -> bool:
    if _remota(entrada) and agora >= entrada["valida_ate"]:
        return True
    return agora - entrada["usada_em"] > VALIDADE_S or agora - entrada["criada_em"] > TETO_S


def _restante(entrada: dict[str, float], agora: float) -> int:
    fim = min(entrada["usada_em"] + VALIDADE_S, entrada["criada_em"] + TETO_S)
    if _remota(entrada):
        fim = min(fim, entrada["valida_ate"])
    return max(0, int(fim - agora))


class Sessoes:
    """Session store on sessoes.json, 0600, tokens never written in clear."""

    def __init__(self, dir_data: Path, agora: Callable[[], float] = time.time) -> None:
        self._caminho = Path(dir_data) / ARQUIVO
        self._agora = agora
        self._falha_registrada = False
        self._sessoes, danificado = self._ler()
        if danificado:
            # A panel nobody can log into is worse than losing the open sessions.
            self._gravar()

    def criar(self, fatores: int = 1, valida_ate: float = 0.0) -> tuple[str, int]:
        """A session, how many factors opened it, and, through the relay, when its window ends.

        The remote door asks two factors. A session opened through it is good until the
        window it was opened in closes, and not a second longer, whatever the idle rule says.
        """
        token = gerar_token()
        agora = self._agora()
        entrada = {
            "criada_em": agora,
            "usada_em": agora,
            "gravada_em": agora,
            "fatores": max(1, min(2, fatores)),
            "valida_ate": max(0.0, float(valida_ate)),
        }
        self._sessoes[impressao(token)] = entrada
        self._gravar()
        return token, _restante(entrada, agora)

    def validar(self, token: str | None) -> bool:
        if not token:
            return False
        chave = impressao(token)
        entrada = self._sessoes.get(chave)
        if entrada is None:
            return False
        agora = self._agora()
        if _vencida(entrada, agora):
            del self._sessoes[chave]
            self._gravar()
            return False
        # The idle rule is measured on this value, and the file only has to agree with it
        # closely enough to survive a restart; rewriting on every request wears the eMMC of the
        # appliance for nothing.
        entrada["usada_em"] = agora
        if agora - entrada["gravada_em"] >= PERSISTIR_APOS_S:
            self._gravar()
        return True

    def expira_em_s(self, token: str | None) -> int:
        if not token:
            return 0
        entrada = self._sessoes.get(impressao(token))
        if entrada is None:
            return 0
        return _restante(entrada, self._agora())

    def fatores_de(self, token: str | None) -> int:
        """How many factors opened this session; zero for a token nobody knows."""
        if not token:
            return 0
        entrada = self._sessoes.get(impressao(token))
        return int(entrada["fatores"]) if entrada is not None else 0

    def revogar(self, token: str) -> None:
        self._sessoes.pop(impressao(token), None)
        self._gravar()

    def revogar_todas(self) -> None:
        self._sessoes.clear()
        self._gravar()

    def revogar_remotas(self) -> None:
        """Every session opened through the relay: what closing the window by hand takes."""
        for chave in [c for c, e in self._sessoes.items() if _remota(e)]:
            del self._sessoes[chave]
        self._gravar()

    def quantidade(self) -> int:
        self._purgar(self._agora())
        return len(self._sessoes)

    def _ler(self) -> tuple[dict[str, dict[str, float]], bool]:
        try:
            dados = ler_json(self._caminho)
        except (OSError, ValueError):
            return {}, True
        if dados is None:
            return {}, False
        bruto = dados.get("sessoes")
        if dados.get("schema_version") != SCHEMA_VERSION or not isinstance(bruto, dict):
            return {}, True
        agora = self._agora()
        sessoes: dict[str, dict[str, float]] = {}
        for chave, entrada in bruto.items():
            registro = _registro(entrada, agora)
            if isinstance(chave, str) and registro is not None:
                sessoes[chave] = registro
        return sessoes, len(sessoes) != len(bruto)

    def _purgar(self, agora: float) -> None:
        for chave in [c for c, e in self._sessoes.items() if _vencida(e, agora)]:
            del self._sessoes[chave]

    def _gravar(self) -> None:
        """The store in memory is the truth; the file is how it survives a restart."""
        self._purgar(self._agora())
        em_disco = {chave: _em_disco(e) for chave, e in self._sessoes.items()}
        try:
            escrever_json(self._caminho, {"schema_version": SCHEMA_VERSION, "sessoes": em_disco})
        except OSError as erro:
            # A full or read only data volume must not turn a revocation into an error the
            # caller retries, nor break every authenticated request; the revocation still holds
            # for the life of this process. Logged once, and never with a token in it.
            if not self._falha_registrada:
                self._falha_registrada = True
                log.error(
                    "could not write %s (%s); sessions stay in memory until the daemon restarts",
                    self._caminho.name,
                    erro.strerror or erro,
                )
            return
        for entrada in self._sessoes.values():
            entrada["gravada_em"] = entrada["usada_em"]
