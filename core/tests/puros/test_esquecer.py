# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: forgetting the password from the data directory, and what it must not take.

Seção 9: esquecer a senha pelo diretório de dados, e o que isso não pode levar junto.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from iphub import esquecer as modulo
from iphub import segredos
from iphub.config import Cadastro, Config, carregar, salvar
from iphub.sessoes import Sessoes

SENHA_SALT = "sal"
SENHA_HASH = "hash"


@pytest.fixture
def dir_data(tmp_path: Path) -> Path:
    caminho = tmp_path / "data"
    caminho.mkdir()
    segredos.abrir(caminho)
    return caminho


def _com_dono(dir_data: Path) -> Config:
    cfg = Config(
        nome_instalacao="Casa",
        senha_salt=SENHA_SALT,
        senha_hash=SENHA_HASH,
        senha_iteracoes=200_000,
        equipamentos=(Cadastro(identidade="uuid-1", tipo="multiroom_linkplay", ip="192.0.2.10"),),
        zonas=("uuid-1",),
    )
    salvar(cfg, dir_data)
    return cfg


def test_esquecer_devolve_o_hub_ao_primeiro_acesso(dir_data: Path):
    _com_dono(dir_data)
    assert carregar(dir_data).configurado is True
    assert modulo.esquecer(dir_data) is True
    assert carregar(dir_data).configurado is False


def test_esquecer_mantem_a_instalacao_inteira(dir_data: Path):
    # Why: erasing config.json would clear the password and take the equipment, the zones and
    # the scenes with it, which is a reinstallation and not a way back in.
    # Por que: apagar o config.json zeraria a senha e levaria junto os equipamentos, as zonas e
    # as cenas, o que é uma reinstalação e não um caminho de volta.
    antes = _com_dono(dir_data)
    modulo.esquecer(dir_data)
    depois = carregar(dir_data)
    assert depois == replace(antes, senha_salt="", senha_hash="", senha_iteracoes=0)


def test_esquecer_mata_as_sessoes_e_troca_a_credencial_de_maquina(dir_data: Path):
    """Section 9: whoever could not get in must not keep a credential issued before.

    Seção 9: quem não conseguia entrar não pode ficar com uma credencial emitida antes.
    """
    _com_dono(dir_data)
    sessoes = Sessoes(dir_data)
    token, _validade = sessoes.criar()
    assert sessoes.validar(token) is True
    antigo = segredos.abrir(dir_data).api_token

    modulo.esquecer(dir_data)

    assert Sessoes(dir_data).validar(token) is False
    assert segredos.abrir(dir_data).api_token != antigo


def test_esquecer_num_hub_sem_dono_nao_faz_nada(dir_data: Path):
    salvar(Config(nome_instalacao="Casa"), dir_data)
    antes = segredos.abrir(dir_data).api_token
    assert modulo.esquecer(dir_data) is False
    assert segredos.abrir(dir_data).api_token == antes


def test_o_comando_diz_o_que_fez_e_sai_com_zero(dir_data: Path, monkeypatch, capsys):
    _com_dono(dir_data)
    monkeypatch.setenv("IPHUB_DATA", str(dir_data))
    assert modulo.main() == 0
    assert "password cleared" in capsys.readouterr().out
    assert carregar(dir_data).configurado is False
