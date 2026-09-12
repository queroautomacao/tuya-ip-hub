# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The diary of the hub: a ring of the last lines, and nothing it does may break what logs."""

import logging

import pytest

from iphub.log import (
    MENSAGEM_MAXIMA,
    Log,
    instalar,
    onde_de,
    origem_de,
)


def _registro(
    nome: str = "iphub.drivers.nativos.linkplay", mensagem: str = "oi"
) -> logging.LogRecord:
    return logging.LogRecord(nome, logging.DEBUG, "arquivo.py", 1, mensagem, None, None)


def test_o_anel_guarda_as_ultimas_linhas_e_conta_o_que_soltou():
    """A hub runs for months, so the diary keeps the last lines and says how many it dropped;
    a hole read as silence would send the integrator looking for a device that never went
    quiet.
    """
    log = Log(limite=3)
    for numero in range(5):
        log.emit(_registro(mensagem=f"linha {numero}"))
    assert [linha.mensagem for linha in log.linhas()] == ["linha 2", "linha 3", "linha 4"]
    assert log.descartadas == 2
    log.limpar()
    assert log.linhas() == ()
    assert log.descartadas == 0


@pytest.mark.parametrize(
    ("nome", "origem", "onde"),
    [
        ("iphub.drivers.nativos.linkplay", "driver", "linkplay"),
        ("iphub.drivers.gestor", "driver", "manager"),
        ("iphub.dpbus.socket", "tuya", "socket"),
        ("iphub.dpbus.numeros", "tuya", "numbers"),
        ("iphub.cenas", "tuya", "scenes"),
        ("iphub.api.licencas", "panel", "licences"),
        ("iphub", "hub", "iphub"),
        ("iphub.painel", "hub", "painel"),
        ("iphub.api.equipamentos", "panel", "equipment"),
        ("iphub.driversfalso", "hub", "driversfalso"),
    ],
)
def test_a_origem_de_uma_linha_vem_do_modulo_que_a_escreveu(nome, origem, onde):
    """The panel groups the lines by where they came from, and the logger name is the fact;
    a prefix that only looks like another is not that other.
    """
    assert origem_de(nome) == origem
    assert onde_de(nome) == onde


def test_uma_mensagem_enorme_ou_com_controle_nao_quebra_a_linha():
    """A device on the LAN writes part of these messages, so a megabyte of garbage or a byte
    that moves the cursor would land in the panel and in whatever file the report is pasted in.
    """
    log = Log()
    log.emit(_registro(mensagem="a" * (MENSAGEM_MAXIMA * 3)))
    log.emit(_registro(mensagem="quebra\nde\x00linha"))
    primeira, segunda = log.linhas()
    assert len(primeira.mensagem) == MENSAGEM_MAXIMA + 3
    assert primeira.mensagem.endswith("...")
    assert segunda.mensagem == "quebra de linha"


def test_uma_mensagem_que_nao_formata_vira_linha_e_nunca_excecao():
    """A handler that raises takes the call that was logging with it, so a wrong format string
    costs a line that says so and nothing else.
    """
    log = Log()
    registro = logging.LogRecord("iphub", logging.INFO, "a.py", 1, "%d", ("nao e numero",), None)
    log.emit(registro)
    assert "unformattable" in log.linhas()[0].mensagem


def test_a_excecao_de_uma_falha_entra_na_linha_sem_o_traceback():
    """The last line of a traceback is what a diary can carry; the container log keeps the rest."""
    log = Log()
    try:
        raise ValueError("valor ruim")
    except ValueError:
        import sys

        registro = logging.LogRecord(
            "iphub", logging.ERROR, "a.py", 1, "falhou", None, sys.exc_info()
        )
    log.emit(registro)
    assert log.linhas()[0].mensagem == "falhou [ValueError: valor ruim]"


def test_instalar_ve_o_debug_do_daemon_e_nao_instala_dois():
    """The diary has a level of its own, so the panel sees every command a driver wrote while
    the container log stays where it was; installing twice would double every line.
    """
    raiz = logging.getLogger("iphub")
    nivel = raiz.level
    handlers = list(raiz.handlers)
    try:
        primeiro = instalar()
        segundo = instalar()
        assert [alvo for alvo in raiz.handlers if isinstance(alvo, Log)] == [segundo]
        logging.getLogger("iphub.drivers.nativos.linkplay").debug("setPlayerCmd:vol:30")
        assert primeiro.linhas() == ()
        linha = segundo.linhas()[-1]
        assert (linha.origem, linha.nivel, linha.mensagem) == (
            "driver",
            "debug",
            "setPlayerCmd:vol:30",
        )
    finally:
        raiz.handlers = handlers
        raiz.setLevel(nivel)


def test_o_json_de_uma_linha_e_o_que_o_painel_le():
    log = Log()
    log.emit(_registro(nome="iphub.dpbus.socket", mensagem="set dp 121 = 30"))
    como_json = log.linhas()[0].como_json()
    assert set(como_json) == {"t", "nivel", "origem", "onde", "texto"}
    assert (como_json["origem"], como_json["onde"]) == ("tuya", "socket")
    assert como_json["texto"] == "set dp 121 = 30"
    assert isinstance(como_json["t"], float)
