# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The vault: secrets sealed at rest with a key the card alone does not hold."""

from dataclasses import replace
from pathlib import Path

import pytest

from iphub import cofre as modulo
from iphub import config, segredos
from iphub.arquivos import modo_de
from iphub.cofre import ARQUIVO_CHAVE, ARQUIVO_SAL, Cofre, cifrado, serie_da_placa
from iphub.config import Cadastro, Config, Licenca

SERIE = "a1b2c3d4e5f60718"


def test_cifra_e_decifra_e_o_vazio_fica_vazio(tmp_path: Path):
    cofre = Cofre(tmp_path, serie=SERIE)
    selado = cofre.cifrar("segredo-do-aparelho")
    assert cifrado(selado) and "segredo-do-aparelho" not in selado
    assert cofre.decifrar(selado) == "segredo-do-aparelho"
    assert cofre.cifrar("") == ""
    assert cofre.decifrar("") == ""
    # Two seals of the same value never read the same, so the file does not say which
    # two equipment share a password.
    assert cofre.cifrar("igual") != cofre.cifrar("igual")


def test_um_valor_em_claro_passa_como_esta(tmp_path: Path):
    """A file written by an older daemon carries the secret in clear, and it keeps working."""
    cofre = Cofre(tmp_path, serie=SERIE)
    assert cofre.decifrar("escrito-em-claro") == "escrito-em-claro"
    assert cofre.ilegiveis == 0


def test_com_serie_a_chave_e_da_placa_e_o_cartao_sozinho_nao_abre(tmp_path: Path, caplog):
    """The salt is on the card and the serial is not: a copy of the card in another
    machine, with another serial or none, opens nothing."""
    aqui = Cofre(tmp_path, serie=SERIE)
    assert aqui.presa_ao_hardware is True
    assert (tmp_path / ARQUIVO_SAL).is_file() and not (tmp_path / ARQUIVO_CHAVE).is_file()
    assert modo_de(tmp_path / ARQUIVO_SAL) == 0o600
    selado = aqui.cifrar("chave-da-licenca")
    outra_placa = Cofre(tmp_path, serie="ffffffffffffffff")
    assert outra_placa.decifrar(selado) == ""
    assert outra_placa.ilegiveis == 1
    assert "another key" in caplog.text
    mesma_placa = Cofre(tmp_path, serie=SERIE)
    assert mesma_placa.decifrar(selado) == "chave-da-licenca"


def test_sem_serie_a_chave_e_um_arquivo_ao_lado(tmp_path: Path):
    cofre = Cofre(tmp_path, serie="")
    assert cofre.presa_ao_hardware is False
    assert modo_de(tmp_path / ARQUIVO_CHAVE) == 0o600
    selado = cofre.cifrar("x")
    assert Cofre(tmp_path, serie="").decifrar(selado) == "x"
    # A serial that appears later does not change a key the secrets were written with.
    assert Cofre(tmp_path, serie=SERIE).decifrar(selado) == "x"


def test_um_arquivo_de_chave_estragado_e_trocado_e_o_antigo_nao_abre(tmp_path: Path):
    cofre = Cofre(tmp_path, serie="")
    selado = cofre.cifrar("x")
    (tmp_path / ARQUIVO_CHAVE).write_text("nao e hex\n", encoding="utf-8")
    novo = Cofre(tmp_path, serie="")
    assert novo.decifrar(selado) == ""


def test_a_serie_vem_do_cpuinfo_ou_do_device_tree(tmp_path: Path):
    cpuinfo = tmp_path / "cpuinfo"
    dt = tmp_path / "serial-number"
    assert serie_da_placa((str(cpuinfo), str(dt))) == ""
    cpuinfo.write_text("processor\t: 0\nSerial\t\t: 0000000000000000\n", encoding="utf-8")
    assert serie_da_placa((str(cpuinfo), str(dt))) == ""
    cpuinfo.write_text("processor\t: 0\nSerial\t\t: a453b5e5d65e9713\n", encoding="utf-8")
    assert serie_da_placa((str(cpuinfo), str(dt))) == "a453b5e5d65e9713"
    cpuinfo.write_text("processor\t: 0\n", encoding="utf-8")
    dt.write_bytes(b"894566f52c838e2d\x00")
    assert serie_da_placa((str(cpuinfo), str(dt))) == "894566f52c838e2d"


def test_o_config_sela_os_quatro_segredos_e_le_de_volta(tmp_path: Path):
    cfg = Config(
        otp_segredo="JBSWY3DPEHPK3PXP",
        senha_hash="c3d4",
        licencas=(Licenca(id="av1", produto="av", uuid="u", pid="p", chave="chave-tuya"),),
        equipamentos=(Cadastro(identidade="a", tipo="t", segredos={"senha": "senha-do-aparelho"}),),
    )
    config.salvar(cfg, tmp_path)
    texto = (tmp_path / config.ARQUIVO).read_text(encoding="utf-8")
    for segredo in ("JBSWY3DPEHPK3PXP", "chave-tuya", "senha-do-aparelho"):
        assert segredo not in texto
    # The hash of the password is what hides the password already, and it stays readable
    # so a hub whose board was swapped still lets its owner in.
    assert '"senha_hash": "c3d4"' in texto
    assert config.carregar(tmp_path) == cfg


def test_o_token_do_barramento_e_selado_e_um_em_claro_e_selado_no_primeiro_boot(tmp_path: Path):
    (tmp_path / segredos.ARQUIVO_TOKEN).write_text("token-em-claro\n", encoding="utf-8")
    assert segredos.abrir(tmp_path).api_token == "token-em-claro"
    escrito = (tmp_path / segredos.ARQUIVO_TOKEN).read_text(encoding="utf-8").strip()
    assert cifrado(escrito) and "token-em-claro" not in escrito
    assert segredos.abrir(tmp_path).api_token == "token-em-claro"
    novo = segredos.rotacionar_api_token(tmp_path)
    assert novo not in (tmp_path / segredos.ARQUIVO_TOKEN).read_text(encoding="utf-8")
    assert segredos.abrir(tmp_path).api_token == novo


def test_um_token_de_outra_placa_e_trocado_por_um_novo(tmp_path: Path):
    """A token nothing can read is a token nothing authenticates with; the bridge pairs again."""
    outra = Cofre(tmp_path / "outra", serie="0000000000000001")
    selado = outra.cifrar("token-de-la")
    (tmp_path / segredos.ARQUIVO_TOKEN).write_text(selado + "\n", encoding="utf-8")
    modulo.esquecer(tmp_path)
    token = segredos.abrir(tmp_path).api_token
    assert token and token != "token-de-la"
    assert segredos.abrir(tmp_path).api_token == token


def test_abrir_devolve_o_mesmo_cofre_para_o_mesmo_diretorio(tmp_path: Path):
    modulo.esquecer(tmp_path)
    assert modulo.abrir(tmp_path) is modulo.abrir(tmp_path)
    assert modulo.abrir(tmp_path).identidade == modulo.abrir(tmp_path).identidade
    assert len(modulo.abrir(tmp_path).identidade) == 4


@pytest.mark.parametrize("valor", [None, 3, b"x", ["cofre:"]])
def test_o_que_nao_e_texto_le_vazio(tmp_path: Path, valor):
    assert Cofre(tmp_path, serie=SERIE).decifrar(valor) == ""


def test_replace_nao_reabre_nada(tmp_path: Path):
    cfg = Config(licencas=(Licenca(id="av1", produto="av", chave="c"),))
    assert replace(cfg, nome_instalacao="x").licencas[0].chave == "c"
