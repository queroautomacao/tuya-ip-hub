# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from iphub.ambiente import Ambiente


def test_padroes_sem_variaveis():
    amb = Ambiente.do_ambiente({})
    assert amb.bind == "0.0.0.0"
    assert amb.porta == 8080
    assert amb.dir_data == Path("/data")
    assert amb.dir_painel == Path("/app/painel")


def test_variaveis_sobrepoem_padroes():
    amb = Ambiente.do_ambiente(
        {
            "IPHUB_BIND": "127.0.0.1",
            "IPHUB_PORTA": "9090",
            "IPHUB_DATA": "/srv/iphub",
            "IPHUB_PAINEL": "/srv/painel",
        }
    )
    assert amb == Ambiente(
        bind="127.0.0.1", porta=9090, dir_data=Path("/srv/iphub"), dir_painel=Path("/srv/painel")
    )


def test_le_os_environ_por_padrao(monkeypatch):
    monkeypatch.setenv("IPHUB_PORTA", "8181")
    assert Ambiente.do_ambiente().porta == 8181


def test_valor_vazio_conta_como_ausente():
    amb = Ambiente.do_ambiente(
        {"IPHUB_BIND": "", "IPHUB_PORTA": "", "IPHUB_DATA": "", "IPHUB_PAINEL": ""}
    )
    assert amb.bind == "0.0.0.0"
    assert amb.porta == 8080
    assert amb.dir_data == Path("/data")
    assert amb.dir_painel == Path("/app/painel")


@pytest.mark.parametrize(
    "porta",
    [
        "abc",
        "0",
        "-1",
        "65536",
        "80.5",
        "8080a",
        "1_000",
        "+80",
        "\uff10\uff18\uff10\uff18\uff10",
        "0x50",
    ],
)
def test_porta_invalida_levanta_value_error(porta):
    with pytest.raises(ValueError, match="IPHUB_PORTA"):
        Ambiente.do_ambiente({"IPHUB_PORTA": porta})


def test_ambiente_e_imutavel():
    amb = Ambiente.do_ambiente({})
    with pytest.raises(FrozenInstanceError):
        amb.porta = 1  # type: ignore[misc]


def test_espaco_em_volta_da_porta_e_tolerado():
    assert Ambiente.do_ambiente({"IPHUB_PORTA": " 8080 "}).porta == 8080
