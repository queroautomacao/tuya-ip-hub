# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the declarative driver routes: the listing, the one pass refusal, the save
that loads with no restart, the delete and the templates.

The exit gate of section 13 lives here: a driver saved through the panel is usable at once,
and an equipment already registered speaks the file that was just saved, not the one it was
born with.

O contrato das rotas de driver declarativo: a listagem, a recusa numa passada, o salvamento
que carrega sem reiniciar, a remoção e os modelos.

O portão de saída da seção 13 mora aqui: um driver salvo pelo painel serve na hora, e um
equipamento já cadastrado fala o arquivo que acabou de ser salvo, não o que ele nasceu falando.
"""

import asyncio
import json
import re
import threading
import time
from pathlib import Path

import pytest

from iphub import regex_seguro
from iphub.api import declarativos as rotas
from iphub.api.declarativos import MODELOS
from iphub.drivers import catalogo as modulo_catalogo
from iphub.drivers.declarativo import formato

RAIZ = Path(__file__).resolve().parents[3]

TIPO = "matriz_de_teste"
IP = "192.0.2.10"

# One embedded example of each transport, the three of the exit gate of section 13.
# Um exemplo embarcado de cada transporte, os três do portão de saída da seção 13.
EMBARCADOS = ("matriz_hdmi_ascii", "rele_http", "amplificador_udp")


def _declaracao(tipo: str = TIPO, **trocas: object) -> dict:
    """A file an integrator would write. It declares no estado block on purpose: these tests
    attack the routes, and a poll that talks to a device belongs to the drivers layer.

    Um arquivo que um integrador escreveria. Ele não declara bloco estado de propósito: estes
    testes atacam as rotas, e um poll que fala com aparelho é da camada de drivers.
    """
    dados: dict = {
        "manifesto": {
            "tipo": tipo,
            "rotulo": {"pt": "Matriz de teste", "en": "Test matrix"},
            "categoria": "matriz",
            "capacidades": ["ligar", "fonte"],
        },
        "transporte": {"tcp": {"porta": 23}},
        "comandos": {
            "ligar": {"envia": "SET POWER ON"},
            "fonte": {"envia": "SET OUT1 VS {valor}", "valores": {"HDMI 1": "IN1"}},
        },
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


@pytest.fixture
def plantar(pasta: Path):
    """Writes a driver file before the hub boots, the way an integrator with a shell does.

    Grava um arquivo de driver antes de o hub subir, como faz um integrador com um shell.
    """

    def gravar(tipo: str, dados: dict | None = None) -> Path:
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = pasta / f"{tipo}.json"
        arquivo.write_text(
            json.dumps(_declaracao(tipo) if dados is None else dados, ensure_ascii=False),
            encoding="utf-8",
        )
        return arquivo

    return gravar


@pytest.fixture
def abrir(fabrica_cliente, posse, bearer):
    """Boots a hub with the drivers of the image, already owned, and its session header.

    A test that plants a file first calls this itself, because a driver of the data directory
    is read when the daemon boots and a file written after that boot was never there for it.

    Sobe um hub com os drivers da imagem, já com dono, e o cabeçalho de sessão dele.

    Um teste que planta um arquivo antes chama isto ele mesmo, porque um driver do diretório
    de dados é lido quando o daemon sobe e um arquivo escrito depois nunca esteve lá para ele.
    """

    async def criar():
        cliente = await fabrica_cliente()
        return cliente, bearer(await posse(cliente))

    return criar


@pytest.fixture
async def hub(abrir):
    return await abrir()


async def _drivers(cliente, auth) -> dict[str, dict]:
    resposta = await cliente.get("/api/drivers", headers=auth)
    assert resposta.status == 200, await resposta.text()
    return {driver["tipo"]: driver for driver in (await resposta.json())["drivers"]}


async def _salvar(cliente, auth, dados: dict):
    return await cliente.post("/api/drivers", json={"json": dados}, headers=auth)


async def _validar(cliente, auth, dados: dict):
    return await cliente.post("/api/drivers/validar", json={"json": dados}, headers=auth)


async def _problemas(resposta) -> set[tuple[str, str]]:
    corpo = await resposta.json()
    return {(problema["campo"], problema["codigo"]) for problema in corpo["problemas"]}


async def _cadastrar(cliente, auth, tipo: str, identidade: str = "uuid-1"):
    return await cliente.post(
        "/api/equipamentos",
        json={"tipo": tipo, "identidade": identidade, "nome": "Sala", "ip": IP},
        headers=auth,
    )


async def _equipamento(cliente, auth, identidade: str = "uuid-1") -> dict:
    resposta = await cliente.get("/api/equipamentos", headers=auth)
    assert resposta.status == 200, await resposta.text()
    equipamentos = (await resposta.json())["equipamentos"]
    return next(eq for eq in equipamentos if eq["identidade"] == identidade)


# ---------- the listing ----------


async def test_a_listagem_traz_os_exemplos_da_imagem_com_o_manifesto_do_painel(hub):
    """Section 13: the hub carries the three examples, and the panel draws them from here.

    Seção 13: o hub carrega os três exemplos, e o painel os desenha a partir daqui.
    """
    cliente, auth = hub
    drivers = await _drivers(cliente, auth)
    assert set(EMBARCADOS) <= set(drivers)
    matriz = drivers["matriz_hdmi_ascii"]
    assert matriz["origem"] == "imagem"
    assert matriz["em_uso"] is False
    assert matriz["manifesto"]["motor"] == "declarativo"
    assert matriz["manifesto"]["tipo"] == "matriz_hdmi_ascii"
    assert set(matriz["manifesto"]["rotulo"]) == {"pt", "en"}
    assert set(matriz["manifesto"]["textos"]) == {"pt", "en"}
    assert "fonte" in matriz["manifesto"]["capacidades"]


async def test_a_listagem_traz_so_o_declarativo_e_nunca_o_nativo(hub):
    """Section 7: this screen edits JSON drivers, and a native is not one of them.

    Seção 7: esta tela edita drivers JSON, e um nativo não é um deles.
    """
    cliente, auth = hub
    drivers = await _drivers(cliente, auth)
    assert "projetor_pjlink" not in drivers
    assert all(driver["manifesto"]["motor"] == "declarativo" for driver in drivers.values())


async def test_um_equipamento_cadastrado_marca_o_driver_como_em_uso(hub):
    cliente, auth = hub
    assert (await _cadastrar(cliente, auth, "matriz_hdmi_ascii")).status == 200
    drivers = await _drivers(cliente, auth)
    assert drivers["matriz_hdmi_ascii"]["em_uso"] is True
    assert drivers["rele_http"]["em_uso"] is False


# ---------- validate: every problem at once ----------


async def test_um_arquivo_bom_e_aceito_e_nada_e_gravado(hub, pasta: Path):
    cliente, auth = hub
    resposta = await _validar(cliente, auth, _declaracao())
    assert resposta.status == 200
    assert (await resposta.json())["code"] is None
    assert TIPO not in await _drivers(cliente, auth)
    assert not pasta.exists() or list(pasta.glob("*.json")) == []


async def test_a_recusa_traz_todo_problema_de_uma_vez_por_campo(hub):
    """Section 7: one pass, so the integrator fixes the file once instead of once per error.

    Seção 7: uma passada, para o integrador consertar o arquivo uma vez e não uma por erro.
    """
    cliente, auth = hub
    torto = _declaracao(
        manifesto={"categoria": "nave"},
        transporte={"tcp": {"porta": 0, "timeout_s": 90}},
    )
    resposta = await _validar(cliente, auth, torto)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == modulo_catalogo.DECL_INVALIDO
    problemas = await _problemas(resposta)
    assert ("manifesto.categoria", "decl_categoria_invalida") in problemas
    assert ("transporte.tcp.porta", "decl_porta_invalida") in problemas
    assert ("transporte.tcp.timeout_s", "decl_timeout_invalido") in problemas


@pytest.mark.parametrize("corpo", [{}, {"json": "texto"}, {"json": []}, {"json": None}])
async def test_um_corpo_que_nao_leva_arquivo_e_recusado(hub, corpo):
    cliente, auth = hub
    for caminho in ("/api/drivers/validar", "/api/drivers"):
        resposta = await cliente.post(caminho, json=corpo, headers=auth)
        assert resposta.status == 400, caminho
        assert (await resposta.json())["code"] == "corpo_invalido"


# ---------- save: usable at once, with no restart ----------


async def test_o_driver_salvo_carrega_sem_reiniciar_e_aceita_cadastro(hub, pasta: Path):
    """The exit gate of section 13: saved through the panel, used without a restart.

    O portão de saída da seção 13: salvo pelo painel, usado sem reiniciar.
    """
    cliente, auth = hub
    assert (await _salvar(cliente, auth, _declaracao())).status == 200
    arquivo = pasta / f"{TIPO}.json"
    assert arquivo.is_file()
    assert json.loads(arquivo.read_text(encoding="utf-8"))["manifesto"]["tipo"] == TIPO
    drivers = await _drivers(cliente, auth)
    assert drivers[TIPO]["origem"] == "integrador"
    catalogo = (await (await cliente.get("/api/catalogo", headers=auth)).json())["catalogo"]
    assert TIPO in {item["tipo"] for item in catalogo}
    assert (await _cadastrar(cliente, auth, TIPO)).status == 200
    assert (await _equipamento(cliente, auth))["tipo"] == TIPO


async def test_salvar_de_novo_refaz_o_equipamento_que_usa_o_tipo(hub):
    """A driver that changed is a driver to build again: the equipment already registered
    must speak the file that was just saved, not the one it was born with.

    Um driver que mudou é um driver para montar de novo: o equipamento já cadastrado precisa
    falar o arquivo que acabou de ser salvo, não o que ele nasceu falando.
    """
    cliente, auth = hub
    assert (await _salvar(cliente, auth, _declaracao())).status == 200
    assert (await _cadastrar(cliente, auth, TIPO)).status == 200
    assert (await _equipamento(cliente, auth))["estado"]["fontes"] == ["HDMI 1"]
    novo = _declaracao(
        comandos={
            "ligar": {"envia": "SET POWER ON"},
            "fonte": {"envia": "SET OUT1 VS {valor}", "valores": {"HDMI 3": "IN3"}},
        }
    )
    assert (await _salvar(cliente, auth, novo)).status == 200
    assert (await _equipamento(cliente, auth))["estado"]["fontes"] == ["HDMI 3"]


async def test_o_arquivo_do_integrador_vence_o_da_imagem(hub, pasta: Path):
    """Section 7: the file of the integrator wins a conflict of tipo with an embedded one.

    Seção 7: o arquivo do integrador vence o conflito de tipo com um embarcado.
    """
    cliente, auth = hub
    assert (await _drivers(cliente, auth))["matriz_hdmi_ascii"]["origem"] == "imagem"
    resposta = await _salvar(cliente, auth, _declaracao("matriz_hdmi_ascii"))
    assert resposta.status == 200
    drivers = await _drivers(cliente, auth)
    assert drivers["matriz_hdmi_ascii"]["origem"] == "integrador"
    assert drivers["matriz_hdmi_ascii"]["manifesto"]["capacidades"] == ["ligar", "fonte"]
    assert (pasta / "matriz_hdmi_ascii.json").is_file()


async def test_um_arquivo_que_o_carregador_recusa_e_desfeito(abrir, plantar, pasta: Path):
    """The validation and the loader judge different things, and a save the loader punishes
    is undone: the file that was working comes back and the driver it would have pushed out
    of the catalog is still there.

    A validação e o carregador julgam coisas diferentes, e um salvamento que o carregador
    pune é desfeito: o arquivo que funcionava volta e o driver que ele teria empurrado para
    fora do catálogo segue lá.
    """
    assinatura = {"ssdp_st": ["urn:teste:servico:1"]}
    plantar("matriz_dona", _declaracao("matriz_dona", descoberta=assinatura))
    cliente, auth = await abrir()
    assert (await _salvar(cliente, auth, _declaracao())).status == 200
    disputa = _declaracao(descoberta=assinatura)
    resposta = await _salvar(cliente, auth, disputa)
    assert resposta.status == 400
    assert ("descoberta", "decl_descoberta_invalida") in await _problemas(resposta)
    gravado = json.loads((pasta / f"{TIPO}.json").read_text(encoding="utf-8"))
    assert gravado["comandos"]["ligar"]["envia"] == "SET POWER ON"
    assert "descoberta" not in gravado
    drivers = await _drivers(cliente, auth)
    assert drivers[TIPO]["origem"] == "integrador"
    assert drivers[TIPO]["manifesto"]["capacidades"] == ["ligar", "fonte"]
    assert "matriz_dona" in drivers


async def test_um_arquivo_ilegivel_e_consertado_por_um_salvamento(abrir, pasta: Path):
    """The panel is how a broken file gets fixed, so a save over one that not even the loader
    could read has to work instead of answering an error about the file being replaced.

    O painel é como um arquivo quebrado é consertado, então um salvamento sobre um que nem o
    carregador conseguiu ler precisa funcionar em vez de responder erro sobre o arquivo que
    está sendo substituído.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{TIPO}.json").write_bytes(b"\xff\xfe nao sou utf-8")
    cliente, auth = await abrir()
    assert TIPO not in await _drivers(cliente, auth)
    assert (await _salvar(cliente, auth, _declaracao())).status == 200
    assert (await _drivers(cliente, auth))[TIPO]["origem"] == "integrador"


async def test_um_vizinho_quebrado_na_pasta_nao_desfaz_um_salvamento_bom(abrir, pasta: Path):
    """A file somebody dropped in the directory with a shell is not something this save broke,
    and undoing a good save to blame it leaves the panel showing a driver that was never
    written and an error about a file the integrator never touched.

    Um arquivo que alguém largou na pasta por um shell não é algo que este salvamento quebrou,
    e desfazer um salvamento bom para culpá-lo deixa o painel mostrando um driver que nunca foi
    gravado e um erro sobre um arquivo em que o integrador nunca tocou.
    """
    cliente, auth = await abrir()
    pasta.mkdir(parents=True, exist_ok=True)
    vizinho = pasta / "vizinho.json"
    vizinho.write_text("isto nao e json", encoding="utf-8")
    resposta = await _salvar(cliente, auth, _declaracao())
    assert resposta.status == 200, await resposta.text()
    drivers = await _drivers(cliente, auth)
    assert drivers[TIPO]["origem"] == "integrador"
    assert (pasta / f"{TIPO}.json").is_file()
    # Why: the broken file is still broken and still named as such; the save neither fixed it
    # nor was punished for it.
    # Por que: o arquivo quebrado segue quebrado e segue nomeado; o salvamento nem o consertou
    # nem foi punido por ele.
    catalogo = rotas.catalogo_de(cliente.app)
    assert vizinho in {recusado.arquivo for recusado in catalogo.recusados}
    assert vizinho.read_text(encoding="utf-8") == "isto nao e json"


async def test_um_arquivo_ja_recusado_que_o_carregador_recusa_de_novo_nao_responde_ok(
    abrir, plantar, pasta: Path
):
    """A refusal this file already carried is still a refusal of it: answering ok because the
    loader was already refusing that name hands the panel a driver nobody can use, and leaves
    on the disk a file that replaced the one being fixed.

    Uma recusa que este arquivo já carregava segue sendo recusa dele: responder ok porque o
    carregador já recusava aquele nome entrega ao painel um driver que ninguém consegue usar, e
    deixa no disco um arquivo que substituiu o que estava sendo consertado.
    """
    assinatura = {"ssdp_st": ["urn:teste:servico:1"]}
    # Why: the loader hands a disputed signature to the first name in the directory, so the
    # owner is named to sort before the file this test saves, which is the one that must lose.
    # Por que: o carregador entrega uma assinatura disputada ao primeiro nome da pasta, então o
    # dono é nomeado para vir antes do arquivo que este teste salva, que é quem tem de perder.
    plantar("aaa_dona", _declaracao("aaa_dona", descoberta=assinatura))
    plantar(TIPO, {"manifesto": {"tipo": TIPO}})
    arquivo = pasta / f"{TIPO}.json"
    antes = arquivo.read_bytes()
    cliente, auth = await abrir()
    assert TIPO not in await _drivers(cliente, auth)
    resposta = await _salvar(cliente, auth, _declaracao(descoberta=assinatura))
    assert resposta.status == 400, await resposta.text()
    assert ("descoberta", "decl_descoberta_invalida") in await _problemas(resposta)
    assert TIPO not in await _drivers(cliente, auth)
    assert arquivo.read_bytes() == antes
    assert "aaa_dona" in await _drivers(cliente, auth)


# ---------- the fire test never stalls the poll ----------


async def test_a_validacao_do_painel_nao_disputa_o_trabalhador_das_leituras(hub, monkeypatch):
    """Section 7: a file being typed can carry a catastrophic pattern per line, and each one
    kills the worker. On the worker the polls read through, that bill is paid by every device
    on the installation.

    Seção 7: um arquivo sendo digitado pode levar um padrão catastrófico por linha, e cada um
    mata o trabalhador. No trabalhador por onde os polls leem, essa conta é paga por todo
    aparelho da instalação.
    """
    cliente, auth = hub
    leituras: list[int] = []
    monkeypatch.setattr(regex_seguro, "instancia", lambda: leituras.append(1) or _nunca())
    dados = _declaracao(
        estado={
            "pede": [{"envia": "PWR?"}],
            "le": {"ligado": {"regex": "PWR (ON|OFF)", "verdadeiro": "ON"}},
        }
    )
    assert (await _validar(cliente, auth, dados)).status == 200
    assert (await _salvar(cliente, auth, dados)).status == 200
    assert leituras == []


def _nunca() -> regex_seguro.RegexSeguro:
    raise AssertionError("the panel must not judge a file on the worker the polls read through")


async def test_duas_validacoes_ao_mesmo_tempo_nao_tomam_duas_threads_da_piscina(hub, monkeypatch):
    """The pool asyncio.to_thread spends is the one every declarative read goes through, so a
    panel validating on it takes a thread per request from the poll of every device.

    A piscina que o asyncio.to_thread gasta é a mesma por onde passa toda leitura declarativa,
    então um painel validando nela toma uma thread por requisição do poll de todo aparelho.
    """
    cliente, auth = hub
    original = rotas.validar_declaracao
    trava = threading.Lock()
    dentro = 0
    pico = 0

    def demorado(dados, *, regex):
        nonlocal dentro, pico
        with trava:
            dentro += 1
            pico = max(pico, dentro)
        time.sleep(0.05)
        with trava:
            dentro -= 1
        return original(dados, regex=regex)

    monkeypatch.setattr(rotas, "validar_declaracao", demorado)
    respostas = await asyncio.gather(
        *(_validar(cliente, auth, _declaracao(f"tipo_{n}")) for n in range(4))
    )
    assert [resposta.status for resposta in respostas] == [200] * 4
    assert pico == 1


# ---------- delete ----------


async def test_apagar_tira_o_driver_do_catalogo_e_o_arquivo_do_disco(abrir, plantar, pasta: Path):
    plantar(TIPO)
    cliente, auth = await abrir()
    assert TIPO in await _drivers(cliente, auth)
    resposta = await cliente.delete(f"/api/drivers/{TIPO}", headers=auth)
    assert resposta.status == 200
    assert not (pasta / f"{TIPO}.json").exists()
    assert TIPO not in await _drivers(cliente, auth)
    catalogo = (await (await cliente.get("/api/catalogo", headers=auth)).json())["catalogo"]
    assert TIPO not in {item["tipo"] for item in catalogo}


async def test_apagar_um_tipo_que_ninguem_reivindica_responde_nao_encontrado(hub):
    cliente, auth = hub
    resposta = await cliente.delete("/api/drivers/nao_existe", headers=auth)
    assert resposta.status == 404
    assert (await resposta.json())["code"] == "decl_nao_encontrado"


# ---------- the templates ----------


@pytest.mark.parametrize("transporte", list(MODELOS))
async def test_o_modelo_de_cada_transporte_e_um_arquivo_que_o_daemon_aceita(hub, transporte):
    """Nobody starts from a blank box, and a template the daemon refuses would teach the
    format wrong on the very first save.

    Ninguém começa de caixa vazia, e um modelo que o daemon recusa ensinaria o formato errado
    logo no primeiro salvamento.
    """
    cliente, auth = hub
    resposta = await cliente.get(f"/api/drivers/modelo/{transporte}", headers=auth)
    assert resposta.status == 200
    modelo = (await resposta.json())["modelo"]
    assert list(modelo["transporte"]) == [transporte]
    assert set(modelo["manifesto"]["rotulo"]) == {"pt", "en"}
    aceito = await _validar(cliente, auth, modelo)
    assert aceito.status == 200, await aceito.text()


async def test_um_transporte_que_nao_existe_nao_tem_modelo(hub):
    cliente, auth = hub
    resposta = await cliente.get("/api/drivers/modelo/serial", headers=auth)
    assert resposta.status == 404
    assert (await resposta.json())["code"] == "decl_nao_encontrado"


def test_todo_codigo_que_estas_rotas_respondem_o_painel_traduz():
    """Section 11: the API never answers a phrase, so a code the panel does not carry would
    reach the integrator as the code itself.

    Seção 11: a API nunca responde frase, então um código que o painel não carrega chegaria ao
    integrador como o próprio código.
    """
    fonte = (RAIZ / "painel/src/declarativos.ts").read_text(encoding="utf-8")
    lista = re.search(r"CODIGOS_DECLARATIVOS = \[(.*?)\] as const", fonte, re.S)
    assert lista is not None, "the panel no longer declares the vocabulary it translates"
    do_painel = set(re.findall(r'"([a-z_]+)"', lista.group(1)))
    do_daemon = {
        *formato.CODIGOS,
        modulo_catalogo.DECL_ARQUIVO_GRANDE,
        modulo_catalogo.DECL_INVALIDO,
        modulo_catalogo.DECL_JSON_INVALIDO,
        modulo_catalogo.DECL_TIPO_OCUPADO,
        rotas.DECL_EM_USO,
        rotas.DECL_NAO_ENCONTRADO,
    }
    assert do_daemon <= do_painel, sorted(do_daemon - do_painel)
