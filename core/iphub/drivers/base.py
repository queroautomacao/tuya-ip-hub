# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The one class every device is, from a relay to a multiroom speaker."""

from dataclasses import dataclass, replace
from typing import Protocol

from iphub.drivers.manifesto import Auth, Estado, Manifesto

# What autenticar may answer, and nothing else.
RESULTADOS = ("pareado", "aguardando", "falhou")

# The stable codes executar may answer; the API translates none of them.
CODIGOS = ("nao_suportado", "eq_offline", "invalid_value", "auth_pendente", "erro_aparelho")

NAO_SUPORTADO = "nao_suportado"
PAREADO = "pareado"

TIPO_DESCONHECIDO = "tipo_desconhecido"
CONTRATO_QUEBRADO = "contrato_quebrado"

# Estado.detalhe reaches the panel, and says the daemon never answers a
# phrase, so it carries the empty string or one code of this vocabulary and nothing else;
# what a device or an exception said goes to the log.
DETALHES = (*CODIGOS, TIPO_DESCONHECIDO, CONTRATO_QUEBRADO)


class Cadastro(Protocol):
    """What a driver reads from its registration; config.Cadastro satisfies this shape."""

    identidade: str
    ip: str
    campos: dict[str, str]
    segredos: dict[str, str]


class AutenticacaoNaoImplementada(NotImplementedError):
    """A driver that needs pairing inherited the base autenticar and never overrode it."""


@dataclass(frozen=True)
class Aparelho:
    """One device an account reaches: the id it is commanded by and the name it has there."""

    id: str
    nome: str


class Driver:
    """The contract: a manifest, a lifecycle, one typed state and one stable code back."""

    MANIFESTO: Manifesto

    def __init__(self, cadastro: Cadastro) -> None:
        self.cadastro = cadastro
        self._estado = Estado(online=False)

    async def iniciar(self) -> None:
        """Opens whatever the driver keeps open. It does NOT authenticate."""

    async def parar(self) -> None:
        """Closes what iniciar opened; called even when iniciar failed."""

    async def autenticar(self) -> str:
        """Refuses the inherited default when the manifest declares an auth,."""
        # A base that answered "pareado" here would tell the panel a TV is paired while
        # every command still fails, and the integrator would hunt the network instead of the
        # driver. Failing loudly at the contract is the cheapest place to find it.
        if self.MANIFESTO.auth != Auth.NENHUMA:
            raise AutenticacaoNaoImplementada(
                f"{type(self).__name__} declares auth {self.MANIFESTO.auth!r} and must "
                f"implement autenticar; the base refuses to pretend success"
            )
        return PAREADO

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The identity of the device at that address, asked with no registration at all.

        Discovery finds an address, and registers an identity; a driver that
        can ask the device who it is turns a finding into a registration the operator does
        not have to type. None means this driver cannot ask, and the sweep says so instead.
        """
        del ip
        return None

    async def aparelhos_da_conta(self) -> tuple["Aparelho", ...]:
        """What the credentials of this registration can reach, for the operator to choose from.

        A driver of the cloud of a maker is registered by a token, and the device it will
        command is one of several the account has, named by an id that exists only inside that
        account. Making the operator find that id and type it is making him do what the account
        already answers; here the hub asks, and he picks from a list. Empty means this driver
        has nothing to list, which is every driver whose device is at an address.
        """
        return ()

    def credenciais_do_aparelho(self) -> dict[str, str]:
        """The fields the DEVICE handed over while pairing, for the caller to persist.

        A pairing that lives in the memory of the driver is a pairing that dies with the
        daemon, and the person walks to the television again on the next deploy. The device
        gives the credential, the driver reads it, and only the caller owns the file, so the
        driver hands it over here and never writes anything. The field named is a field of the
        manifest, so one declared SEGREDO lands where keeps a secret and never comes
        back to the panel. Empty means this driver got nothing to keep.
        """
        return {}

    async def atualizar(self) -> None:
        """One poll. The manager calls it on its own interval, never the driver."""

    def estado(self) -> Estado:
        return self._estado

    async def executar(self, acao: str, valor: object = None) -> str | None:
        """None for done, or one of CODIGOS. The manager already refused what is not declared."""
        return NAO_SUPORTADO

    def _defina(self, **campos: object) -> None:
        """Replaces the state whole, so no reader ever sees a half built Estado."""
        self._estado = replace(self._estado, **campos)
