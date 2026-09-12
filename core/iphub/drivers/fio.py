# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The transcript of the wire, what a driver sent and what the device answered.

When a customer says "the receiver does not work", the only thing that settles it is the
bytes. A log that says "poll 3 failed with eq_offline" says the driver gave up; a log that
says "-> GET /YamahaExtendedControl/v1/main/getStatus" and "<- 200 {response_code:0,...}" says
what the device did, and the next version is written from that line instead of from a guess.
So every driver writes its exchanges through here, in one shape, so a transcript of a Yamaha
reads like a transcript of an LG.

Two rules keep the transcript usable. A secret never lands in it: the token of a cloud, the
client key of a television and the password of a projector are redacted before the line is
written, whatever shape the payload had. And the routine poll is written ONCE, on the first
exchange after a connection and again after every failure: the ring of the panel holds a
thousand lines, and thirteen devices polled every ten seconds would evict the commands that
are the reason anybody opens it, while the one transcript of the poll is exactly what shows
the shape of what the device answers.
"""

import json
import logging
import re

# A line of the panel is read on a phone, and a body of sixty kilobytes is a wall; what a
# diagnosis needs is the head of the answer, where the status and the first fields live.
TEXTO_MAXIMO = 400

# The names under which a secret travels, whatever the protocol calls its container.
CHAVES_SECRETAS = frozenset(
    {
        "client-key",
        "clientkey",
        "client_key",
        "token",
        "access_token",
        "accesstoken",
        "authorization",
        "x-auth-psk",
        "psk",
        "senha",
        "password",
        "pass",
        "chave",
        "key",
    }
)
REDIGIDO = "***"
_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_ESPACO = re.compile(r"\s+")


class Fio:
    """The transcript of one equipment, quiet on the routine poll and loud on everything else."""

    def __init__(self, log: logging.Logger, quem: str) -> None:
        self._log = log
        self._quem = quem
        self._rotina_escrita = False

    def enviado(self, texto: object, *, rotina: bool = False) -> None:
        if rotina and self._rotina_escrita:
            return
        self._log.debug("%s -> %s", self._quem, aparar(texto))

    def recebido(self, texto: object, *, rotina: bool = False) -> None:
        if rotina and self._rotina_escrita:
            return
        self._log.debug("%s <- %s", self._quem, aparar(texto))
        if rotina:
            self._rotina_escrita = True

    def falhou(self, o_que: str, erro: object) -> None:
        """What was being asked and why it did not come back, and the next poll is written."""
        self._rotina_escrita = False
        self._log.warning("%s: %s failed: %s", self._quem, aparar(o_que), _razao(erro))

    def recusado(self, o_que: str, codigo: str) -> None:
        """The device answered, and the answer was no: written like an answer, not a failure.

        A television without one optional endpoint refuses it on every poll, and a
        warning per poll is what buries the one line that matters; the refusal is an answer of
        the device and is written at the level of answers. A socket that died is a failure.
        """
        self._log.debug("%s <- refused %s: %s", self._quem, aparar(o_que), codigo)

    def reconectou(self) -> None:
        """A new connection is a new transcript: the next poll is written again."""
        self._rotina_escrita = False


def aparar(texto: object) -> str:
    """One line, secrets redacted, control characters gone, inside the ceiling."""
    limpo = redigir(texto)
    limpo = _ESPACO.sub(" ", _CONTROLE.sub("", limpo)).strip()
    if len(limpo) <= TEXTO_MAXIMO:
        return limpo
    return f"{limpo[:TEXTO_MAXIMO]}... ({len(limpo)} chars)"


def redigir(texto: object) -> str:
    """The text with every secret replaced, whether it came as a mapping, as JSON or as raw
    text with a secret name in it.
    """
    if isinstance(texto, bytes | bytearray):
        texto = bytes(texto).decode("utf-8", errors="replace")
    if isinstance(texto, dict | list | tuple):
        return json.dumps(_redigido(texto), ensure_ascii=False, separators=(",", ":"))
    bruto = str(texto)
    if bruto.lstrip()[:1] in "{[":
        try:
            return json.dumps(
                _redigido(json.loads(bruto)), ensure_ascii=False, separators=(",", ":")
            )
        except (ValueError, RecursionError):
            pass
    return _redigir_texto(bruto)


def _redigido(valor: object) -> object:
    if isinstance(valor, dict):
        return {
            chave: (REDIGIDO if str(chave).lower() in CHAVES_SECRETAS else _redigido(item))
            for chave, item in valor.items()
        }
    if isinstance(valor, list | tuple):
        return [_redigido(item) for item in valor]
    return valor


_NOMES_SECRETOS = "|".join(sorted(re.escape(chave) for chave in CHAVES_SECRETAS))
_PAR_SECRETO = re.compile(r"(?i)\b(" + _NOMES_SECRETOS + r")(\s*[=:]\s*)([^&;,\n\"']+)")


def _redigir_texto(texto: str) -> str:
    return _PAR_SECRETO.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDIGIDO}", texto)


def _razao(erro: object) -> str:
    if isinstance(erro, BaseException):
        return f"{type(erro).__name__}: {erro}" if str(erro) else type(erro).__name__
    return str(erro)
