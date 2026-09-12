# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The registry of homes: which installations this relay serves, asked of the maker's Directus.

The code of the hub is public, so nothing INSIDE a hub can be the thing that decides whether
it may use the relay of the maker; a check there is a line somebody deletes. The decision
lives here, and the rule is the one the maker keeps in its own registry: an installation is
served while the home of the app it belongs to is registered there, by whoever the maker
lets register homes. The hub tells the relay its home when it dials; this asks the registry.

This file knows one question and one answer: is there a record whose field equals this home.
The collection, the field and the token are settings, so the shape of the registry is the
maker's to change without touching this.
"""

import json
import logging
import re
import time
from collections.abc import Callable

from aiohttp import ClientError, ClientSession, ClientTimeout
from yarl import URL

log = logging.getLogger("relay.cadastro")

# The id of a home on the platform is a number; anything else is not a home and is not
# even asked about, which keeps a stranger from writing a filter of their own.
HOME = re.compile(r"[0-9]{1,20}")
NOME = re.compile(r"[A-Za-z0-9_]{1,64}")
PRAZO_S = 5.0
RESPOSTA_MAXIMA = 64 * 1024
# A home confirmed stays confirmed for this long without asking again: a hub whose link
# flaps dials several times in a minute, and the registry does not change by the minute.
CACHE_S = 300.0
CACHE_MAXIMO = 20_000


def home_valido(texto: object) -> bool:
    return isinstance(texto, str) and HOME.fullmatch(texto) is not None


class Indisponivel(Exception):
    """The registry could not be asked; the answer is neither yes nor no."""


class Cadastro:
    """One question to Directus: is this home registered."""

    def __init__(
        self,
        url: str,
        token: str,
        colecao: str,
        campo: str,
        agora: Callable[[], float] = time.monotonic,
    ) -> None:
        if not NOME.fullmatch(colecao) or not NOME.fullmatch(campo):
            raise ValueError("the collection and the field are names of Directus")
        self._url = URL(url.rstrip("/"))
        if self._url.scheme not in ("http", "https") or not self._url.host:
            raise ValueError("the address of Directus must be http or https")
        self._token = token
        self._colecao = colecao
        self._campo = campo
        self._agora = agora
        self._sessao: ClientSession | None = None
        self._confirmados: dict[str, float] = {}

    async def cadastrado(self, home: str) -> bool:
        """True for a registered home, False for one nobody registered; raises Indisponivel."""
        confirmado = self._confirmados.get(home)
        if confirmado is not None and self._agora() - confirmado < CACHE_S:
            return True
        # The brackets travel as brackets, the way the app of the maker writes this same
        # query; every piece of it was validated above, so nothing here needs escaping.
        consulta = f"filter[{self._campo}][_eq]={home}&limit=1&fields={self._campo}"
        url = URL(f"{self._url}/items/{self._colecao}?{consulta}", encoded=True)
        cabecalhos = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            async with self._abrir().get(url, headers=cabecalhos, allow_redirects=False) as r:
                if r.status != 200:
                    raise Indisponivel(f"directus answered {r.status}")
                bruto = await r.content.read(RESPOSTA_MAXIMA + 1)
        except (TimeoutError, ClientError, OSError) as erro:
            raise Indisponivel(str(erro) or type(erro).__name__) from erro
        if len(bruto) > RESPOSTA_MAXIMA:
            raise Indisponivel("directus answered more than this reads")
        try:
            dados = _json(bruto)
        except ValueError as erro:
            raise Indisponivel("directus did not answer json") from erro
        registros = dados.get("data")
        if not isinstance(registros, list):
            raise Indisponivel("directus answered without data")
        if not registros:
            return False
        # The record that came back has to be the home that was asked about: a registry
        # that ignored the filter would answer its first record to every question, and a
        # role that cannot read the field would answer a record with nothing in it. Both
        # read as "not registered", and the log says why every hub is being refused.
        primeiro = registros[0]
        valor = primeiro.get(self._campo) if isinstance(primeiro, dict) else None
        if valor is None or str(valor).strip() != home:
            log.warning(
                "the registry answered a record that is not the home asked about; "
                "check the field %s and the permissions of the token",
                self._campo,
            )
            return False
        self._lembrar(home)
        return True

    async def fechar(self) -> None:
        sessao, self._sessao = self._sessao, None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    def _abrir(self) -> ClientSession:
        if self._sessao is None or self._sessao.closed:
            self._sessao = ClientSession(timeout=ClientTimeout(total=PRAZO_S))
        return self._sessao

    def _lembrar(self, home: str) -> None:
        agora = self._agora()
        if len(self._confirmados) >= CACHE_MAXIMO:
            vencidos = [h for h, t in self._confirmados.items() if agora - t >= CACHE_S]
            for h in vencidos:
                del self._confirmados[h]
            if len(self._confirmados) >= CACHE_MAXIMO:
                self._confirmados.clear()
        self._confirmados[home] = agora


def _json(bruto: bytes) -> dict:
    dados = json.loads(bruto)
    if not isinstance(dados, dict):
        raise ValueError("not an object")
    return dados
