# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the equipment routes: shape, stable codes, what each one changes, and the
boot that walks the driver catalog before any of them answers.

O contrato das rotas de equipamento: forma, códigos estáveis, o que cada uma muda, e o boot
que percorre o catálogo de drivers antes de qualquer uma responder.
"""

import asyncio
import json
import logging
import os

import pytest
from aiohttp import web

from iphub.__main__ import main
from iphub.api import equipamentos as rotas
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config
from iphub.drivers import catalogo as modulo_catalogo
from iphub.drivers.base import DETALHES, Driver
from iphub.drivers.manifesto import Auth, Campo, Descoberta, Manifesto, TipoCampo
from iphub.drivers.simulado import RespondedorMdns, RespondedorSsdp

TIPO = "exemplo"
ST = "urn:teste-org:device:Exemplo:1"
UUID = "9b1deb3d-3b7d-4bad-9bdd-2b0d7b3dcb6d"

CORPO = {
    "tipo": TIPO,
    "identidade": "uuid-1",
    "nome": "Sala",
    "ip": "192.0.2.10",
    "campos": {"porta": "8080", "senha": "s3gr3d0"},
}

CAMPOS = (Campo("porta", TipoCampo.INTEIRO, padrao="8080"), Campo("senha", TipoCampo.SEGREDO))
SEM_DESCOBERTA = Descoberta()


def _manifesto(
    tipo: str = TIPO,
    *,
    categoria: str = "outro",
    capacidades: tuple[str, ...] = ("ligar", "volume", "fonte"),
    auth: Auth = Auth.NENHUMA,
    config_campos: tuple[Campo, ...] = CAMPOS,
    descoberta: Descoberta = SEM_DESCOBERTA,
) -> Manifesto:
    textos = {
        "descricao": "Exemplo",
        "auth_ajuda": "Ajuda",
        **{f"campo_{campo.nome}": campo.nome for campo in config_campos},
    }
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": "Exemplo", "en": "Example"},
        categoria=categoria,
        capacidades=capacidades,
        auth=auth,
        descoberta=descoberta,
        config_campos=config_campos,
        textos={"pt": dict(textos), "en": dict(textos)},
    )


def _fabrica(manifesto: Manifesto | None = None, **comportamento: object) -> type[Driver]:
    """A driver with knobs, so a test makes it answer exactly what it wants to assert.

    Um driver com botões, para um teste fazê-lo responder exatamente o que quer afirmar.
    """

    class Falso(Driver):
        MANIFESTO = manifesto if manifesto is not None else _manifesto()
        instancias: list["Falso"] = []

        def __init__(self, cadastro) -> None:
            super().__init__(cadastro)
            self.executados: list[tuple[str, object]] = []
            self._defina(online=True, ligado=False, volume=7, fonte="hdmi1", fontes=("hdmi1",))
            type(self).instancias.append(self)

        async def executar(self, acao: str, valor: object = None) -> str | None:
            self.executados.append((acao, valor))
            return comportamento.get("resposta")

        async def autenticar(self) -> str:
            return str(comportamento.get("resultado", "pareado"))

    Falso.instancias = []
    return Falso


@pytest.fixture
def abrir(fabrica_cliente, posse, bearer):
    """A hub with the catalog the test wants, already owned, and its session header.

    Um hub com o catálogo que o teste quer, já com dono, e o cabeçalho de sessão dele.
    """

    async def criar(catalogo: dict, *, equipamentos: tuple[Cadastro, ...] = ()):
        cliente = await fabrica_cliente(catalogo=catalogo, config=Config(equipamentos=equipamentos))
        return cliente, bearer(await posse(cliente))

    return criar


@pytest.fixture
async def hub(abrir):
    """The usual case: one driver in the catalog and nothing registered yet.

    O caso comum: um driver no catálogo e nada cadastrado ainda.
    """
    classe = _fabrica()
    cliente, auth = await abrir({TIPO: classe})
    return cliente, auth, classe


async def _cadastrar(cliente, auth, **mudancas):
    return await cliente.post("/api/equipamentos", json={**CORPO, **mudancas}, headers=auth)


async def _lista(cliente, auth) -> list[dict]:
    resposta = await cliente.get("/api/equipamentos", headers=auth)
    assert resposta.status == 200, await resposta.text()
    return (await resposta.json())["equipamentos"]


def _config_do_disco(amb) -> dict:
    return json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))


async def test_o_catalogo_traz_o_manifesto_nos_dois_idiomas(hub):
    cliente, auth, _ = hub
    resposta = await cliente.get("/api/catalogo", headers=auth)
    assert resposta.status == 200
    (item,) = (await resposta.json())["catalogo"]
    assert item["tipo"] == TIPO
    assert item["capacidades"] == ["ligar", "volume", "fonte"]
    assert set(item["textos"]) == {"pt", "en"}
    assert item["rotulo"] == {"pt": "Exemplo", "en": "Example"}
    assert item["descoberta"] == {"ssdp_st": [], "ssdp_fabricantes": [], "mdns_servicos": []}


async def test_os_enums_viajam_por_valor_em_minusculas(abrir):
    # Why: the panel reads auth and tipo as plain lower case text, so a name like "CODIGO"
    # would silently turn every pairing button off.
    # Por que: o painel lê auth e tipo como texto puro minúsculo, então um nome como "CODIGO"
    # desligaria em silêncio todo botão de pareamento.
    cliente, auth = await abrir({TIPO: _fabrica(_manifesto(auth=Auth.CODIGO))})
    (item,) = (await (await cliente.get("/api/catalogo", headers=auth)).json())["catalogo"]
    assert item["auth"] == "codigo"
    assert [campo["tipo"] for campo in item["config_campos"]] == ["inteiro", "segredo"]
    assert item["config_campos"][0] == {
        "nome": "porta",
        "tipo": "inteiro",
        "obrigatorio": False,
        "padrao": "8080",
    }


async def test_hub_sem_equipamento_responde_lista_vazia(hub):
    cliente, auth, _ = hub
    assert await _lista(cliente, auth) == []


async def test_cadastro_guarda_o_segredo_no_config_e_nunca_o_devolve(hub, amb):
    cliente, auth, classe = hub
    assert (await _cadastrar(cliente, auth)).status == 200
    (equipamento,) = await _lista(cliente, auth)
    assert equipamento["campos"] == {"porta": "8080"}
    assert equipamento["segredos_definidos"] == ["senha"]
    assert "s3gr3d0" not in json.dumps(equipamento)
    assert equipamento["estado"] == {
        "online": True,
        "ligado": False,
        "volume": 7,
        "mudo": None,
        "fonte": "hdmi1",
        "fontes": ["hdmi1"],
        "tocando": None,
        "detalhe": "",
    }
    (guardado,) = _config_do_disco(amb)["equipamentos"]
    assert guardado["segredos"] == {"senha": "s3gr3d0"}
    assert guardado["campos"] == {"porta": "8080"}
    assert len(classe.instancias) == 1


@pytest.mark.parametrize(
    ("mudanca", "esperado", "status"),
    [
        ({"tipo": "nao_existe"}, "tipo_desconhecido", 400),
        ({"tipo": 7}, "tipo_desconhecido", 400),
        ({"ip": "aparelho.local"}, "ip_invalido", 400),
        ({"ip": ""}, "ip_invalido", 400),
        ({"identidade": ""}, "campo_invalido", 400),
        ({"identidade": "a\nb"}, "campo_invalido", 400),
        ({"identidade": 7}, "campo_invalido", 400),
        ({"nome": "a" * 500}, "campo_invalido", 400),
        ({"campos": {"porta": "oitenta"}}, "campo_invalido", 400),
        ({"campos": {"porta": ["8080"]}}, "campo_invalido", 400),
        ({"campos": {"outro": "1"}}, "campo_invalido", 400),
        ({"campos": "porta=8080"}, "campo_invalido", 400),
    ],
)
async def test_cadastro_recusado_com_o_codigo_estavel(hub, mudanca, esperado, status):
    cliente, auth, classe = hub
    resposta = await _cadastrar(cliente, auth, **mudanca)
    assert resposta.status == status
    assert await resposta.json() == {"ok": False, "code": esperado}
    assert await _lista(cliente, auth) == []
    assert classe.instancias == []


async def test_campo_obrigatorio_ausente_e_campo_invalido(abrir):
    campos = (Campo("chave", TipoCampo.TEXTO, obrigatorio=True),)
    cliente, auth = await abrir({TIPO: _fabrica(_manifesto(config_campos=campos))})
    resposta = await _cadastrar(cliente, auth, campos={})
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "campo_invalido"


async def test_corpo_que_nao_e_objeto_json_e_corpo_invalido(hub):
    cliente, auth, _ = hub
    resposta = await cliente.post("/api/equipamentos", data=b"[]", headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "corpo_invalido"


async def test_a_mesma_identidade_duas_vezes_e_409(hub):
    cliente, auth, _ = hub
    assert (await _cadastrar(cliente, auth)).status == 200
    resposta = await _cadastrar(cliente, auth, ip="192.0.2.11")
    assert resposta.status == 409
    assert (await resposta.json())["code"] == "identidade_duplicada"
    assert len(await _lista(cliente, auth)) == 1


async def test_atualizar_sem_o_segredo_mantem_o_guardado(hub, amb):
    cliente, auth, classe = hub
    await _cadastrar(cliente, auth)
    corpo = {**CORPO, "ip": "192.0.2.20", "campos": {"porta": "9090"}}
    resposta = await cliente.post("/api/equipamentos/uuid-1", json=corpo, headers=auth)
    assert resposta.status == 200, await resposta.text()
    (equipamento,) = await _lista(cliente, auth)
    assert (equipamento["ip"], equipamento["campos"]) == ("192.0.2.20", {"porta": "9090"})
    assert equipamento["segredos_definidos"] == ["senha"]
    (guardado,) = _config_do_disco(amb)["equipamentos"]
    assert guardado["segredos"] == {"senha": "s3gr3d0"}
    # Why: the driver read the address when it was born, so an update that only touched
    # config.json would leave the running driver talking to the old one.
    # Por que: o driver leu o endereço quando nasceu, então uma atualização que só mexesse no
    # config.json deixaria o driver vivo falando com o endereço antigo.
    assert len(classe.instancias) == 2
    assert classe.instancias[-1].cadastro.ip == "192.0.2.20"


async def test_atualizar_sem_um_campo_comum_mantem_o_guardado(hub, amb):
    # Why: the port is not a secret, but erasing it on an update that only fixes the name sends
    # the driver back to the default of the manifest, which is not what the operator asked.
    # Por que: a porta não é segredo, mas apagá-la numa atualização que só conserta o nome
    # devolve o driver ao padrão do manifesto, que não é o que o operador pediu.
    cliente, auth, classe = hub
    await _cadastrar(cliente, auth)
    corpo = {"tipo": TIPO, "nome": "Sala de estar", "ip": "192.0.2.10", "campos": {}}
    resposta = await cliente.post("/api/equipamentos/uuid-1", json=corpo, headers=auth)
    assert resposta.status == 200, await resposta.text()
    (equipamento,) = await _lista(cliente, auth)
    assert (equipamento["nome"], equipamento["campos"]) == ("Sala de estar", {"porta": "8080"})
    assert equipamento["segredos_definidos"] == ["senha"]
    assert classe.instancias[-1].cadastro.campos == {"porta": "8080"}
    assert _config_do_disco(amb)["equipamentos"][0]["campos"] == {"porta": "8080"}


async def test_atualizar_com_um_campo_comum_vazio_apaga(hub, amb):
    cliente, auth, _ = hub
    await _cadastrar(cliente, auth)
    corpo = {**CORPO, "campos": {"porta": ""}}
    assert (await cliente.post("/api/equipamentos/uuid-1", json=corpo, headers=auth)).status == 200
    (equipamento,) = await _lista(cliente, auth)
    assert equipamento["campos"] == {}
    assert _config_do_disco(amb)["equipamentos"][0]["campos"] == {}


async def test_atualizar_com_o_segredo_vazio_apaga(hub, amb):
    cliente, auth, _ = hub
    await _cadastrar(cliente, auth)
    corpo = {**CORPO, "campos": {"porta": "8080", "senha": ""}}
    assert (await cliente.post("/api/equipamentos/uuid-1", json=corpo, headers=auth)).status == 200
    (equipamento,) = await _lista(cliente, auth)
    assert equipamento["segredos_definidos"] == []
    assert _config_do_disco(amb)["equipamentos"][0]["segredos"] == {}


async def test_atualizar_nao_troca_a_identidade_e_404_para_quem_nao_existe(hub):
    cliente, auth, _ = hub
    await _cadastrar(cliente, auth)
    trocada = await cliente.post(
        "/api/equipamentos/uuid-1", json={**CORPO, "identidade": "uuid-2"}, headers=auth
    )
    assert trocada.status == 400
    assert (await trocada.json())["code"] == "campo_invalido"
    ausente = await cliente.post("/api/equipamentos/uuid-9", json=CORPO, headers=auth)
    assert ausente.status == 404
    assert (await ausente.json())["code"] == "eq_nao_encontrado"


async def test_remover_apaga_do_config_e_a_segunda_vez_e_404(hub, amb):
    cliente, auth, _ = hub
    await _cadastrar(cliente, auth)
    assert (await cliente.delete("/api/equipamentos/uuid-1", headers=auth)).status == 200
    assert await _lista(cliente, auth) == []
    assert _config_do_disco(amb)["equipamentos"] == []
    repetida = await cliente.delete("/api/equipamentos/uuid-1", headers=auth)
    assert repetida.status == 404
    assert (await repetida.json())["code"] == "eq_nao_encontrado"


async def test_acao_declarada_chega_ao_driver(hub):
    cliente, auth, classe = hub
    await _cadastrar(cliente, auth)
    resposta = await cliente.post(
        "/api/equipamentos/uuid-1/acao", json={"acao": "volume", "valor": 30}, headers=auth
    )
    assert resposta.status == 200
    assert await resposta.json() == {"ok": True, "code": None}
    assert classe.instancias[0].executados == [("volume", 30)]


@pytest.mark.parametrize(
    ("esperado", "status"),
    [
        ("eq_offline", 503),
        ("invalid_value", 400),
        ("auth_pendente", 409),
        ("erro_aparelho", 502),
        ("nao_suportado", 400),
    ],
)
async def test_o_codigo_do_driver_vira_o_status_da_rota(abrir, esperado, status):
    cliente, auth = await abrir({TIPO: _fabrica(resposta=esperado)})
    await _cadastrar(cliente, auth)
    resposta = await cliente.post(
        "/api/equipamentos/uuid-1/acao", json={"acao": "ligar"}, headers=auth
    )
    assert resposta.status == status
    assert await resposta.json() == {"ok": False, "code": esperado}


async def test_acao_em_identidade_que_ninguem_cadastrou_e_404(hub):
    cliente, auth, _ = hub
    resposta = await cliente.post(
        "/api/equipamentos/uuid-9/acao", json={"acao": "ligar"}, headers=auth
    )
    assert resposta.status == 404
    assert (await resposta.json())["code"] == "eq_nao_encontrado"


@pytest.mark.parametrize("corpo", [{"acao": "ligar", "valor": {"a": 1}}, {"acao": "ligar"}])
async def test_valor_que_nao_e_escalar_nao_chega_ao_driver(hub, corpo):
    # Why: a driver writes this value on a socket, so an object must be refused by the route
    # and never handed to code that expects a level, a name or a switch.
    # Por que: um driver escreve este valor num socket, então um objeto tem de ser recusado
    # pela rota e nunca entregue a código que espera nível, nome ou chave.
    cliente, auth, classe = hub
    await _cadastrar(cliente, auth)
    composto = "valor" in corpo
    resposta = await cliente.post("/api/equipamentos/uuid-1/acao", json=corpo, headers=auth)
    assert resposta.status == (400 if composto else 200)
    if composto:
        assert (await resposta.json())["code"] == "invalid_value"
        assert classe.instancias[0].executados == []


async def test_nan_no_valor_e_invalid_value(hub):
    cliente, auth, classe = hub
    await _cadastrar(cliente, auth)
    resposta = await cliente.post(
        "/api/equipamentos/uuid-1/acao",
        data=json.dumps({"acao": "volume", "valor": float("nan")}),
        headers={**auth, "Content-Type": "application/json"},
    )
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "invalid_value"
    assert classe.instancias[0].executados == []


async def test_acao_sem_nome_e_corpo_invalido(hub):
    cliente, auth, _ = hub
    await _cadastrar(cliente, auth)
    resposta = await cliente.post("/api/equipamentos/uuid-1/acao", json={"valor": 1}, headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "corpo_invalido"


@pytest.mark.parametrize("resultado", ["pareado", "aguardando", "falhou"])
async def test_autenticar_devolve_um_dos_tres_resultados(abrir, resultado):
    manifesto = _manifesto(auth=Auth.CODIGO)
    cliente, auth = await abrir({TIPO: _fabrica(manifesto, resultado=resultado)})
    await _cadastrar(cliente, auth)
    resposta = await cliente.post("/api/equipamentos/uuid-1/autenticar", headers=auth)
    assert resposta.status == 200
    assert await resposta.json() == {"ok": True, "code": None, "resultado": resultado}


async def test_autenticar_identidade_desconhecida_e_404(hub):
    cliente, auth, _ = hub
    resposta = await cliente.post("/api/equipamentos/uuid-9/autenticar", headers=auth)
    assert resposta.status == 404
    assert (await resposta.json())["code"] == "eq_nao_encontrado"


async def test_cadastro_de_tipo_que_saiu_da_imagem_sobrevive_offline(abrir):
    # Why: section 6, losing the registration of the integrator because a driver left the
    # image would be worse than reporting it offline with the reason.
    # Por que: seção 6, perder o cadastro do integrador porque um driver saiu da imagem seria
    # pior do que reportá-lo offline com o motivo.
    guardado = Cadastro(identidade="uuid-1", tipo="sumiu", nome="Sala", ip="192.0.2.10")
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(guardado,))
    (equipamento,) = await _lista(cliente, auth)
    assert equipamento["estado"]["online"] is False
    assert equipamento["estado"]["detalhe"] == "tipo_desconhecido"
    assert equipamento["campos"] == {}


async def test_o_detalhe_que_chega_ao_painel_e_sempre_um_codigo(abrir):
    # Why: section 11, the API never answers a phrase, and detalhe is the one field where an
    # English sentence composed by the daemon used to reach the screen untranslated.
    # Por que: seção 11, a API nunca responde frase, e o detalhe é o único campo em que uma
    # frase em inglês composta pelo daemon chegava à tela sem tradução.
    guardado = Cadastro(identidade="uuid-9", tipo="sumiu", nome="Copa", ip="192.0.2.30")
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(guardado,))
    await _cadastrar(cliente, auth)
    detalhes = [equipamento["estado"]["detalhe"] for equipamento in await _lista(cliente, auth)]
    assert len(detalhes) == 2
    assert "tipo_desconhecido" in detalhes
    assert all(detalhe == "" or detalhe in DETALHES for detalhe in detalhes), detalhes


@pytest.fixture
def varredura_curta(monkeypatch):
    """Points the sweep at a responder on loopback and shortens the wait for the answers.

    Aponta a varredura para um respondedor no loopback e encurta a espera pelas respostas.
    """

    def apontar(endereco: tuple[str, int], mdns: tuple[str, int] | None = None) -> None:
        monkeypatch.setattr(rotas, "DESTINO", endereco)
        # Why: the sweep speaks both transports, so a test that pointed only the SSDP one at
        # loopback would send a real mDNS query onto the segment of whoever runs the suite.
        # Por que: a varredura fala os dois transportes, então um teste que apontasse só o
        # SSDP para o loopback mandaria consulta mDNS de verdade para o segmento de quem roda
        # a suíte.
        monkeypatch.setattr(rotas, "DESTINO_MDNS", mdns or endereco)
        monkeypatch.setattr(rotas, "TIMEOUT_VARREDURA_S", 0.4)

    return apontar


async def test_a_varredura_acha_pelo_st_e_marca_o_que_ja_esta_cadastrado(abrir, varredura_curta):
    resposta_ssdp = {"st": ST, "usn": f"uuid:{UUID}::{ST}", "server": "Teste/1.0 Exemplo"}
    async with RespondedorSsdp((resposta_ssdp,)) as servidor:
        varredura_curta(servidor.endereco)
        classe = _fabrica(_manifesto(descoberta=Descoberta(ssdp_st=(ST,))))
        cliente, auth = await abrir({TIPO: classe})
        (achado,) = (await (await cliente.post("/api/descoberta", headers=auth)).json())["achados"]
        assert achado["tipo"] == TIPO
        assert achado["identidade"] == UUID
        assert achado["ip"] == "127.0.0.1"
        assert achado["ja_cadastrado"] is False
        await _cadastrar(cliente, auth, identidade=UUID)
        (achado,) = (await (await cliente.post("/api/descoberta", headers=auth)).json())["achados"]
        assert achado["ja_cadastrado"] is True


async def test_o_que_nenhum_manifesto_reivindica_vem_com_texto_vazio(abrir, varredura_curta):
    # Why: the panel reads tipo and identidade as text, so a null there would print the word
    # null in the list of what the sweep found.
    # Por que: o painel lê tipo e identidade como texto, então um null ali imprimiria a
    # palavra null na lista do que a varredura achou.
    resposta_ssdp = {"st": "urn:outro:coisa:1", "usn": "sem-uuid", "server": "Estranho/1.0"}
    async with RespondedorSsdp((resposta_ssdp,)) as servidor:
        varredura_curta(servidor.endereco)
        classe = _fabrica(_manifesto(descoberta=Descoberta(ssdp_fabricantes=("ninguem",))))
        cliente, auth = await abrir({TIPO: classe})
        (achado,) = (await (await cliente.post("/api/descoberta", headers=auth)).json())["achados"]
        assert achado["tipo"] == ""
        assert achado["identidade"] == ""
        assert achado["porta"] is None


async def test_dois_pedidos_ao_mesmo_tempo_fazem_uma_varredura_so(abrir, varredura_curta):
    resposta_ssdp = {"st": ST, "usn": f"uuid:{UUID}::{ST}", "server": "Teste/1.0"}
    async with RespondedorSsdp((resposta_ssdp,)) as servidor:
        varredura_curta(servidor.endereco)
        classe = _fabrica(_manifesto(descoberta=Descoberta(ssdp_st=(ST,))))
        cliente, auth = await abrir({TIPO: classe})
        respostas = await asyncio.gather(
            cliente.post("/api/descoberta", headers=auth),
            cliente.post("/api/descoberta", headers=auth),
        )
        assert [r.status for r in respostas] == [200, 200]
        assert len(servidor.pedidos) == 1, servidor.pedidos
        for resposta in respostas:
            assert len((await resposta.json())["achados"]) == 1


async def test_plano_sem_assinatura_nao_manda_datagrama(abrir, varredura_curta):
    async with RespondedorSsdp(({"st": ST, "usn": "uuid:x", "server": "Teste"},)) as servidor:
        varredura_curta(servidor.endereco)
        cliente, auth = await abrir({TIPO: _fabrica()})
        resposta = await cliente.post("/api/descoberta", headers=auth)
        assert resposta.status == 200
        assert (await resposta.json())["achados"] == []
        assert servidor.pedidos == []


@pytest.fixture
def sem_escrita(amb):
    """A data directory nothing can write into, which is what a full or read only volume is.

    Um diretório de dados em que nada consegue escrever, que é o que um volume cheio é.
    """
    if os.getuid() == 0:
        pytest.skip("root writes into a read only directory anyway")
    amb.dir_data.chmod(0o500)
    yield
    amb.dir_data.chmod(0o700)


async def test_falha_ao_gravar_nao_deixa_o_gestor_a_frente_do_disco(hub, amb, sem_escrita):
    # Why: a registration the daemon polls but the file does not carry vanishes on the next
    # restart, and until then the panel shows an equipment nobody can find again.
    # Por que: um cadastro que o daemon faz poll mas o arquivo não carrega some no próximo
    # reinício, e até lá o painel mostra um equipamento que ninguém acha de novo.
    cliente, auth, classe = hub
    resposta = await _cadastrar(cliente, auth)
    assert resposta.status == 500
    assert await resposta.json() == {"ok": False, "code": "erro_interno"}
    assert await _lista(cliente, auth) == []
    assert _config_do_disco(amb)["equipamentos"] == []
    assert classe.instancias == []


async def test_falha_ao_gravar_na_remocao_mantem_o_equipamento(hub, amb):
    cliente, auth, _ = hub
    await _cadastrar(cliente, auth)
    amb.dir_data.chmod(0o500)
    try:
        if os.getuid() == 0:
            pytest.skip("root writes into a read only directory anyway")
        resposta = await cliente.delete("/api/equipamentos/uuid-1", headers=auth)
        assert resposta.status == 500
        assert (await resposta.json())["code"] == "erro_interno"
        assert len(await _lista(cliente, auth)) == 1
        assert len(_config_do_disco(amb)["equipamentos"]) == 1
    finally:
        amb.dir_data.chmod(0o700)


def test_um_catalogo_que_estoura_no_boot_recusa_sem_traceback(amb, monkeypatch, caplog):
    # Why: the boot walks the driver catalog now, so a module that raises anything at import
    # printed a traceback in the container log where the integrator needs the refusal line.
    # Por que: o boot percorre o catálogo de drivers agora, então um módulo que levanta
    # qualquer coisa no import imprimia traceback no log do container onde o integrador
    # precisa da linha da recusa.
    monkeypatch.setenv("IPHUB_DATA", str(amb.dir_data))
    monkeypatch.setenv("IPHUB_PAINEL", str(amb.dir_painel))
    monkeypatch.setenv("IPHUB_BIND", amb.bind)
    monkeypatch.setenv("IPHUB_PORTA", str(amb.porta))

    def estourar(pacote: object) -> dict:
        raise RuntimeError("driver module blew up on import")

    # Why: the boot builds the catalog, and walking the native package is where a module of a
    # driver is imported, so this is the very call that raises when one of them is broken.
    # Por que: o boot monta o catálogo, e varrer o pacote nativo é onde um módulo de driver é
    # importado, então esta é justamente a chamada que estoura quando um deles está quebrado.
    monkeypatch.setattr(modulo_catalogo, "carregar_pacote", estourar)
    monkeypatch.setattr(web, "run_app", lambda *args, **campos: pytest.fail("it served anyway"))
    caplog.set_level(logging.ERROR, logger="iphub")
    assert main() == 1
    assert "refusing to boot" in caplog.text
    assert "driver module blew up on import" in caplog.text
    assert all(registro.exc_info is None for registro in caplog.records)


async def test_a_varredura_acha_o_aparelho_que_so_responde_mdns(abrir, varredura_curta):
    """Section 6: discovery is generated from the manifests, on the transport each one
    declares, and the multiroom speaker of section 14 declares only mDNS.

    Why: sweeping SSDP alone answered "nothing here" on a segment full of speakers, and the
    whole shipped catalogue declares not one SSDP signature, so the panel could never find a
    single device.

    Seção 6: a descoberta é gerada dos manifestos, no transporte que cada um declara, e a
    caixa multiroom da seção 14 declara só mDNS.

    Por que: varrer só SSDP respondia "não há nada aqui" num segmento cheio de caixas, e o
    catálogo inteiro que embarca não declara uma assinatura SSDP sequer, então o painel nunca
    conseguiria achar aparelho nenhum.
    """
    servico = "_linkplay._tcp"
    entrada = {
        "servico": servico,
        "instancia": f"Caixa.{servico}.local",
        "host": "caixa.local",
        "ip": "127.0.0.1",
        "porta": 80,
    }
    async with RespondedorMdns((entrada,)) as servidor:
        varredura_curta(servidor.endereco)
        classe = _fabrica(_manifesto(descoberta=Descoberta(mdns_servicos=(servico,))))
        cliente, auth = await abrir({TIPO: classe})
        corpo = await (await cliente.post("/api/descoberta", headers=auth)).json()
    (achado,) = corpo["achados"]
    assert achado["tipo"] == TIPO
    assert achado["ip"] == "127.0.0.1"


async def test_um_transporte_que_falha_nao_apaga_o_que_o_outro_achou(abrir, varredura_curta):
    """One transport failing on this host must not hide the devices the other one found.

    Um transporte falhando neste host não pode esconder os aparelhos que o outro achou.
    """
    resposta_ssdp = {"st": ST, "usn": f"uuid:{UUID}::{ST}", "server": "Teste/1.0"}
    async with RespondedorSsdp((resposta_ssdp,)) as servidor:
        # An mDNS destination with no port at all: the socket raises instead of answering.
        # Um destino mDNS sem porta alguma: o socket estoura em vez de responder.
        varredura_curta(servidor.endereco, mdns=("127.0.0.1", 0))
        classe = _fabrica(
            _manifesto(descoberta=Descoberta(ssdp_st=(ST,), mdns_servicos=("_x._tcp",)))
        )
        cliente, auth = await abrir({TIPO: classe})
        resposta = await cliente.post("/api/descoberta", headers=auth)
        assert resposta.status == 200, await resposta.text()
        (achado,) = (await resposta.json())["achados"]
    assert achado["identidade"] == UUID


async def test_os_dois_transportes_falhando_respondem_erro_interno(abrir, varredura_curta):
    """Both transports failing is a fault of this host, and it is reported as one.

    Why: answering an empty list there would send the integrator hunting the network instead
    of the daemon, which is the whole reason the sweep answers a stable code.

    Os dois transportes falhando é falha deste host, e é reportada como tal.

    Por que: responder lista vazia ali mandaria o integrador caçar a rede em vez do daemon,
    que é a razão inteira de a varredura responder um código estável.
    """
    # A destination with no port at all: the socket raises instead of answering.
    # Um destino sem porta alguma: o socket estoura em vez de responder.
    varredura_curta(("127.0.0.1", 0))
    classe = _fabrica(_manifesto(descoberta=Descoberta(ssdp_st=(ST,), mdns_servicos=("_x._tcp",))))
    cliente, auth = await abrir({TIPO: classe})
    resposta = await cliente.post("/api/descoberta", headers=auth)
    assert resposta.status == 500
    assert await resposta.json() == {"ok": False, "code": "erro_interno"}


async def test_trocar_o_tipo_para_um_que_nao_e_multiroom_esvazia_o_bloco_de_zona(abrir):
    """Section 6: a zone is a multiroom equipment occupying a block, so an equipment that
    stops being multiroom cannot stay in one.

    Why: the block would keep publishing a zone whose device refuses every data point of
    section 8, and nothing anywhere would say why.

    Seção 6: uma zona é um equipamento multiroom ocupando um bloco, então um equipamento que
    deixa de ser multiroom não pode ficar num.

    Por que: o bloco seguiria publicando uma zona cujo aparelho recusa todo data point da
    seção 8, e nada em lugar nenhum diria por quê.
    """
    outro = "projetor_falso"
    caixa = _fabrica(_manifesto(categoria="multiroom", capacidades=("volume", "agrupar")))
    projetor = _fabrica(_manifesto(outro, categoria="projetor"))
    cliente, auth = await abrir({TIPO: caixa, outro: projetor})
    assert (await _cadastrar(cliente, auth)).status == 200
    resposta = await cliente.post("/api/zonas", json={"zonas": ["uuid-1"]}, headers=auth)
    assert resposta.status == 200, await resposta.text()

    resposta = await cliente.post(
        "/api/equipamentos/uuid-1", json={**CORPO, "tipo": outro}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    corpo = await (await cliente.get("/api/zonas", headers=auth)).json()
    assert corpo["zonas"][0]["identidade"] == ""
