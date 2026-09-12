# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Schedules: data with a clock, fired once in their minute, in the zone of the house."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from iphub.agenda import (
    Agendamento,
    AgendamentosInvalidos,
    Despertador,
    devidos,
    fuso_valido,
    validar,
    zona,
)

SAO_PAULO = "America/Sao_Paulo"


def test_valida_e_ordena_os_dias():
    (lido,) = validar([{"cena": 3, "hora": "07:30", "dias": [4, 0, 2]}])
    assert lido == Agendamento(cena=3, hora="07:30", dias=(0, 2, 4), ativo=True)
    assert validar([]) == ()


@pytest.mark.parametrize(
    ("item", "campo", "codigo"),
    [
        (
            {"cena": 0, "hora": "07:30", "dias": [0]},
            "agendamentos[0].cena",
            "agendamento_cena_invalida",
        ),
        (
            {"cena": 33, "hora": "07:30", "dias": [0]},
            "agendamentos[0].cena",
            "agendamento_cena_invalida",
        ),
        (
            {"cena": "1", "hora": "07:30", "dias": [0]},
            "agendamentos[0].cena",
            "agendamento_cena_invalida",
        ),
        (
            {"cena": 1, "hora": "7:30", "dias": [0]},
            "agendamentos[0].hora",
            "agendamento_hora_invalida",
        ),
        (
            {"cena": 1, "hora": "24:00", "dias": [0]},
            "agendamentos[0].hora",
            "agendamento_hora_invalida",
        ),
        (
            {"cena": 1, "hora": "07:30", "dias": []},
            "agendamentos[0].dias",
            "agendamento_dias_invalidos",
        ),
        (
            {"cena": 1, "hora": "07:30", "dias": [7]},
            "agendamentos[0].dias",
            "agendamento_dias_invalidos",
        ),
        (
            {"cena": 1, "hora": "07:30", "dias": [1, 1]},
            "agendamentos[0].dias",
            "agendamento_dias_invalidos",
        ),
        (
            {"cena": 1, "hora": "07:30", "dias": [0], "ativo": 1},
            "agendamentos[0].ativo",
            "agendamento_ativo_invalido",
        ),
        (
            {"cena": 1, "hora": "07:30", "dias": [0], "x": 1},
            "agendamentos[0]",
            "agendamento_chave_desconhecida",
        ),
        ("nao", "agendamentos[0]", "agendamento_nao_objeto"),
    ],
)
def test_cada_problema_e_nomeado_pelo_campo(item, campo, codigo):
    with pytest.raises(AgendamentosInvalidos) as recusa:
        validar([item])
    assert (campo, codigo) in recusa.value.problemas


def test_uma_lista_que_nao_e_lista_e_uma_lista_demais():
    with pytest.raises(AgendamentosInvalidos) as recusa:
        validar({"cena": 1})
    assert recusa.value.problemas == (("agendamentos", "agendamentos_nao_lista"),)
    with pytest.raises(AgendamentosInvalidos) as recusa:
        validar([{"cena": 1, "hora": "07:30", "dias": [0]}] * 33)
    assert recusa.value.problemas == (("agendamentos", "agendamentos_demais"),)


def test_o_fuso_e_um_nome_que_a_caixa_conhece():
    assert fuso_valido("") is True
    assert fuso_valido(SAO_PAULO) is True
    assert fuso_valido("Europe/Lisbon") is True
    assert fuso_valido("Marte/Olympus") is False
    assert fuso_valido("../../etc/passwd") is False
    assert fuso_valido(3) is False
    assert str(zona(SAO_PAULO)) == SAO_PAULO
    assert zona("") is not None


def test_devidos_e_a_hora_e_o_dia_da_semana():
    agendamentos = (
        Agendamento(1, "07:30", (0, 1, 2, 3, 4)),
        Agendamento(2, "07:30", (5, 6)),
        Agendamento(3, "07:30", (0,), ativo=False),
        Agendamento(4, "23:00", (0, 1, 2, 3, 4, 5, 6)),
    )
    segunda = datetime(2026, 9, 14, 7, 30, tzinfo=ZoneInfo(SAO_PAULO))
    assert devidos(agendamentos, segunda) == [0]
    sabado = datetime(2026, 9, 12, 7, 30, tzinfo=ZoneInfo(SAO_PAULO))
    assert devidos(agendamentos, sabado) == [1]
    assert devidos(agendamentos, segunda.replace(hour=23, minute=0)) == [3]
    assert devidos(agendamentos, segunda.replace(minute=31)) == []


class _Relogio:
    def __init__(self, agora: float) -> None:
        self.agora = agora

    def __call__(self) -> float:
        return self.agora


def test_o_despertador_dispara_uma_vez_por_minuto_na_zona_da_casa():
    # 2026-09-14 07:30 in Sao Paulo is 10:30 UTC.
    momento = datetime(2026, 9, 14, 7, 30, tzinfo=ZoneInfo(SAO_PAULO)).timestamp()
    relogio = _Relogio(momento)
    rodadas: list[int] = []
    agendamentos = [Agendamento(5, "07:30", (0,))]
    despertador = Despertador(
        lambda: agendamentos, lambda: SAO_PAULO, lambda n: rodadas.append(n), agora=relogio
    )
    assert despertador.tique() == [5]
    relogio.agora += 20
    assert despertador.tique() == []
    relogio.agora += 20
    assert despertador.tique() == []
    assert rodadas == [5]
    # In UTC the same instant is half past ten, and nothing is due.
    em_utc = Despertador(
        lambda: agendamentos, lambda: "UTC", lambda n: rodadas.append(n), agora=relogio
    )
    assert em_utc.tique() == []
    # The next week, same minute, fires again; a scene that refuses is not counted.
    relogio.agora = momento + 7 * 86400
    assert despertador.tique() == [5]
    relogio.agora = momento + 14 * 86400
    recusa = Despertador(
        lambda: agendamentos, lambda: SAO_PAULO, lambda n: "cena_em_curso", agora=relogio
    )
    assert recusa.tique() == []


def test_o_despertador_le_a_lista_viva():
    momento = datetime(2026, 9, 14, 7, 30, tzinfo=ZoneInfo("UTC")).timestamp()
    lista: list[Agendamento] = []
    rodadas: list[int] = []
    despertador = Despertador(
        lambda: lista, lambda: "UTC", lambda n: rodadas.append(n), agora=_Relogio(momento)
    )
    assert despertador.tique() == []
    lista.append(Agendamento(2, "07:30", (0,)))
    assert despertador.tique() == [2]
