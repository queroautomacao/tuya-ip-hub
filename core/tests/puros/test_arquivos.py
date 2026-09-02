# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: a file under the data directory is born 0600 and is written atomically.

Seção 9: um arquivo do diretório de dados nasce 0600 e é escrito de forma atômica.
"""

import errno
import json
import os
import stat
from pathlib import Path

import pytest

from iphub import arquivos

SEGREDO = "ABCD-EFGH-JKLM-NPQR\n"


@pytest.fixture
def umask_aberto():
    """The widest umask a container can inherit; nothing may reach the group or the world.

    O umask mais aberto que um container pode herdar; nada pode chegar ao grupo ou ao mundo.
    """
    anterior = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(anterior)


def test_escreve_e_le_texto(tmp_path: Path):
    caminho = tmp_path / "segredo.txt"
    arquivos.escrever_texto(caminho, "instalação\n")
    assert arquivos.ler_texto(caminho) == "instalação\n"


def test_arquivo_nasce_0600(tmp_path: Path):
    caminho = tmp_path / "segredo.txt"
    arquivos.escrever_texto(caminho, SEGREDO)
    assert arquivos.modo_de(caminho) == 0o600


def test_umask_aberto_nao_alarga_o_modo(tmp_path: Path, umask_aberto):
    caminho = tmp_path / "segredo.txt"
    arquivos.escrever_texto(caminho, SEGREDO)
    assert arquivos.modo_de(caminho) == 0o600


def test_temporario_tambem_nasce_0600(tmp_path: Path, umask_aberto, monkeypatch):
    # Why: between the open and the replace the secret already exists on disk; a temporary
    # file open to the group would leak it in that window.
    # Por que: entre o open e o replace o segredo já existe em disco; um temporário aberto ao
    # grupo o vazaria nessa janela.
    vistos = []
    real = os.replace

    def espiar(origem, destino):
        vistos.append(arquivos.modo_de(Path(origem)))
        return real(origem, destino)

    monkeypatch.setattr(os, "replace", espiar)
    arquivos.escrever_texto(tmp_path / "segredo.txt", SEGREDO)
    assert vistos == [0o600]


def test_escrita_nao_alarga_modo_existente(tmp_path: Path):
    caminho = tmp_path / "segredo.txt"
    arquivos.escrever_texto(caminho, SEGREDO)
    caminho.chmod(0o400)
    arquivos.escrever_texto(caminho, "outro\n")
    assert arquivos.modo_de(caminho) == 0o400


def test_escrita_fecha_modo_folgado(tmp_path: Path):
    caminho = tmp_path / "segredo.txt"
    arquivos.escrever_texto(caminho, SEGREDO)
    caminho.chmod(0o666)
    arquivos.escrever_texto(caminho, "outro\n")
    assert arquivos.modo_de(caminho) == 0o600


def test_escrita_substitui_o_conteudo_inteiro(tmp_path: Path):
    caminho = tmp_path / "segredo.txt"
    arquivos.escrever_texto(caminho, "x" * 500)
    arquivos.escrever_texto(caminho, "ok\n")
    assert caminho.read_text(encoding="utf-8") == "ok\n"


def test_nao_deixa_temporario_para_tras(tmp_path: Path):
    caminho = tmp_path / "segredo.txt"
    arquivos.escrever_texto(caminho, SEGREDO)
    arquivos.escrever_texto(caminho, SEGREDO)
    assert [c.name for c in tmp_path.iterdir()] == ["segredo.txt"]


def test_falha_na_troca_preserva_o_arquivo_e_apaga_o_temporario(tmp_path: Path, monkeypatch):
    caminho = tmp_path / "segredo.txt"
    arquivos.escrever_texto(caminho, SEGREDO)

    def falhar(origem, destino):
        raise OSError("disco cheio")

    monkeypatch.setattr(os, "replace", falhar)
    with pytest.raises(OSError):
        arquivos.escrever_texto(caminho, "conteudo novo\n")
    assert caminho.read_text(encoding="utf-8") == SEGREDO
    assert [c.name for c in tmp_path.iterdir()] == ["segredo.txt"]


def test_json_ida_e_volta(tmp_path: Path):
    caminho = tmp_path / "config.json"
    dados = {"nome": "Instalação", "lista": ["a", "b"], "numero": 200000, "ligado": True}
    arquivos.escrever_json(caminho, dados)
    assert arquivos.ler_json(caminho) == dados


def test_json_nasce_0600(tmp_path: Path, umask_aberto):
    caminho = tmp_path / "config.json"
    arquivos.escrever_json(caminho, {"a": 1})
    assert arquivos.modo_de(caminho) == 0o600


def test_json_e_legivel_por_gente(tmp_path: Path):
    caminho = tmp_path / "config.json"
    arquivos.escrever_json(caminho, {"b": 2, "a": "ção"})
    texto = caminho.read_text(encoding="utf-8")
    assert texto.endswith("\n")
    assert "ção" in texto
    assert texto.index('"a"') < texto.index('"b"')


def test_ler_ausente_devolve_none(tmp_path: Path):
    assert arquivos.ler_texto(tmp_path / "nao-existe.txt") is None
    assert arquivos.ler_json(tmp_path / "nao-existe.json") is None


@pytest.mark.parametrize("conteudo", ["[1, 2]", '"texto"', "3", "null", "true"])
def test_json_que_nao_e_objeto_e_recusado(tmp_path: Path, conteudo):
    caminho = tmp_path / "config.json"
    caminho.write_text(conteudo, encoding="utf-8")
    with pytest.raises(ValueError):
        arquivos.ler_json(caminho)


@pytest.mark.parametrize("conteudo", ["", "{", "{'a': 1}", "nao e json"])
def test_json_quebrado_e_recusado(tmp_path: Path, conteudo):
    caminho = tmp_path / "config.json"
    caminho.write_text(conteudo, encoding="utf-8")
    with pytest.raises(ValueError):
        arquivos.ler_json(caminho)


def test_modo_segredo_e_0600():
    assert arquivos.MODO_SEGREDO == 0o600


def test_json_gravado_pode_ser_lido_por_outra_ferramenta(tmp_path: Path):
    caminho = tmp_path / "config.json"
    arquivos.escrever_json(caminho, {"schema_version": 1})
    assert json.loads(caminho.read_text(encoding="utf-8")) == {"schema_version": 1}


def test_diretorio_de_dados_nasce_0700(tmp_path: Path, umask_aberto):
    # Why: this directory holds every secret of section 9; on a host with umask 022 it would
    # be born 0755 and any local user could list the names of those files.
    # Por que: este diretório guarda todo segredo da seção 9; num hospedeiro com umask 022 ele
    # nasceria 0755 e qualquer usuário local listaria os nomes desses arquivos.
    caminho = tmp_path / "data"
    arquivos.garantir_diretorio(caminho)
    assert arquivos.modo_de(caminho) == 0o700


def test_diretorio_de_dados_que_ja_existia_e_fechado(tmp_path: Path):
    caminho = tmp_path / "data"
    caminho.mkdir(mode=0o755)
    caminho.chmod(0o755)
    arquivos.garantir_diretorio(caminho)
    assert arquivos.modo_de(caminho) == 0o700


def test_diretorio_de_dados_mais_fechado_nao_e_alargado(tmp_path: Path):
    caminho = tmp_path / "data"
    caminho.mkdir()
    caminho.chmod(0o500)
    try:
        arquivos.garantir_diretorio(caminho)
        assert arquivos.modo_de(caminho) == 0o500
    finally:
        caminho.chmod(0o700)


def test_garantir_diretorio_cria_os_pais_e_repete_sem_erro(tmp_path: Path, umask_aberto):
    caminho = tmp_path / "pai" / "data"
    arquivos.garantir_diretorio(caminho)
    arquivos.garantir_diretorio(caminho)
    assert caminho.is_dir()
    assert arquivos.modo_de(caminho) == 0o700


def test_link_no_lugar_do_arquivo_nao_e_seguido(tmp_path: Path):
    # Why: a link planted in the data directory would turn the daemon into a reader of any
    # file the container can open, and the ownership code goes straight to the log.
    # Por que: um link plantado no diretório de dados transformaria o daemon num leitor de
    # qualquer arquivo que o container abra, e o código de posse vai direto para o log.
    alvo = tmp_path / "fora.txt"
    alvo.write_text("segredo do hospedeiro\n", encoding="utf-8")
    link = tmp_path / "segredo.txt"
    link.symlink_to(alvo)
    assert arquivos.ler_texto(link) is None
    assert arquivos.ler_json(link) is None


def test_link_quebrado_tambem_e_ausente(tmp_path: Path):
    link = tmp_path / "segredo.txt"
    link.symlink_to(tmp_path / "nao-existe.txt")
    assert arquivos.ler_texto(link) is None


def test_escrita_sobre_um_link_troca_o_link_e_nao_o_alvo(tmp_path: Path):
    alvo = tmp_path / "fora.txt"
    alvo.write_text("segredo do hospedeiro\n", encoding="utf-8")
    link = tmp_path / "segredo.txt"
    link.symlink_to(alvo)
    arquivos.escrever_texto(link, SEGREDO)
    assert not link.is_symlink()
    assert arquivos.modo_de(link) == 0o600
    assert alvo.read_text(encoding="utf-8") == "segredo do hospedeiro\n"


def test_escrita_sincroniza_o_diretorio_depois_da_troca(tmp_path: Path, monkeypatch):
    # Why: the appliance goes down by losing power; without the directory fsync the rename
    # that installs config.json is not durable, and the file comes back empty or old.
    # Por que: o appliance cai por falta de energia; sem o fsync do diretório a troca de nome
    # que instala o config.json não é durável, e o arquivo volta vazio ou velho.
    alvos = []
    real = os.fsync

    def espiar(descritor):
        alvos.append(stat.S_ISDIR(os.fstat(descritor).st_mode))
        return real(descritor)

    monkeypatch.setattr(os, "fsync", espiar)
    arquivos.escrever_texto(tmp_path / "segredo.txt", SEGREDO)
    assert alvos == [False, True]


def test_diretorio_que_recusa_fsync_nao_quebra_a_escrita(tmp_path: Path, monkeypatch):
    real = os.fsync

    def talvez(descritor):
        if stat.S_ISDIR(os.fstat(descritor).st_mode):
            raise OSError(errno.EINVAL, "fsync on a directory is not supported here")
        return real(descritor)

    monkeypatch.setattr(os, "fsync", talvez)
    arquivos.escrever_texto(tmp_path / "segredo.txt", SEGREDO)
    assert arquivos.ler_texto(tmp_path / "segredo.txt") == SEGREDO
