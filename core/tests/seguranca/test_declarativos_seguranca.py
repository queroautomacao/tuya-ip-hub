# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 7 and 9 over the driver routes: every test here attacks a rule.

It tries to save a driver with no session, to write a file outside the drivers directory, to
replace code that ships in the image with data, to plant a regex that would freeze the
daemon in a poll, to make the hub talk to a host nobody registered, to delete the driver an
equipment depends on, and to leave a file behind on a save that was refused.

Seções 7 e 9 sobre as rotas de driver: todo teste aqui ataca uma regra.

Ele tenta salvar um driver sem sessão, gravar arquivo fora da pasta de drivers, trocar por
dado o código que embarca na imagem, plantar uma regex que congelaria o daemon num poll,
fazer o hub falar com um host que ninguém cadastrou, apagar o driver de que um equipamento
depende, e deixar arquivo para trás num salvamento que foi recusado.
"""

import json
from pathlib import Path

import pytest

from iphub.api.declarativos import CORPO_MAXIMO_DRIVER
from iphub.arquivos import modo_de
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.drivers import catalogo as modulo_catalogo
from iphub.portao import CABECALHOS

TIPO = "matriz_de_teste"
NATIVO = "projetor_pjlink"
IP = "192.0.2.10"

# Overlapping alternation: no heuristic catches it, and the fire test of section 7 does.
# Alternância sobreposta: heurística nenhuma pega, e a prova de fogo da seção 7 pega.
REGEX_CATASTROFICA = r"(a|aa)+$"

ROTAS = (
    ("GET", "/api/drivers"),
    ("POST", "/api/drivers"),
    ("POST", "/api/drivers/validar"),
    ("DELETE", f"/api/drivers/{TIPO}"),
    ("GET", "/api/drivers/modelo/tcp"),
)

# Every one of these would be a file name if the tipo were taken as one; none of them is an
# identifier of [a-z0-9_], which is the alphabet the validation accepts.
# Cada um destes viraria nome de arquivo se o tipo fosse tomado como um; nenhum é
# identificador de [a-z0-9_], que é o alfabeto que a validação aceita.
TIPOS_DE_FUGA = (
    "../fora",
    "..",
    "/etc/passwd",
    "drivers/../../config",
    "config.json",
    "matriz\x00.json",
    "matriz de teste",
    "MATRIZ",
    "",
    "a" * 33,
)


def _declaracao(tipo: str = TIPO, **trocas: object) -> dict:
    dados: dict = {
        "manifesto": {
            "tipo": tipo,
            "rotulo": {"pt": "Matriz de teste", "en": "Test matrix"},
            "categoria": "matriz",
            "capacidades": ["ligar"],
        },
        "transporte": {"tcp": {"porta": 23}},
        "comandos": {"ligar": {"envia": "SET POWER ON"}},
    }
    for chave, valor in trocas.items():
        if chave == "manifesto" and isinstance(valor, dict):
            dados["manifesto"].update(valor)
        else:
            dados[chave] = valor
    return dados


@pytest.fixture
def pasta(amb) -> Path:
    return amb.dir_data / modulo_catalogo.PASTA_INTEGRADOR


# Why: the image ships an empty embedded catalogue, and these attacks need one embedded
# driver to aim at, so the examples of milestone 3 stand in for it.
# Por que: a imagem embarca um catálogo vazio, e estes ataques precisam de um driver embarcado
# para mirar, então os exemplos do marco 3 fazem as vezes dele.
EXEMPLOS = Path(__file__).resolve().parents[1] / "drivers" / "exemplos"


@pytest.fixture
async def cliente_dr(fabrica_cliente, monkeypatch):
    monkeypatch.setattr(modulo_catalogo, "PASTA_EMBARCADA", EXEMPLOS)
    return await fabrica_cliente()


@pytest.fixture
async def com_dono(cliente_dr, posse, bearer):
    return cliente_dr, bearer(await posse(cliente_dr))


def _confere_cabecalhos(resposta) -> None:
    for nome, valor in CABECALHOS.items():
        assert resposta.headers.get(nome) == valor, nome


def _arquivos(pasta: Path) -> list[str]:
    return sorted(caminho.name for caminho in pasta.iterdir()) if pasta.is_dir() else []


async def _salvar(cliente, auth, dados: dict):
    return await cliente.post("/api/drivers", json={"json": dados}, headers=auth)


async def _codigos(resposta) -> set[str]:
    corpo = await resposta.json()
    return {problema["codigo"] for problema in corpo.get("problemas", [])}


# ---------- the session, section 9 ----------


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_nenhuma_rota_de_driver_responde_sem_sessao(cliente_dr, metodo, caminho, pasta):
    resposta = await cliente_dr.request(metodo, caminho, json={"json": _declaracao()})
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "nao_autenticado"}
    _confere_cabecalhos(resposta)
    assert _arquivos(pasta) == []


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_token_inventado_nao_abre_nenhuma_rota_de_driver(cliente_dr, metodo, caminho, bearer):
    resposta = await cliente_dr.request(
        metodo, caminho, json={"json": _declaracao()}, headers=bearer("nao-sou-token")
    )
    assert resposta.status == 401
    assert await resposta.json() == {"ok": False, "code": "sessao_invalida"}
    _confere_cabecalhos(resposta)


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_toda_rota_de_driver_carrega_os_cabecalhos(com_dono, metodo, caminho):
    cliente, auth = com_dono
    resposta = await cliente.request(metodo, caminho, json={"json": _declaracao()}, headers=auth)
    _confere_cabecalhos(resposta)
    assert resposta.headers["Content-Type"].startswith("application/json")


async def test_os_cabecalhos_acompanham_tambem_a_recusa_de_um_arquivo(com_dono):
    cliente, auth = com_dono
    resposta = await _salvar(cliente, auth, {"manifesto": "isto nao e um manifesto"})
    assert resposta.status == 400
    _confere_cabecalhos(resposta)


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_origin_de_outro_site_e_403_em_toda_rota_de_driver(com_dono, metodo, caminho, pasta):
    cliente, auth = com_dono
    cabecalhos = {**auth, "Origin": "http://evil.example.com"}
    resposta = await cliente.request(
        metodo, caminho, json={"json": _declaracao()}, headers=cabecalhos
    )
    assert resposta.status == 403
    assert await resposta.json() == {"ok": False, "code": "origem_nao_permitida"}
    assert _arquivos(pasta) == []


# ---------- the tipo is a file name, section 9 ----------


@pytest.mark.parametrize("tipo", TIPOS_DE_FUGA)
async def test_um_tipo_que_tenta_sair_da_pasta_de_drivers_e_recusado(com_dono, tipo, amb, pasta):
    """The tipo is the name of the file the daemon writes, so anything but an identifier is
    an attempt to write somewhere else.

    O tipo é o nome do arquivo que o daemon grava, então tudo que não é identificador é
    tentativa de gravar em outro lugar.
    """
    cliente, auth = com_dono
    antes = sorted(caminho.name for caminho in amb.dir_data.iterdir())
    resposta = await _salvar(cliente, auth, _declaracao(tipo))
    assert resposta.status == 400
    assert "decl_tipo_invalido" in await _codigos(resposta)
    assert _arquivos(pasta) == []
    assert sorted(caminho.name for caminho in amb.dir_data.iterdir()) == antes


@pytest.mark.parametrize(
    "caminho",
    [
        "/api/drivers/..",
        "/api/drivers/../config.json",
        "/api/drivers/..%2f..%2fconfig.json",
        "/api/drivers/%2e%2e%2fconfig.json",
        f"/api/drivers/{ARQUIVO_CONFIG}",
    ],
)
async def test_uma_remocao_nunca_alcanca_um_arquivo_fora_da_pasta(com_dono, amb, caminho):
    """The file a delete removes is the one the loader found, never a name from the path.

    O arquivo que uma remoção apaga é o que o carregador achou, nunca um nome do caminho.
    """
    cliente, auth = com_dono
    resposta = await cliente.delete(caminho, headers=auth)
    assert resposta.status != 200
    assert (await resposta.json())["ok"] is False
    assert (amb.dir_data / ARQUIVO_CONFIG).is_file()


# ---------- data never replaces code, rule 3 of section 2 ----------


async def test_um_json_com_o_tipo_de_um_nativo_e_recusado(com_dono, pasta):
    cliente, auth = com_dono
    resposta = await _salvar(cliente, auth, _declaracao(NATIVO))
    assert resposta.status == 400
    assert await _codigos(resposta) == {modulo_catalogo.DECL_TIPO_OCUPADO}
    assert _arquivos(pasta) == []
    catalogo = (await (await cliente.get("/api/catalogo", headers=auth)).json())["catalogo"]
    nativo = next(item for item in catalogo if item["tipo"] == NATIVO)
    assert nativo["motor"] == "nativo"


# ---------- section 7 under attack ----------


async def test_uma_regex_catastrofica_e_recusada_ao_salvar_e_nunca_chega_ao_poll(com_dono, pasta):
    """The fire test runs when the file is saved, not in the middle of a poll: `re` does not
    release the GIL, so one catastrophic pattern would freeze the daemon whole.

    A prova de fogo roda ao salvar o arquivo, não no meio de um poll: o `re` não solta a GIL,
    então um padrão catastrófico congelaria o daemon inteiro.
    """
    cliente, auth = com_dono
    explosiva = _declaracao(
        estado={"pede": [{"envia": "GET"}], "le": {"fonte": {"regex": REGEX_CATASTROFICA}}}
    )
    resposta = await _salvar(cliente, auth, explosiva)
    assert resposta.status == 400
    assert await _codigos(resposta) == {"decl_regex_perigosa"}
    assert _arquivos(pasta) == []


async def test_uma_base_que_nao_e_o_proprio_aparelho_e_recusada(com_dono, pasta):
    """Section 9 on the driver side: the hub never becomes a proxy into the LAN, and a driver
    received ready made never sends the internal address of the customer somewhere else.

    Seção 9 do lado do driver: o hub nunca vira proxy da LAN, e um driver recebido pronto
    nunca manda o endereço interno do cliente para outro lugar.
    """
    cliente, auth = com_dono
    vazamento = _declaracao(
        transporte={"http": {"base": "http://198.51.100.7"}},
        comandos={"ligar": {"envia": "/on"}},
    )
    resposta = await _salvar(cliente, auth, vazamento)
    assert resposta.status == 400
    assert await _codigos(resposta) == {"decl_base_invalida"}
    assert _arquivos(pasta) == []


async def test_um_arquivo_maior_que_o_teto_nao_e_gravado(com_dono, pasta):
    """The loader refuses a file past the ceiling, so saving one would write a driver that
    never loads again and that the hub would list only until the next boot.

    O carregador recusa arquivo acima do teto, então salvar um gravaria um driver que nunca
    mais carrega e que o hub listaria só até o próximo boot.
    """
    # The file goes past the ceiling of the loader while the body still fits the one of the
    # route, so what refuses it here is the size of the file and not the size of the request.
    # O arquivo passa do teto do carregador enquanto o corpo ainda cabe no da rota, então o que
    # o recusa aqui é o tamanho do arquivo e não o tamanho da requisição.
    cliente, auth = com_dono
    gordo = _declaracao()
    gordo["manifesto"]["textos"] = {
        idioma: {f"campo_{indice}": "x" * 500 for indice in range(63)} for idioma in ("pt", "en")
    }
    resposta = await _salvar(cliente, auth, gordo)
    assert resposta.status == 400
    assert await _codigos(resposta) == {modulo_catalogo.DECL_ARQUIVO_GRANDE}
    assert _arquivos(pasta) == []


async def test_um_corpo_maior_que_o_teto_da_rota_e_recusado(com_dono, pasta):
    cliente, auth = com_dono
    enorme = _declaracao(manifesto={"rotulo": {"pt": "x" * CORPO_MAXIMO_DRIVER, "en": "y"}})
    resposta = await _salvar(cliente, auth, enorme)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "corpo_invalido"
    assert _arquivos(pasta) == []


# ---------- what a save leaves on the disk ----------


async def test_o_arquivo_salvo_nasce_0600_na_pasta_0700(com_dono, pasta):
    cliente, auth = com_dono
    assert (await _salvar(cliente, auth, _declaracao())).status == 200
    assert modo_de(pasta / f"{TIPO}.json") == 0o600
    assert modo_de(pasta) == 0o700


async def test_um_salvamento_recusado_nao_deixa_arquivo_pela_metade(com_dono, pasta):
    """No partial save: a file the validation refused was never on the disk, and a refusal
    after a good save leaves the good one exactly as it was.

    Sem salvamento parcial: um arquivo que a validação recusou nunca esteve no disco, e uma
    recusa depois de um salvamento bom deixa o bom exatamente como estava.
    """
    cliente, auth = com_dono
    assert (await _salvar(cliente, auth, _declaracao())).status == 200
    bom = (pasta / f"{TIPO}.json").read_text(encoding="utf-8")
    torto = _declaracao(transporte={"tcp": {"porta": 0}})
    assert (await _salvar(cliente, auth, torto)).status == 400
    assert (pasta / f"{TIPO}.json").read_text(encoding="utf-8") == bom
    assert _arquivos(pasta) == [f"{TIPO}.json"]


async def test_um_driver_em_uso_nao_pode_ser_apagado(com_dono, pasta):
    """Deleting the driver of a registered equipment would leave a device of an unknown tipo,
    offline forever and with no way back.

    Apagar o driver de um equipamento cadastrado deixaria um aparelho de tipo desconhecido,
    offline para sempre e sem volta.
    """
    cliente, auth = com_dono
    assert (await _salvar(cliente, auth, _declaracao())).status == 200
    cadastro = {"tipo": TIPO, "identidade": "uuid-1", "nome": "Sala", "ip": IP}
    assert (await cliente.post("/api/equipamentos", json=cadastro, headers=auth)).status == 200
    resposta = await cliente.delete(f"/api/drivers/{TIPO}", headers=auth)
    assert resposta.status == 409
    assert (await resposta.json())["code"] == "decl_em_uso"
    assert (pasta / f"{TIPO}.json").is_file()
    lista = (await (await cliente.get("/api/equipamentos", headers=auth)).json())["equipamentos"]
    assert [equipamento["tipo"] for equipamento in lista] == [TIPO]


async def test_um_driver_da_imagem_nao_e_apagado_pelo_painel(com_dono):
    """The panel writes into the data directory, and an embedded file is not in it.

    O painel grava no diretório de dados, e um arquivo embarcado não está nele.
    """
    cliente, auth = com_dono
    resposta = await cliente.delete("/api/drivers/matriz_hdmi_ascii", headers=auth)
    assert resposta.status == 404
    assert (await resposta.json())["code"] == "decl_nao_encontrado"
    assert (EXEMPLOS / "matriz_hdmi_ascii.json").is_file()
    drivers = (await (await cliente.get("/api/drivers", headers=auth)).json())["drivers"]
    assert "matriz_hdmi_ascii" in {driver["tipo"] for driver in drivers}


async def test_o_arquivo_gravado_e_o_json_que_o_carregador_le(com_dono, pasta):
    """What is written is the file itself, so the driver that loads is the one that was sent.

    O que é gravado é o próprio arquivo, então o driver que carrega é o que foi enviado.
    """
    cliente, auth = com_dono
    assert (await _salvar(cliente, auth, _declaracao())).status == 200
    gravado = json.loads((pasta / f"{TIPO}.json").read_text(encoding="utf-8"))
    assert gravado == _declaracao()
