# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Panel sessions: random token, kept as a hash, idle validity and absolute cap, section 9.

Sessões do painel: token aleatório, guardado em hash, validade ociosa e teto, seção 9.
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
    """Fingerprint written to disk, so reading the file gives no usable token.

    Impressão escrita em disco, para ler o arquivo não entregar token utilizável.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _registro(entrada: object, agora: float) -> dict[str, float] | None:
    if not isinstance(entrada, dict):
        return None
    criada = entrada.get("criada_em")
    usada = entrada.get("usada_em")
    if not isinstance(criada, (int, float)) or not isinstance(usada, (int, float)):
        return None
    # Why: a record dated ahead of now never goes idle, so a file written by a clock that ran
    # forward, or planted by hand, would hold a session that no expiry ever reaches.
    # Por que: um registro datado à frente de agora nunca fica ocioso, então um arquivo escrito
    # por um relógio adiantado, ou plantado na mão, guardaria uma sessão que expiração nenhuma
    # alcança.
    usada_em = min(float(usada), agora)
    return {"criada_em": min(float(criada), agora), "usada_em": usada_em, "gravada_em": usada_em}


def _em_disco(entrada: dict[str, float]) -> dict[str, float]:
    """Only what the format of the file declares; gravada_em lives in memory alone.

    Só o que o formato do arquivo declara; gravada_em vive apenas em memória.
    """
    return {"criada_em": entrada["criada_em"], "usada_em": entrada["usada_em"]}


def _vencida(entrada: dict[str, float], agora: float) -> bool:
    return agora - entrada["usada_em"] > VALIDADE_S or agora - entrada["criada_em"] > TETO_S


def _restante(entrada: dict[str, float], agora: float) -> int:
    fim = min(entrada["usada_em"] + VALIDADE_S, entrada["criada_em"] + TETO_S)
    return max(0, int(fim - agora))


class Sessoes:
    """Session store on sessoes.json, 0600, tokens never written in clear.

    Repositório de sessões em sessoes.json, 0600, tokens nunca escritos em claro.
    """

    def __init__(self, dir_data: Path, agora: Callable[[], float] = time.time) -> None:
        self._caminho = Path(dir_data) / ARQUIVO
        self._agora = agora
        self._falha_registrada = False
        self._sessoes, danificado = self._ler()
        if danificado:
            # Why: a panel nobody can log into is worse than losing the open sessions.
            # Por que: um painel em que ninguém consegue entrar é pior do que perder as
            # sessões abertas.
            self._gravar()

    def criar(self) -> tuple[str, int]:
        token = gerar_token()
        agora = self._agora()
        entrada = {"criada_em": agora, "usada_em": agora, "gravada_em": agora}
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
        # Why: the idle rule is measured on this value, and the file only has to agree with it
        # closely enough to survive a restart; rewriting on every request wears the eMMC of the
        # appliance for nothing.
        # Por que: a regra de ociosidade é medida sobre este valor, e o arquivo só precisa
        # concordar com ele o bastante para sobreviver a um reinício; reescrever a cada
        # requisição gasta o eMMC do appliance à toa.
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

    def revogar(self, token: str) -> None:
        self._sessoes.pop(impressao(token), None)
        self._gravar()

    def revogar_todas(self) -> None:
        self._sessoes.clear()
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
        """The store in memory is the truth; the file is how it survives a restart.

        O repositório em memória é a verdade; o arquivo é como ele sobrevive a um reinício.
        """
        self._purgar(self._agora())
        em_disco = {chave: _em_disco(e) for chave, e in self._sessoes.items()}
        try:
            escrever_json(self._caminho, {"schema_version": SCHEMA_VERSION, "sessoes": em_disco})
        except OSError as erro:
            # Why: a full or read only data volume must not turn a revocation into an error the
            # caller retries, nor break every authenticated request; the revocation still holds
            # for the life of this process. Logged once, and never with a token in it.
            # Por que: um volume de dados cheio ou somente leitura não pode transformar uma
            # revogação em erro que o chamador repete, nem quebrar toda requisição autenticada;
            # a revogação continua valendo enquanto este processo viver. Registrado uma vez, e
            # nunca com um token dentro.
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
