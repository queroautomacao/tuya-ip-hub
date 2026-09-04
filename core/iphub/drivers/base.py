# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6: the one class every device is, from a relay to a multiroom speaker.

Seção 6: a única classe que todo aparelho é, de um relé a uma caixa multiroom.
"""

from dataclasses import replace
from typing import Protocol

from iphub.drivers.manifesto import Auth, Estado, Manifesto

# What autenticar may answer, and nothing else.
# O que o autenticar pode responder, e nada mais.
RESULTADOS = ("pareado", "aguardando", "falhou")

# The stable codes executar may answer, section 6; the API translates none of them.
# Os códigos estáveis que o executar pode responder, seção 6; a API não traduz nenhum.
CODIGOS = ("nao_suportado", "eq_offline", "invalid_value", "auth_pendente", "erro_aparelho")

NAO_SUPORTADO = "nao_suportado"
PAREADO = "pareado"

TIPO_DESCONHECIDO = "tipo_desconhecido"
CONTRATO_QUEBRADO = "contrato_quebrado"

# Why: Estado.detalhe reaches the panel, and section 11 says the daemon never answers a
# phrase, so it carries the empty string or one code of this vocabulary and nothing else;
# what a device or an exception said goes to the log.
# Por que: o Estado.detalhe chega ao painel, e a seção 11 diz que o daemon nunca responde
# frase, então ele carrega a string vazia ou um código deste vocabulário e nada mais; o que
# um aparelho ou uma exceção disse vai para o log.
DETALHES = (*CODIGOS, TIPO_DESCONHECIDO, CONTRATO_QUEBRADO)


class Cadastro(Protocol):
    """What a driver reads from its registration; config.Cadastro satisfies this shape.

    O que um driver lê do seu cadastro; o config.Cadastro satisfaz esta forma.
    """

    identidade: str
    ip: str
    campos: dict[str, str]
    segredos: dict[str, str]


class AutenticacaoNaoImplementada(NotImplementedError):
    """A driver that needs pairing inherited the base autenticar and never overrode it.

    Um driver que precisa de pareamento herdou o autenticar da base e nunca o sobrescreveu.
    """


class Driver:
    """The contract: a manifest, a lifecycle, one typed state and one stable code back.

    O contrato: um manifesto, um ciclo de vida, um estado tipado e um código estável de volta.
    """

    MANIFESTO: Manifesto

    def __init__(self, cadastro: Cadastro) -> None:
        self.cadastro = cadastro
        self._estado = Estado(online=False)

    async def iniciar(self) -> None:
        """Opens whatever the driver keeps open. It does NOT authenticate.

        Abre o que o driver mantiver aberto. NÃO autentica.
        """

    async def parar(self) -> None:
        """Closes what iniciar opened; called even when iniciar failed.

        Fecha o que o iniciar abriu; chamado mesmo quando o iniciar falhou.
        """

    async def autenticar(self) -> str:
        """Refuses the inherited default when the manifest declares an auth, section 6.

        Recusa o padrão herdado quando o manifesto declara uma auth, seção 6.
        """
        # Why: a base that answered "pareado" here would tell the panel a TV is paired while
        # every command still fails, and the integrator would hunt the network instead of the
        # driver. Failing loudly at the contract is the cheapest place to find it.
        # Por que: uma base que respondesse "pareado" aqui diria ao painel que uma TV está
        # pareada enquanto todo comando falha, e o integrador caçaria a rede em vez do driver.
        # Falhar alto no contrato é o lugar mais barato de achar isso.
        if self.MANIFESTO.auth != Auth.NENHUMA:
            raise AutenticacaoNaoImplementada(
                f"{type(self).__name__} declares auth {self.MANIFESTO.auth!r} and must "
                f"implement autenticar; the base refuses to pretend success"
            )
        return PAREADO

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The identity of the device at that address, asked with no registration at all.

        Why: discovery finds an address, and section 6 registers an identity; a driver that
        can ask the device who it is turns a finding into a registration the operator does
        not have to type. None means this driver cannot ask, and the sweep says so instead.

        A identidade do aparelho naquele endereço, perguntada sem cadastro nenhum.

        Por que: a descoberta acha um endereço, e a seção 6 cadastra uma identidade; um driver
        que sabe perguntar ao aparelho quem ele é transforma um achado num cadastro que o
        operador não precisa digitar. None diz que este driver não sabe perguntar, e a
        varredura diz isso em vez dele.
        """
        del ip
        return None

    async def atualizar(self) -> None:
        """One poll. The manager calls it on its own interval, never the driver.

        Um poll. O gestor o chama no intervalo dele, nunca o driver.
        """

    def estado(self) -> Estado:
        return self._estado

    async def executar(self, acao: str, valor: object = None) -> str | None:
        """None for done, or one of CODIGOS. The manager already refused what is not declared.

        None para feito, ou um de CODIGOS. O gestor já recusou o que não está declarado.
        """
        return NAO_SUPORTADO

    def _defina(self, **campos: object) -> None:
        """Replaces the state whole, so no reader ever sees a half built Estado.

        Troca o estado inteiro, para nenhum leitor ver um Estado montado pela metade.
        """
        self._estado = replace(self._estado, **campos)
