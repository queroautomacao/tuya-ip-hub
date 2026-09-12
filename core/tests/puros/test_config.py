# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Configuration on disk: defaults, round trip, 0600, and a format that refuses to be guessed."""

import json
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest
from aiohttp import web

from iphub import arquivos, auth, config
from iphub.ambiente import Ambiente
from iphub.api.comum import AMBIENTE, CONFIG, Mutavel, trocar_config
from iphub.versao import SCHEMA_VERSION

CADASTRO = config.Cadastro(
    identidade="uuid-da-caixa",
    tipo="projetor_pjlink",
    nome="Sala",
    ip="192.0.2.10",
    campos={"porta": "4352"},
    segredos={"senha": "segredo-do-aparelho"},
)

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
    # A half written password must never count as a configured hub.
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
    # The caller that refuses to boot catches ValueError; the subclass must stay under it.
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
    # A wrong type would reach the gate as a Host allowlist or the login as an iteration
    # count; the file is refused instead of coerced.
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
    # The value goes straight to pbkdf2_hmac on every login, so a hand edited huge number
    # hangs the daemon on the login route and a tiny one makes the stored hash cheap to crack.
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "senha_iteracoes": iteracoes})
    with pytest.raises(config.ConfigIncompativel, match="senha_iteracoes") as erro:
        config.carregar(dir_data)
    assert str(dir_data) in str(erro.value)


@pytest.mark.parametrize("iteracoes", [config.ITERACOES_MINIMAS, 200_000, config.ITERACOES_MAXIMAS])
def test_iteracoes_dentro_da_faixa_carregam(dir_data: Path, iteracoes):
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "senha_iteracoes": iteracoes})
    assert config.carregar(dir_data).senha_iteracoes == iteracoes


def test_iteracoes_zero_e_o_hub_que_ainda_nao_tem_senha(dir_data: Path):
    # Zero is the default of a hub with no password, and auth.conferir already refuses it;
    # the band must not turn that state into a daemon that will not boot.
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "senha_iteracoes": 0})
    cfg = config.carregar(dir_data)
    assert cfg.senha_iteracoes == 0
    assert cfg.configurado is False


def test_a_faixa_de_iteracoes_cerca_o_padrao_da_secao_9():
    assert config.ITERACOES_MINIMAS <= auth.ITERACOES <= config.ITERACOES_MAXIMAS


def test_sem_a_chave_equipamentos_o_hub_esta_vazio(dir_data: Path):
    """Zero equipment is a normal hub, never a broken file."""
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION})
    assert config.carregar(dir_data).equipamentos == ()


def test_equipamentos_ida_e_volta(dir_data: Path):
    outro = config.Cadastro(identidade="uuid-2", tipo="matriz_exemplo")
    cfg = replace(CHEIA, equipamentos=(CADASTRO, outro))
    config.salvar(cfg, dir_data)
    lido = config.carregar(dir_data)
    assert lido == cfg
    assert lido.equipamentos[0].campos == {"porta": "4352"}
    assert lido.equipamentos[1] == outro


def test_credencial_de_aparelho_vive_no_config_selada_e_ele_nasce_0600(dir_data: Path):
    """Device credentials live in config.json, sealed by the vault, and that file is 0600."""
    config.salvar(replace(CHEIA, equipamentos=(CADASTRO,)), dir_data)
    caminho = dir_data / config.ARQUIVO
    assert arquivos.modo_de(caminho) == 0o600
    texto = caminho.read_text(encoding="utf-8")
    assert "segredo-do-aparelho" not in texto
    assert "cofre:" in texto
    assert config.carregar(dir_data).equipamentos[0].segredos == CADASTRO.segredos


@pytest.mark.parametrize(
    "item",
    [
        {"tipo": "projetor_pjlink"},
        {"identidade": "uuid-1"},
        {"identidade": "", "tipo": "projetor_pjlink"},
        {"identidade": "uuid-1", "tipo": ""},
        {"identidade": "   ", "tipo": "projetor_pjlink"},
        {"identidade": "uuid-1", "tipo": "   "},
    ],
)
def test_equipamento_sem_identidade_ou_sem_tipo_recusa_carregar(dir_data: Path, item):
    # The identity is the key of the equipment and the tipo is the key of the driver; a
    # row missing either one would load as an equipment nothing can ever reach.
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "equipamentos": [item]})
    with pytest.raises(config.ConfigIncompativel, match="equipamentos"):
        config.carregar(dir_data)


@pytest.mark.parametrize(
    ("equipamentos", "trecho"),
    [
        ({"uuid-1": {}}, "equipamentos"),
        ("uuid-1", "equipamentos"),
        ([[]], "equipamentos"),
        (["uuid-1"], "equipamentos"),
        ([None], "equipamentos"),
        ([{"identidade": 7, "tipo": "t"}], "identidade"),
        ([{"identidade": "u", "tipo": ["t"]}], "tipo"),
        ([{"identidade": "u", "tipo": "t", "nome": 1}], "nome"),
        ([{"identidade": "u", "tipo": "t", "ip": None}], "ip"),
        ([{"identidade": "u", "tipo": "t", "campos": "porta=1"}], "campos"),
        ([{"identidade": "u", "tipo": "t", "campos": {"porta": 4352}}], "campos"),
        ([{"identidade": "u", "tipo": "t", "campos": {"1": "a"}, "segredos": []}], "segredos"),
        ([{"identidade": "u", "tipo": "t", "segredos": {"senha": None}}], "segredos"),
    ],
)
def test_equipamento_de_tipo_errado_recusa_carregar(dir_data: Path, equipamentos, trecho):
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "equipamentos": equipamentos})
    with pytest.raises(config.ConfigIncompativel, match=trecho):
        config.carregar(dir_data)


def test_identidade_repetida_recusa_carregar(dir_data: Path):
    """Two rows sharing one identity would silently become one device."""
    linha = {"identidade": "uuid-1", "tipo": "projetor_pjlink"}
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "equipamentos": [linha, dict(linha)]})
    with pytest.raises(config.ConfigIncompativel, match="uuid-1"):
        config.carregar(dir_data)


def test_chave_desconhecida_dentro_do_equipamento_e_ignorada(dir_data: Path):
    linha = {"identidade": "uuid-1", "tipo": "t", "sobra": {"a": 1}}
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "equipamentos": [linha]})
    assert config.carregar(dir_data).equipamentos == (config.Cadastro("uuid-1", "t"),)


def test_cadastro_e_imutavel():
    with pytest.raises(FrozenInstanceError):
        CADASTRO.ip = "192.0.2.11"  # type: ignore[misc]


def test_trocar_config_guarda_cadastros_e_nao_dicts(dir_data: Path, tmp_path: Path):
    """The equipment survives the API helper as a Cadastro, never as a raw dict."""
    app = web.Application()
    app[AMBIENTE] = Ambiente(bind="127.0.0.1", porta=8080, dir_data=dir_data, dir_painel=tmp_path)
    app[CONFIG] = Mutavel(config.Config())
    trocar_config(app, config.Config(equipamentos=(CADASTRO,)))
    vivo = app[CONFIG].valor.equipamentos
    assert vivo == (CADASTRO,)
    assert isinstance(vivo[0], config.Cadastro)
    assert config.carregar(dir_data).equipamentos == (CADASTRO,)


@pytest.mark.parametrize(
    "endereco",
    [
        "projetor.local",
        "localhost",
        "http://192.0.2.10",
        "192.0.2.10:4352",
        "192.0.2.10 ",
        "1922.0.2.10",
        "fe80::1%eth0",
        "[2001:db8::1]",
        "a" * 60,
    ],
)
def test_ip_que_nao_e_literal_recusa_carregar(dir_data: Path, endereco):
    """The file is the other door into the field the write routes already guard."""
    # A hand edited hostname loads and the action route then dials it, which is the hub
    # working as a proxy into the LAN of the client, the exact thing closes.
    linha = {"identidade": "uuid-1", "tipo": "projetor_pjlink", "ip": endereco}
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "equipamentos": [linha]})
    with pytest.raises(config.ConfigIncompativel, match="ip"):
        config.carregar(dir_data)


@pytest.mark.parametrize("endereco", ["192.0.2.10", "2001:db8::1", "127.0.0.1", "::1", ""])
def test_ip_literal_e_ip_vazio_carregam(dir_data: Path, endereco):
    """An empty ip is a registration whose address is not known yet, which is normal."""
    linha = {"identidade": "uuid-1", "tipo": "projetor_pjlink", "ip": endereco}
    _gravar_cru(dir_data, {"schema_version": SCHEMA_VERSION, "equipamentos": [linha]})
    assert config.carregar(dir_data).equipamentos[0].ip == endereco


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("192.0.2.10", "192.0.2.10"),
        ("2001:0DB8::0001", "2001:db8::1"),
        ("projetor.local", None),
        ("fe80::1%eth0", None),
        ("", None),
        (None, None),
        (7, None),
        ("192.0.2.10\n", None),
    ],
)
def test_ip_literal_conhece_endereco_e_recusa_o_resto(texto, esperado):
    assert config.ip_literal(texto) == esperado
