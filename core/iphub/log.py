# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The diary of the hub: the last lines of what the daemon did, kept in memory for the panel
to show and for a report to carry.

Why: what a driver put on the wire, what the bridge of the platform asked for and what the
panel changed are three stories that only make sense read together, in order, on the same
screen. A container log tells them, but the integrator standing in a house has a browser and
not a shell, and asking for `docker logs` is asking for the one thing a tablet cannot do.

The ceiling is the whole point: a hub runs for months, so the diary keeps the LAST lines and
forgets the rest, counting what it dropped so nobody reads a hole as silence. Nothing here
touches disk: a diary that survived a reboot would be a database, and this is a window.

O log do hub: as últimas linhas do que o daemon fez, guardadas em memória para o painel
mostrar e para um relato levar.

Por que: o que um driver pôs no fio, o que a ponte da plataforma pediu e o que o painel mudou
são três histórias que só fazem sentido lidas juntas, em ordem, na mesma tela. O log do
container as conta, mas o integrador de pé numa casa tem um navegador e não um shell, e pedir
`docker logs` é pedir justamente o que um tablet não faz.

O teto é o ponto: um hub roda por meses, então o log guarda as ÚLTIMAS linhas e esquece o
resto, contando o que descartou para ninguém ler um buraco como silêncio. Nada aqui toca o
disco: um log que sobrevivesse a um reboot seria um banco de dados, e isto é uma janela.
"""

import logging
from collections import deque
from dataclasses import dataclass

# Why: a thousand lines is a few minutes of a busy hub and about 200 kB of memory, which is
# what a diagnosis needs and what a placa with 512 MB never notices; the panel reads them all
# at once, so the ceiling is also what keeps one GET small.
# Por que: mil linhas são alguns minutos de um hub movimentado e uns 200 kB de memória, que é
# o que um diagnóstico precisa e o que uma placa de 512 MB nunca sente; o painel as lê todas
# de uma vez, então o teto é também o que mantém um GET pequeno.
LINHAS_MAXIMO = 1000

# Why: a device that answers a megabyte of garbage would put that megabyte in every line of
# the diary; the message is cut where a human stops reading anyway.
# Por que: um aparelho que responde um megabyte de lixo poria esse megabyte em cada linha do
# log; a mensagem é cortada onde um humano para de ler de todo jeito.
MENSAGEM_MAXIMA = 600

# The origins the panel groups the lines by, decided by the logger that wrote each one.
# As origens pelas quais o painel agrupa as linhas, decididas pelo logger que escreveu cada uma.
ORIGEM_DRIVER = "driver"
ORIGEM_TUYA = "tuya"
ORIGEM_PAINEL = "painel"
ORIGEM_HUB = "hub"
ORIGENS = (ORIGEM_DRIVER, ORIGEM_TUYA, ORIGEM_PAINEL, ORIGEM_HUB)

# Which prefix of a logger name belongs to which origin, longest prefix first.
# Qual prefixo de nome de logger pertence a qual origem, prefixo mais longo primeiro.
_ORIGEM_POR_PREFIXO = (
    ("iphub.drivers", ORIGEM_DRIVER),
    ("iphub.dpbus", ORIGEM_TUYA),
    ("iphub.api", ORIGEM_PAINEL),
    ("iphub.cenas", ORIGEM_TUYA),
)


@dataclass(frozen=True)
class Linha:
    """One line of the diary, as the panel reads it.

    Uma linha do log, como o painel a lê.
    """

    # Why: the clock of the record and not of the reading, because a line is read minutes
    # after it happened and the order between two lines is the whole value of a log.
    # Por que: o relógio do registro e não o da leitura, porque uma linha é lida minutos depois
    # de acontecer e a ordem entre duas linhas é todo o valor de um log.
    instante: float
    nivel: str
    origem: str
    onde: str
    mensagem: str

    def como_json(self) -> dict:
        return {
            "t": round(self.instante, 3),
            "nivel": self.nivel,
            "origem": self.origem,
            "onde": self.onde,
            "texto": self.mensagem,
        }


def origem_de(nome: str) -> str:
    """The origin of a line, from the name of the logger that wrote it.

    A origem de uma linha, pelo nome do logger que a escreveu.
    """
    for prefixo, origem in _ORIGEM_POR_PREFIXO:
        if nome == prefixo or nome.startswith(f"{prefixo}."):
            return origem
    return ORIGEM_HUB


def onde_de(nome: str) -> str:
    """The short name of the module that wrote the line, which is what a reader recognises.

    O nome curto do módulo que escreveu a linha, que é o que um leitor reconhece.
    """
    return nome.removeprefix("iphub.").split(".")[-1] or nome


class Log(logging.Handler):
    """A ring of the last lines, filled by the logging of the whole daemon.

    Um anel das últimas linhas, preenchido pelo logging do daemon inteiro.
    """

    def __init__(self, limite: int = LINHAS_MAXIMO) -> None:
        super().__init__(level=logging.DEBUG)
        self._linhas: deque[Linha] = deque(maxlen=max(1, limite))
        self.descartadas = 0

    def emit(self, record: logging.LogRecord) -> None:
        # Why: a handler that raises takes the call that was logging with it, and a defect in
        # formatting a message must never be able to break a poll or a command.
        # Por que: um handler que estoura leva junto a chamada que estava logando, e um defeito
        # ao formatar uma mensagem nunca pode quebrar um poll ou um comando.
        try:
            mensagem = record.getMessage()
        except Exception:
            mensagem = f"<unformattable {record.msg!r}>"
        if record.exc_info:
            # Why: the traceback of an unexpected failure is the one thing worth more than the
            # message, but only its last line fits a diary; the container log keeps the rest.
            # Por que: o traceback de uma falha inesperada é a única coisa que vale mais que a
            # mensagem, mas só a última linha dele cabe num log; o log do container guarda
            # o resto.
            excecao = record.exc_info[1]
            if excecao is not None:
                mensagem = f"{mensagem} [{type(excecao).__name__}: {excecao}]"
        if len(self._linhas) == self._linhas.maxlen:
            self.descartadas += 1
        self._linhas.append(
            Linha(
                instante=record.created,
                nivel=record.levelname.lower(),
                origem=origem_de(record.name),
                onde=onde_de(record.name),
                mensagem=_apara(mensagem),
            )
        )

    def linhas(self) -> tuple[Linha, ...]:
        return tuple(self._linhas)

    def limpar(self) -> None:
        self._linhas.clear()
        self.descartadas = 0


def _apara(mensagem: str) -> str:
    # Why: a control character in a message written by a device would break the line of the
    # panel and, in a copied report, the file it lands in.
    # Por que: um caractere de controle numa mensagem escrita por um aparelho quebraria a
    # linha do painel e, num relato copiado, o arquivo em que ela cai.
    limpo = "".join(caractere if caractere.isprintable() else " " for caractere in mensagem)
    if len(limpo) <= MENSAGEM_MAXIMA:
        return limpo
    return f"{limpo[:MENSAGEM_MAXIMA]}..."


def instalar(limite: int = LINHAS_MAXIMO) -> Log:
    """Puts a diary under the logging of the daemon and answers it.

    Why: the level of the diary is its own, so the container log stays at INFO while the panel
    still sees every command a driver wrote; the propagation to the root handler is untouched.

    Põe um log sob o logging do daemon e o devolve.

    Por que: o nível do log é próprio dele, então o log do container fica em INFO enquanto o
    painel ainda vê todo comando que um driver escreveu; a propagação para o handler raiz fica
    como está.
    """
    log = Log(limite)
    raiz = logging.getLogger("iphub")
    for antigo in [alvo for alvo in raiz.handlers if isinstance(alvo, Log)]:
        raiz.removeHandler(antigo)
    raiz.addHandler(log)
    if raiz.level == logging.NOTSET or raiz.level > logging.DEBUG:
        raiz.setLevel(logging.DEBUG)
    return log
