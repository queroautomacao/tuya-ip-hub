# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The diary of the hub: the last lines of what the daemon did, kept in memory for the panel
to show and for a report to carry.

What a driver put on the wire, what the bridge of the platform asked for and what the
panel changed are three stories that only make sense read together, in order, on the same
screen. A container log tells them, but the integrator standing in a house has a browser and
not a shell, and asking for `docker logs` is asking for the one thing a tablet cannot do.

The ceiling is the whole point: a hub runs for months, so the diary keeps the LAST lines and
forgets the rest, counting what it dropped so nobody reads a hole as silence. Nothing here
touches disk: a diary that survived a reboot would be a database, and this is a window.
"""

import logging
from collections import deque
from dataclasses import dataclass

# A thousand lines is a few minutes of a busy hub and about 200 kB of memory, which is
# what a diagnosis needs and what a placa with 512 MB never notices; the panel reads them all
# at once, so the ceiling is also what keeps one GET small.
LINHAS_MAXIMO = 1000

# A device that answers a megabyte of garbage would put that megabyte in every line of
# the diary; the message is cut where a human stops reading anyway.
MENSAGEM_MAXIMA = 600

# The origins the panel groups the lines by, decided by the logger that wrote each one.
ORIGEM_DRIVER = "driver"
ORIGEM_TUYA = "tuya"
ORIGEM_PAINEL = "panel"
ORIGEM_HUB = "hub"
ORIGENS = (ORIGEM_DRIVER, ORIGEM_TUYA, ORIGEM_PAINEL, ORIGEM_HUB)

# Which prefix of a logger name belongs to which origin, longest prefix first.
_ORIGEM_POR_PREFIXO = (
    ("iphub.drivers", ORIGEM_DRIVER),
    ("iphub.dpbus", ORIGEM_TUYA),
    ("iphub.api", ORIGEM_PAINEL),
    ("iphub.cenas", ORIGEM_TUYA),
)


@dataclass(frozen=True)
class Linha:
    """One line of the diary, as the panel reads it."""

    # The clock of the record and not of the reading, because a line is read minutes
    # after it happened and the order between two lines is the whole value of a log.
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
    """The origin of a line, from the name of the logger that wrote it."""
    for prefixo, origem in _ORIGEM_POR_PREFIXO:
        if nome == prefixo or nome.startswith(f"{prefixo}."):
            return origem
    return ORIGEM_HUB


# A log is read by whoever is debugging an installation, and the modules of this daemon
# are named in Portuguese because the code is. "gestor" says nothing to a reader of the log,
# and a driver of a maker is read by people who do not speak it. So the few module names that
# reach a log line are written in English here, and everything else keeps the name it has,
# which is already English or is the name of a maker. The map is small on purpose: a module
# that never logs never needs a line here.
_ONDE_EM_INGLES = {
    "gestor": "manager",
    "equipamentos": "equipment",
    "cenas": "scenes",
    "licencas": "licences",
    "sistema": "system",
    "numeros": "numbers",
    "descoberta": "discovery",
    "varredura": "sweep",
    "catalogo": "catalog",
    "motor": "engine",
    "transporte": "transport",
    "portao": "gate",
    "sessoes": "sessions",
    "comum": "common",
    "regex_seguro": "regex",
}


def onde_de(nome: str) -> str:
    """The short name of the module that wrote the line, in English, which is what a reader
    recognises.
    """
    curto = nome.removeprefix("iphub.").split(".")[-1] or nome
    return _ONDE_EM_INGLES.get(curto, curto)


class Log(logging.Handler):
    """A ring of the last lines, filled by the logging of the whole daemon."""

    def __init__(self, limite: int = LINHAS_MAXIMO) -> None:
        super().__init__(level=logging.DEBUG)
        self._linhas: deque[Linha] = deque(maxlen=max(1, limite))
        self.descartadas = 0

    def emit(self, record: logging.LogRecord) -> None:
        # A handler that raises takes the call that was logging with it, and a defect in
        # formatting a message must never be able to break a poll or a command.
        try:
            mensagem = record.getMessage()
        except Exception:
            mensagem = f"<unformattable {record.msg!r}>"
        if record.exc_info:
            # The traceback of an unexpected failure is the one thing worth more than the
            # message, but only its last line fits a diary; the container log keeps the rest.
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
    # A control character in a message written by a device would break the line of the
    # panel and, in a copied report, the file it lands in.
    limpo = "".join(caractere if caractere.isprintable() else " " for caractere in mensagem)
    if len(limpo) <= MENSAGEM_MAXIMA:
        return limpo
    return f"{limpo[:MENSAGEM_MAXIMA]}..."


def instalar(limite: int = LINHAS_MAXIMO) -> Log:
    """Puts a diary under the logging of the daemon and answers it.

    The level of the diary is its own, so the container log stays at INFO while the panel
    still sees every command a driver wrote; the propagation to the root handler is untouched.
    """
    log = Log(limite)
    raiz = logging.getLogger("iphub")
    for antigo in [alvo for alvo in raiz.handlers if isinstance(alvo, Log)]:
        raiz.removeHandler(antigo)
    raiz.addHandler(log)
    if raiz.level == logging.NOTSET or raiz.level > logging.DEBUG:
        raiz.setLevel(logging.DEBUG)
    return log
