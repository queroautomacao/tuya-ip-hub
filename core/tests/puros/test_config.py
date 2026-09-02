# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Configuration on disk: defaults, round trip, 0600, and a format that refuses to be guessed.

Configuração em disco: padrões, ida e volta, 0600, e um formato que se recusa a ser adivinhado.
"""

import json
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from iphub import arquivos, auth, config
from iphub.versao import SCHEMA_VERSION

CHEIA = config.Config(
    nome_instalacao="Casa de teste",
    idioma="en",
    hosts_permitidos=("hub.local",),
    proxies_confiaveis=("192.0.2.9",),
    senha_salt="a1b2",
    senha_hash="c3d4",
    senha_iteracoes=200_000,
)


@pytest.fixture
def dir_data(tmp_path: Path) -> Path:
    caminho = tmp_path / "data"
    caminho.mkdir()
    return caminho


def _gravar_cru(dir_data: Path, dados: dict) -> Path:
    caminho = dir_data / config.ARQUIVO
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return caminho


def test_sem_arquivo_e_um_hub_nunca_configurado(dir_data: Path):
    cfg = config.carregar(dir_data)
    assert cfg == config.Config()
    assert cfg.configurado is False
    assert cfg.idioma == "pt"
    assert cfg.hosts_permitidos == ()
    assert cfg.proxies_confiaveis == ()


@pytest.mark.parametrize(
    ("salt", "hash_", "esperado"),
    [("", "", False), ("a1", "", False), ("", "b2", False), ("a1", "b2", True)],
)
def test_configurado_exige_salt_e_hash(salt, hash_, esperado):
    # Why: a half written password must never count as a configured hub.
    # Por que: uma senha gravada pela metade nunca pode contar como hub configurado.
    assert config.Config(senha_salt=salt, senha_hash=hash_).configurado is esperado


def test_ida_e_volta(dir_data: Path):
    config.salvar(CHEIA, dir_data)
    assert config.carregar(dir_data) == CHEIA


def test_arquivo_nasce_0600(dir_data: Path):
    config.salvar(CHEIA, dir_data)
    assert arquivos.modo_de(dir_data / config.ARQUIVO) == 0o600


def test_salvar_nao_alarga_modo_existente(dir_data: Path):
    caminho = dir_data / config.ARQUIVO
    config.salvar(CHEIA, dir_data)
    caminho.chmod(0o400)
    config.salvar(CHEIA, dir_data)
    assert arquivos.modo_de(caminho) == 0o400


def test_salvar_fecha_modo_folgado(dir_data: Path):
    caminho = dir_data / config.ARQUIVO
    config.salvar(CHEIA, dir_data)
    caminho.chmod(0o644)
    config.salvar(CHEIA, dir_data)
    assert arquivos.modo_de(caminho) == 0o600


def test_arquivo_carrega_schema_e_todos_os_campos(dir_data: Path):
    config.salvar(CHEIA, dir_data)
    dados = json.loads((dir_data / config.ARQUIVO).read_text(encoding="utf-8"))
    assert dados["schema_version"] == SCHEMA_VERSION
    assert set(dados) == {"schema_version"} | {campo.name for campo in fields(config.Config)}


@pytest.mark.parametrize("schema", [SCHEMA_VERSION + 1, SCHEMA_VERSION - 1, "1", None, True])
def test_schema_diferente_recusa_carregar(dir_data: Path, schema):
    _gravar_cru(dir_data, {"schema_version": schema, "idioma": "pt"})
    with pytest.raises(config.ConfigIncompativel) as erro:
        config.carregar(dir_data)
    mensagem = str(erro.value)
    assert repr(schema) in mensagem
    assert str(SCHEMA_VERSION) in mensagem
    assert str(dir_data) in mensagem


def test_schema_ausente_recusa_carregar(dir_data: Path):
    _gravar_cru(dir_data, {"idioma": "pt"})
    with pytest.raises(config.ConfigIncompativel):
        config.carregar(dir_data)


def test_incompatibilidade_e_um_value_error(dir_data: Path):
    # Why: the caller that refuses to boot catches ValueError; the subclass must stay under it.
    # Por que: quem recusa o boot captura ValueError; a subclasse tem de ficar debaixo dele.
    assert issubclass(config.ConfigIncompativel, ValueError)


@pytest.mark.parametrize(
    ("chave", "valor"),
    [
        ("nome_instalacao", 7),
        ("nome_instalacao", None),
        ("idioma", ["pt"]),
        ("hosts_permitidos", "hub.local"),
        ("hosts_permitidos", [1, 2]),
        ("hosts_permitidos", {"a": "b"}),
        ("proxies_confiaveis", "192.0.2.9"),
        ("senha_salt", 1),
        ("senha_hash", None),
        ("senha_iteracoes", "200000"),
        ("senha_iteracoes", 1.5),
        ("senha_iteracoes", True),
    ],
)
def test_tipo_errado_recusa_carregar(dir_data: Path, chave, valor):
    # Why: a wrong type would reach the gate as a Host allowlist or the login as an iteration
    # count; the file is refused instead of coerced.
    # Por que: um tipo errado chegaria ao portão como lista de Host ou ao login como contagem
    # de iterações; o arquivo é recusado em vez de convertido.
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, chave: valor})
    with pytest.raises(config.ConfigIncompativel, match=chave):
        config.carregar(dir_data)


def test_chave_desconhecida_e_ignorada(dir_data: Path):
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "idioma": "en", "sobra": {"a": 1}})
    assert config.carregar(dir_data).idioma == "en"


def test_chave_faltando_usa_o_padrao(dir_data: Path):
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "nome_instalacao": "Casa"})
    cfg = config.carregar(dir_data)
    assert cfg.nome_instalacao == "Casa"
    assert cfg.idioma == config.Config().idioma
    assert cfg.senha_iteracoes == 0


def test_listas_voltam_como_tupla(dir_data: Path):
    _gravar_cru(
        dir_data,
        {"schema_version": SCHEMA_VERSION, "hosts_permitidos": ["hub.local", "outro.local"]},
    )
    assert config.carregar(dir_data).hosts_permitidos == ("hub.local", "outro.local")


@pytest.mark.parametrize("conteudo", ["", "{", "[]", '"texto"', "nao e json"])
def test_arquivo_quebrado_recusa_carregar(dir_data: Path, conteudo):
    (dir_data / config.ARQUIVO).write_text(conteudo, encoding="utf-8")
    with pytest.raises(config.ConfigIncompativel):
        config.carregar(dir_data)


def test_config_e_imutavel():
    with pytest.raises(FrozenInstanceError):
        config.Config().idioma = "en"  # type: ignore[misc]


def test_senha_gravada_sobrevive_a_um_novo_salvamento(dir_data: Path):
    config.salvar(CHEIA, dir_data)
    cfg = config.carregar(dir_data)
    config.salvar(replace(cfg, nome_instalacao="Outra"), dir_data)
    depois = config.carregar(dir_data)
    assert depois.senha_hash == CHEIA.senha_hash
    assert depois.senha_iteracoes == CHEIA.senha_iteracoes
    assert depois.nome_instalacao == "Outra"


@pytest.mark.parametrize("iteracoes", [1, 999, -1, -200_000, 2_000_001, 10**9, 10**12])
def test_iteracoes_fora_da_faixa_recusam_carregar(dir_data: Path, iteracoes):
    # Why: the value goes straight to pbkdf2_hmac on every login, so a hand edited huge number
    # hangs the daemon on the login route and a tiny one makes the stored hash cheap to crack.
    # Por que: o valor vai direto para o pbkdf2_hmac em cada entrada, então um número enorme
    # editado na mão trava o daemon na rota de entrada e um pequeno barateia o hash guardado.
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "senha_iteracoes": iteracoes})
    with pytest.raises(config.ConfigIncompativel, match="senha_iteracoes") as erro:
        config.carregar(dir_data)
    assert str(dir_data) in str(erro.value)


@pytest.mark.parametrize("iteracoes", [config.ITERACOES_MINIMAS, 200_000, config.ITERACOES_MAXIMAS])
def test_iteracoes_dentro_da_faixa_carregam(dir_data: Path, iteracoes):
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "senha_iteracoes": iteracoes})
    assert config.carregar(dir_data).senha_iteracoes == iteracoes


def test_iteracoes_zero_e_o_hub_que_ainda_nao_tem_senha(dir_data: Path):
    # Why: zero is the default of a hub with no password, and auth.conferir already refuses it;
    # the band must not turn that state into a daemon that will not boot.
    # Por que: zero é o padrão de um hub sem senha, e o auth.conferir já o recusa; a faixa não
    # pode transformar esse estado num daemon que não sobe.
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "senha_iteracoes": 0})
    cfg = config.carregar(dir_data)
    assert cfg.senha_iteracoes == 0
    assert cfg.configurado is False


def test_a_faixa_de_iteracoes_cerca_o_padrao_da_secao_9():
    assert config.ITERACOES_MINIMAS <= auth.ITERACOES <= config.ITERACOES_MAXIMAS
