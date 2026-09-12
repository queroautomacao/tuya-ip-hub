# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The window of the remote access: it closes by itself, and nobody can buy more than a day."""

import json

from iphub.arquivos import modo_de
from iphub.remoto import ARQUIVO, JANELA_S, Janela
from iphub.versao import SCHEMA_VERSION


class _Relogio:
    def __init__(self, agora: float = 1_700_000_000.0) -> None:
        self.agora = agora

    def __call__(self) -> float:
        return self.agora


def test_uma_janela_nova_nasce_fechada(tmp_path):
    janela = Janela(tmp_path, _Relogio())
    assert janela.ativa() is False
    assert janela.restante_s() == 0


def test_armar_abre_por_vinte_e_quatro_horas_e_o_tempo_anda(tmp_path):
    relogio = _Relogio()
    janela = Janela(tmp_path, relogio)
    assert janela.armar() == JANELA_S
    assert janela.ativa() is True
    relogio.agora += JANELA_S - 60
    assert janela.restante_s() == 60
    relogio.agora += 61
    assert janela.ativa() is False
    assert janela.restante_s() == 0


def test_desarmar_fecha_na_hora(tmp_path):
    janela = Janela(tmp_path, _Relogio())
    janela.armar()
    janela.desarmar()
    assert janela.ativa() is False


def test_a_janela_sobrevive_a_um_restart_e_continua_contando(tmp_path):
    """A hub that rebooted at three in the morning must not strand the integrator, and the
    deadline is absolute, so the door still closes at the same instant."""
    relogio = _Relogio()
    Janela(tmp_path, relogio).armar()
    relogio.agora += 3600
    depois = Janela(tmp_path, relogio)
    assert depois.ativa() is True
    assert depois.restante_s() == JANELA_S - 3600


def test_uma_janela_vencida_no_disco_nasce_fechada(tmp_path):
    relogio = _Relogio()
    Janela(tmp_path, relogio).armar()
    relogio.agora += JANELA_S + 1
    assert Janela(tmp_path, relogio).ativa() is False


def test_um_prazo_plantado_a_mao_nao_compra_mais_que_uma_janela(tmp_path):
    """Whoever edits the file can only ever hold the door open for the day the owner grants."""
    relogio = _Relogio()
    (tmp_path / ARQUIVO).write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "fim_em": relogio.agora + 365 * 86400}),
        encoding="utf-8",
    )
    assert Janela(tmp_path, relogio).restante_s() == JANELA_S


def test_um_arquivo_estragado_e_uma_porta_fechada(tmp_path):
    for conteudo in ("", "{", "[]", json.dumps({"fim_em": 1}), json.dumps({"schema_version": 1})):
        (tmp_path / ARQUIVO).write_text(conteudo, encoding="utf-8")
        assert Janela(tmp_path, _Relogio()).ativa() is False


def test_o_arquivo_da_janela_nasce_seiscentos(tmp_path):
    janela = Janela(tmp_path, _Relogio())
    janela.armar()
    assert modo_de(tmp_path / ARQUIVO) == 0o600


def test_armar_sorteia_um_codigo_e_desarmar_o_apaga(tmp_path):
    janela = Janela(tmp_path, _Relogio())
    assert janela.codigo() == ""
    janela.armar()
    codigo = janela.codigo()
    assert len(codigo) == 9 and codigo[4] == "-"
    assert janela.confere(codigo) is True
    janela.desarmar()
    assert janela.codigo() == ""
    assert janela.confere(codigo) is False


def test_uma_janela_nova_tem_um_codigo_novo(tmp_path):
    """A code that leaked buys one day and never two."""
    janela = Janela(tmp_path, _Relogio())
    janela.armar()
    primeiro = janela.codigo()
    janela.armar()
    assert janela.codigo() != primeiro
    assert janela.confere(primeiro) is False


def test_o_codigo_sobrevive_ao_restart_e_morre_com_a_janela(tmp_path):
    relogio = _Relogio()
    Janela(tmp_path, relogio).armar()
    depois = Janela(tmp_path, relogio)
    codigo = depois.codigo()
    assert codigo != ""
    assert Janela(tmp_path, relogio).confere(codigo) is True
    relogio.agora += JANELA_S + 1
    vencida = Janela(tmp_path, relogio)
    assert vencida.codigo() == ""
    assert vencida.confere(codigo) is False


def test_o_codigo_e_conferido_sem_ligar_para_traco_espaco_e_caixa(tmp_path):
    """A person types what the phone showed, and the phone showed a dash in the middle."""
    janela = Janela(tmp_path, _Relogio())
    janela.armar()
    codigo = janela.codigo()
    for escrito in (
        codigo.lower(),
        codigo.replace("-", ""),
        f" {codigo} ",
        codigo.replace("-", " "),
    ):
        assert janela.confere(escrito) is True
    assert janela.confere(None) is False
    assert janela.confere("") is False
    assert janela.confere("ABCD-EFGH") is False


def test_o_alfabeto_do_codigo_nao_tem_letra_que_se_confunde_com_numero(tmp_path):
    from iphub.remoto import ALFABETO

    assert not set("01OILU") & set(ALFABETO)
    assert len(ALFABETO) == 30
    assert ALFABETO == "".join(sorted(set(ALFABETO)))
