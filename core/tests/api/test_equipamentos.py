# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the equipment routes: shape, stable codes, what each one changes, and the
boot that walks the driver catalog before any of them answers.
"""

import asyncio
import json
import logging
import os

import pytest

from iphub import __main__ as modulo_principal
from iphub import cofre
from iphub.__main__ import main
from iphub.api import equipamentos as rotas
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config
from iphub.drivers import catalogo as modulo_catalogo
from iphub.drivers.base import DETALHES, Aparelho, Driver
from iphub.drivers.descoberta import Achado
from iphub.drivers.manifesto import Auth, Campo, Descoberta, Manifesto, Sugestao, TipoCampo
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
    sugestoes: tuple[Sugestao, ...] = (),
    lista_da_conta: str = "",
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
        lista_da_conta=lista_da_conta,
        config_campos=config_campos,
        textos={"pt": dict(textos), "en": dict(textos)},
        sugestoes=sugestoes,
    )


def _fabrica(manifesto: Manifesto | None = None, **comportamento: object) -> type[Driver]:
    """A driver with knobs, so a test makes it answer exactly what it wants to assert."""

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

        def credenciais_do_aparelho(self) -> dict[str, str]:
            achadas = comportamento.get("credenciais")
            return dict(achadas) if isinstance(achadas, dict) else {}

        async def aparelhos_da_conta(self):
            erro = comportamento.get("conta_estoura")
            if erro is not None:
                raise OSError(str(erro))
            return tuple(comportamento.get("da_conta", ()))

    Falso.instancias = []
    return Falso


@pytest.fixture
def abrir(fabrica_cliente, posse, bearer):
    """A hub with the catalog the test wants, already owned, and its session header."""

    async def criar(catalogo: dict, *, equipamentos: tuple[Cadastro, ...] = ()):
        cliente = await fabrica_cliente(catalogo=catalogo, config=Config(equipamentos=equipamentos))
        return cliente, bearer(await posse(cliente))

    return criar


@pytest.fixture
async def hub(abrir):
    """The usual case: one driver in the catalog and nothing registered yet."""
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
    """The file as written, with the credentials of each equipment opened by the vault."""
    dados = json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))
    chaveiro = cofre.abrir(amb.dir_data)
    for equipamento in dados.get("equipamentos", []):
        segredos = equipamento.get("segredos", {})
        for nome, valor in segredos.items():
            assert not valor or cofre.cifrado(valor), f"{nome} written in clear"
            segredos[nome] = chaveiro.decifrar(valor)
    return dados


def test_duas_visoes_de_um_endereco_viram_uma_linha_so():
    """A speaker announces itself by multicast without naming a driver and answers its own
    port naming one, and the panel must show it once, with what each sighting saw."""
    anuncio = Achado(
        tipo="",
        identidade="",
        ip="192.0.2.10",
        porta=None,
        descricao="Linux/2.6",
        nome="sala.local",
    )
    driver = Achado(
        tipo=TIPO, identidade="uuid-1", ip="192.0.2.10", porta=None, descricao="Sala", nome=""
    )
    (linha,) = rotas._sem_repetir((anuncio, driver))
    assert (linha.tipo, linha.identidade) == (TIPO, "uuid-1")
    assert (linha.descricao, linha.nome) == ("Sala", "sala.local")
    mudou = Achado(
        tipo=TIPO, identidade="uuid-1", ip="192.0.2.11", porta=None, descricao="", nome=""
    )
    assert [achado.ip for achado in rotas._sem_repetir((driver, mudou))] == ["192.0.2.10"]


def test_o_nome_que_o_aparelho_deu_a_si_vence_a_lista_de_portas():
    """A probe writes the ports when nobody named the device, and that is the last resort."""
    sondado = Achado(
        tipo="", identidade="", ip="192.0.2.10", porta=None, descricao="TCP 80, 443", nome=""
    )
    nomeado = Achado(
        tipo=TIPO, identidade="uuid-1", ip="192.0.2.10", porta=None, descricao="Sala", nome=""
    )
    (linha,) = rotas._sem_repetir((sondado, nomeado))
    assert linha.descricao == "Sala"
    (invertida,) = rotas._sem_repetir((nomeado, sondado))
    assert invertida.descricao == "Sala"
    so_sonda = Achado(
        tipo=TIPO, identidade="uuid-1", ip="192.0.2.10", porta=None, descricao="", nome=""
    )
    (unica,) = rotas._sem_repetir((sondado, so_sonda))
    assert unica.descricao == "TCP 80, 443"


async def test_duas_buscas_da_mesma_faixa_viram_uma_varredura_so(abrir, monkeypatch):
    """A panel that gave up waiting and asked again must not put a second sweep of the same
    range on top of the first: on a small board that doubled the load that made it give up."""
    chamadas = []

    async def devagar(rede, classes, **resto):
        chamadas.append(str(rede))
        await asyncio.sleep(0.2)
        return ()

    monkeypatch.setattr(rotas.modulo_varredura, "procurar", devagar)
    monkeypatch.setattr(rotas, "TIMEOUT_VARREDURA_S", 0.05)
    monkeypatch.setattr(rotas, "LIMITE_VARREDURA_S", 0.1)
    cliente, auth = await abrir({TIPO: _fabrica()})
    corpo = {"faixa": "192.0.2.0/30"}
    respostas = await asyncio.gather(
        *(cliente.post("/api/descoberta", json=corpo, headers=auth) for _ in range(3))
    )
    assert [resposta.status for resposta in respostas] == [200, 200, 200]
    assert chamadas == ["192.0.2.0/30"]


async def test_a_rede_do_host_vem_como_faixas_para_o_painel(hub, monkeypatch):
    from iphub import rede

    monkeypatch.setattr(
        rede, "enderecos_do_host", lambda: ("192.168.65.3", "192.0.2.10", "10.1.2.3")
    )
    cliente, auth, _ = hub
    resposta = await cliente.get("/api/rede", headers=auth)
    assert resposta.status == 200
    assert (await resposta.json())["faixas"] == ["10.1.2.0/24"]
    assert (await cliente.get("/api/rede")).status == 401


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
    # The panel reads auth and tipo as plain lower case text, so a name like "CODIGO"
    # would silently turn every pairing button off.
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
        "modos": [],
        "atalhos": [],
        "reproduzindo": None,
        "tocando": None,
        "temperatura": None,
        "modo": None,
        "vento": None,
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
    # The driver read the address when it was born, so an update that only touched
    # config.json would leave the running driver talking to the old one.
    assert len(classe.instancias) == 2
    assert classe.instancias[-1].cadastro.ip == "192.0.2.20"


async def test_atualizar_sem_um_campo_comum_mantem_o_guardado(hub, amb):
    # The port is not a secret, but erasing it on an update that only fixes the name sends
    # the driver back to the default of the manifest, which is not what the operator asked.
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
    # A driver writes this value on a socket, so an object must be refused by the route
    # and never handed to code that expects a level, a name or a switch.
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
    # Losing the registration of the integrator because a driver left the
    # image would be worse than reporting it offline with the reason.
    guardado = Cadastro(identidade="uuid-1", tipo="sumiu", nome="Sala", ip="192.0.2.10")
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(guardado,))
    (equipamento,) = await _lista(cliente, auth)
    assert equipamento["estado"]["online"] is False
    assert equipamento["estado"]["detalhe"] == "tipo_desconhecido"
    assert equipamento["campos"] == {}


async def test_o_detalhe_que_chega_ao_painel_e_sempre_um_codigo(abrir):
    # The API never answers a phrase, and detalhe is the one field where an
    # English sentence composed by the daemon used to reach the screen untranslated.
    guardado = Cadastro(identidade="uuid-9", tipo="sumiu", nome="Copa", ip="192.0.2.30")
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(guardado,))
    await _cadastrar(cliente, auth)
    detalhes = [equipamento["estado"]["detalhe"] for equipamento in await _lista(cliente, auth)]
    assert len(detalhes) == 2
    assert "tipo_desconhecido" in detalhes
    assert all(detalhe == "" or detalhe in DETALHES for detalhe in detalhes), detalhes


@pytest.fixture
def varredura_curta(monkeypatch):
    """Points the sweep at a responder on loopback and shortens the wait for the answers."""

    def apontar(endereco: tuple[str, int], mdns: tuple[str, int] | None = None) -> None:
        monkeypatch.setattr(rotas, "DESTINO", endereco)
        # The sweep speaks both transports, so a test that pointed only the SSDP one at
        # loopback would send a real mDNS query onto the segment of whoever runs the suite.
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


async def test_o_mesmo_tipo_no_mesmo_endereco_ja_esta_cadastrado_seja_qual_for_o_nome(
    abrir, varredura_curta
):
    """Measured on 7/set/2026: a television registered by hand with its MAC as identity
    answered the sweep with its uuid, and the button offered to register it again. The same
    type at the address the answer came from is that device; the same type elsewhere is not.
    """
    resposta_ssdp = {"st": ST, "usn": f"uuid:{UUID}::{ST}", "server": "Teste/1.0 Exemplo"}
    async with RespondedorSsdp((resposta_ssdp,)) as servidor:
        varredura_curta(servidor.endereco)
        classe = _fabrica(_manifesto(descoberta=Descoberta(ssdp_st=(ST,))))
        cliente, auth = await abrir({TIPO: classe})
        await _cadastrar(cliente, auth, identidade="f8b95ae6aacc", ip="192.0.2.9")
        (achado,) = (await (await cliente.post("/api/descoberta", headers=auth)).json())["achados"]
        assert achado["identidade"] == UUID
        assert achado["ja_cadastrado"] is False
        await _cadastrar(cliente, auth, identidade="a1b2c3d4e5f6", ip="127.0.0.1")
        (achado,) = (await (await cliente.post("/api/descoberta", headers=auth)).json())["achados"]
        assert achado["ja_cadastrado"] is True


async def test_o_que_nenhum_manifesto_reivindica_vem_com_texto_vazio(abrir, varredura_curta):
    # The panel reads tipo and identidade as text, so a null there would print the word
    # null in the list of what the sweep found.
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
    """A data directory nothing can write into, which is what a full or read only volume is."""
    if os.getuid() == 0:
        pytest.skip("root writes into a read only directory anyway")
    amb.dir_data.chmod(0o500)
    yield
    amb.dir_data.chmod(0o700)


async def test_falha_ao_gravar_nao_deixa_o_gestor_a_frente_do_disco(hub, amb, sem_escrita):
    # A registration the daemon polls but the file does not carry vanishes on the next
    # restart, and until then the panel shows an equipment nobody can find again.
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
    # The boot walks the driver catalog now, so a module that raises anything at import
    # printed a traceback in the container log where the integrator needs the refusal line.
    monkeypatch.setenv("IPHUB_DATA", str(amb.dir_data))
    monkeypatch.setenv("IPHUB_PAINEL", str(amb.dir_painel))
    monkeypatch.setenv("IPHUB_BIND", amb.bind)
    monkeypatch.setenv("IPHUB_PORTA", str(amb.porta))

    def estourar(pacote: object) -> dict:
        raise RuntimeError("driver module blew up on import")

    # The boot builds the catalog, and walking the native package is where a module of a
    # driver is imported, so this is the very call that raises when one of them is broken.
    monkeypatch.setattr(modulo_catalogo, "carregar_pacote", estourar)
    monkeypatch.setattr(
        modulo_principal, "servir_ate_o_fim", lambda *args: pytest.fail("it served anyway")
    )
    caplog.set_level(logging.ERROR, logger="iphub")
    assert main() == 1
    assert "refusing to boot" in caplog.text
    assert "driver module blew up on import" in caplog.text
    assert all(registro.exc_info is None for registro in caplog.records)


async def test_a_varredura_acha_o_aparelho_que_so_responde_mdns(abrir, varredura_curta):
    """Discovery is generated from the manifests, on the transport each one
    declares, and the multiroom speaker declares only mDNS.

    Sweeping SSDP alone answered "nothing here" on a segment full of speakers, and the
    whole shipped catalogue declares not one SSDP signature, so the panel could never find a
    single device.
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
    """One transport failing on this host must not hide the devices the other one found."""
    resposta_ssdp = {"st": ST, "usn": f"uuid:{UUID}::{ST}", "server": "Teste/1.0"}
    async with RespondedorSsdp((resposta_ssdp,)) as servidor:
        # An mDNS destination with no port at all: the socket raises instead of answering.
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

    Answering an empty list there would send the integrator hunting the network instead
    of the daemon, which is the whole reason the sweep answers a stable code.
    """
    # A destination with no port at all: the socket raises instead of answering.
    varredura_curta(("127.0.0.1", 0))
    classe = _fabrica(_manifesto(descoberta=Descoberta(ssdp_st=(ST,), mdns_servicos=("_x._tcp",))))
    cliente, auth = await abrir({TIPO: classe})
    resposta = await cliente.post("/api/descoberta", headers=auth)
    assert resposta.status == 500
    assert await resposta.json() == {"ok": False, "code": "erro_interno"}


async def test_trocar_o_tipo_para_um_que_nao_e_multiroom_mantem_o_numero(abrir):
    """Any registered equipment of the product may occupy a number, so an
    equipment that stops being multiroom keeps its number on the app; the data points follow
    the new manifest.
    """
    outro = "projetor_falso"
    caixa = _fabrica(_manifesto(categoria="multiroom", capacidades=("volume", "agrupar")))
    projetor = _fabrica(_manifesto(outro, categoria="projetor"))
    cliente, auth = await abrir({TIPO: caixa, outro: projetor})
    assert (await _cadastrar(cliente, auth)).status == 200
    resposta = await cliente.post("/api/licencas", json={"produto": "av"}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    licenca = (await resposta.json())["licenca"]["id"]
    resposta = await cliente.post(
        f"/api/licencas/{licenca}/numeros", json={"numeros": ["uuid-1"]}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()

    resposta = await cliente.post(
        "/api/equipamentos/uuid-1", json={**CORPO, "tipo": outro}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    corpo = await (await cliente.get("/api/licencas", headers=auth)).json()
    assert corpo["licencas"][0]["numeros"][0]["identidade"] == "uuid-1"
    (equipamento,) = await _lista(cliente, auth)
    assert (equipamento["licenca"], equipamento["numero"]) == (licenca, 1)


async def test_a_varredura_pergunta_o_uuid_a_caixa_que_o_mdns_achou(
    abrir, varredura_curta, monkeypatch
):
    """Registers the uuid, and the mDNS answer of a speaker carries only its name
    and its address; the sweep asks the speaker who it is, so the finding registers itself.

    Without this the sweep found the speaker and the panel still asked the operator to
    type the identity by hand, which is the one thing discovery exists to spare them.
    """
    from iphub.drivers.nativos import linkplay
    from iphub.drivers.simulado import ServidorHttp

    uuid = "FF31F09E1A5020554E1CD9F1"
    rotas = {
        "/httpapi.asp?command=getStatusEx": (200, json.dumps({"uuid": uuid, "plm_support": "0x6"}))
    }
    servico = "_linkplay._tcp"
    entrada = {
        "servico": servico,
        "instancia": f"Sala.{servico}.local",
        "host": "sala.local",
        "ip": "127.0.0.1",
        "porta": 80,
    }
    async with ServidorHttp(rotas) as caixa, RespondedorMdns((entrada,)) as servidor:
        monkeypatch.setattr(linkplay, "PORTA_HTTP", caixa.endereco[1])
        varredura_curta(servidor.endereco)
        cliente, auth = await abrir({linkplay.LinkPlay.MANIFESTO.tipo: linkplay.LinkPlay})
        corpo = await (await cliente.post("/api/descoberta", headers=auth)).json()
    (achado,) = corpo["achados"]
    assert achado["tipo"] == linkplay.LinkPlay.MANIFESTO.tipo
    assert achado["identidade"] == uuid
    assert achado["ip"] == "127.0.0.1"


async def test_uma_caixa_que_nao_responde_o_uuid_ainda_e_um_achado(
    abrir, varredura_curta, monkeypatch
):
    """A speaker that answers mDNS and not HTTP is still on the network; the finding stays,
    without identity, and the panel says the identity has to be typed.
    """
    from iphub.drivers.nativos import linkplay

    servico = "_linkplay._tcp"
    entrada = {
        "servico": servico,
        "instancia": f"Sala.{servico}.local",
        "host": "sala.local",
        "ip": "127.0.0.1",
        "porta": 80,
    }
    async with RespondedorMdns((entrada,)) as servidor:
        # A port nobody listens on: the identification fails fast and the sweep goes on.
        monkeypatch.setattr(linkplay, "PORTA_HTTP", 1)
        varredura_curta(servidor.endereco)
        cliente, auth = await abrir({linkplay.LinkPlay.MANIFESTO.tipo: linkplay.LinkPlay})
        corpo = await (await cliente.post("/api/descoberta", headers=auth)).json()
    (achado,) = corpo["achados"]
    assert achado["identidade"] == ""
    assert achado["ip"] == "127.0.0.1"


SUGESTOES = (
    Sugestao("atalhos", "Radio 1", "http://10.0.0.2/radio1"),
    Sugestao("atalhos", "Radio 2", "http://10.0.0.2/radio2"),
    Sugestao("atalhos", "Preset 1", "preset:1"),
)


async def test_um_cadastro_novo_nasce_com_os_atalhos_que_o_driver_sugere(abrir, amb):
    """The value of a shortcut is a string of the protocol of the device, so an
    equipment that arrived with an empty list left the integrator guessing; what the driver
    suggests fills the list of the registration, and reaches the file.
    """
    manifesto = _manifesto(capacidades=("ligar", "volume", "atalho"), sugestoes=SUGESTOES)
    cliente, auth = await abrir({TIPO: _fabrica(manifesto)})
    assert (await _cadastrar(cliente, auth)).status == 200
    (equipamento,) = await _lista(cliente, auth)
    assert equipamento["listas"]["atalhos"] == [
        {"rotulo": "Radio 1", "valor": "http://10.0.0.2/radio1"},
        {"rotulo": "Radio 2", "valor": "http://10.0.0.2/radio2"},
        {"rotulo": "Preset 1", "valor": "preset:1"},
    ]
    guardado = _config_do_disco(amb)["equipamentos"][0]["listas"]["atalhos"]
    assert [item["valor"] for item in guardado] == [
        "http://10.0.0.2/radio1",
        "http://10.0.0.2/radio2",
        "preset:1",
    ]
    # An update that sends an empty object is someone clearing the list on purpose, and a
    # suggestion that came back would be a list the integrator cannot empty.
    resposta = await cliente.post(
        f"/api/equipamentos/{CORPO['identidade']}", json={**CORPO, "listas": {}}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    (equipamento,) = await _lista(cliente, auth)
    assert equipamento["listas"] == {}


async def test_o_corpo_do_cadastro_vence_a_sugestao_do_driver(abrir):
    """A body that carries lists is the integrator saying what he wants, and nothing is added
    to it.
    """
    manifesto = _manifesto(capacidades=("ligar", "volume", "atalho"), sugestoes=SUGESTOES)
    cliente, auth = await abrir({TIPO: _fabrica(manifesto)})
    listas = {"atalhos": [{"rotulo": "Minha radio", "valor": "http://10.0.0.9/x"}]}
    assert (await _cadastrar(cliente, auth, listas=listas)).status == 200
    (equipamento,) = await _lista(cliente, auth)
    assert equipamento["listas"] == listas


async def test_o_catalogo_leva_as_sugestoes_do_manifesto(abrir):
    """The panel offers the examples again after someone deletes them, so it reads them from
    the manifest like everything else it knows about a driver.
    """
    manifesto = _manifesto(capacidades=("ligar", "volume", "atalho"), sugestoes=SUGESTOES)
    cliente, auth = await abrir({TIPO: _fabrica(manifesto)})
    (item,) = (await (await cliente.get("/api/catalogo", headers=auth)).json())["catalogo"]
    assert item["sugestoes"] == [
        {"lista": "atalhos", "rotulo": "Radio 1", "valor": "http://10.0.0.2/radio1"},
        {"lista": "atalhos", "rotulo": "Radio 2", "valor": "http://10.0.0.2/radio2"},
        {"lista": "atalhos", "rotulo": "Preset 1", "valor": "preset:1"},
    ]


async def test_um_driver_sem_sugestao_cadastra_com_a_lista_vazia(hub):
    cliente, auth, _ = hub
    assert (await _cadastrar(cliente, auth)).status == 200
    (equipamento,) = await _lista(cliente, auth)
    assert equipamento["listas"] == {}


async def test_um_driver_de_nuvem_cadastra_sem_endereco_e_recusa_um(fabrica_cliente, posse, bearer):
    """A device with no local API is reached through the cloud of its maker, so its
    registration carries no address; an address on one is somebody expecting the hub to dial
    it, and it never will.
    """
    from iphub.drivers.nativos.lg_thinq import LgThinq

    cliente = await fabrica_cliente(catalogo={LgThinq.MANIFESTO.tipo: LgThinq})
    auth = bearer(await posse(cliente))
    corpo = {
        "tipo": LgThinq.MANIFESTO.tipo,
        "identidade": "ar-da-sala",
        "nome": "Ar da sala",
        "campos": {"pais": "BR", "token": "pat-de-teste-1234567890", "dispositivo": "abc123def456"},
    }
    resposta = await cliente.post("/api/equipamentos", json=corpo, headers=auth)
    assert resposta.status == 200, await resposta.text()
    listagem = await (await cliente.get("/api/equipamentos", headers=auth)).json()
    cadastrado = listagem["equipamentos"][0]
    assert cadastrado["ip"] == ""
    # The token is a secret of the registration and never comes back.
    assert "token" not in cadastrado["campos"]
    assert cadastrado["segredos_definidos"] == ["token"]
    resposta = await cliente.post(
        "/api/equipamentos", json={**corpo, "identidade": "outro", "ip": "192.0.2.9"}, headers=auth
    )
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "ip_invalido"


async def test_o_catalogo_diz_quais_drivers_sao_de_nuvem(fabrica_cliente, posse, bearer):
    """The panel draws the form from the manifest, so it reads there that this one asks for a
    credential and not for an address.
    """
    from iphub.drivers.nativos.lg_thinq import LgThinq

    cliente = await fabrica_cliente(catalogo={LgThinq.MANIFESTO.tipo: LgThinq})
    auth = bearer(await posse(cliente))
    corpo = await (await cliente.get("/api/catalogo", headers=auth)).json()
    item = next(linha for linha in corpo["catalogo"] if linha["tipo"] == LgThinq.MANIFESTO.tipo)
    assert item["nuvem"] is True
    assert item["auth"] == "chave"


async def test_o_pareamento_grava_a_credencial_que_o_aparelho_entregou(abrir, amb):
    """A pairing that lives in the memory of the driver dies with the daemon.

    Without this the person walks to the television again on every deploy, which is what
    an LG did on the bench: it paired, it obeyed, and the config file had nothing in it.
    """
    manifesto = _manifesto(
        auth=Auth.POPUP_NO_APARELHO,
        config_campos=(*CAMPOS, Campo("chave_cliente", TipoCampo.TEXTO)),
    )
    classe = _fabrica(manifesto, credenciais={"chave_cliente": "a-chave-da-tv"})
    cliente, auth = await abrir({TIPO: classe})
    assert (await _cadastrar(cliente, auth)).status == 200
    resposta = await cliente.post("/api/equipamentos/uuid-1/autenticar", headers=auth)
    assert (await resposta.json())["resultado"] == "pareado"
    (guardado,) = _config_do_disco(amb)["equipamentos"]
    assert guardado["campos"]["chave_cliente"] == "a-chave-da-tv"


async def test_uma_credencial_de_campo_segredo_nunca_volta_ao_painel(abrir, amb):
    """A field the manifest declares SEGREDO is a secret wherever it came from."""
    manifesto = _manifesto(
        auth=Auth.POPUP_NO_APARELHO,
        config_campos=(*CAMPOS, Campo("token", TipoCampo.SEGREDO)),
    )
    classe = _fabrica(manifesto, credenciais={"token": "s3gr3d0-da-tv"})
    cliente, auth = await abrir({TIPO: classe})
    assert (await _cadastrar(cliente, auth)).status == 200
    await cliente.post("/api/equipamentos/uuid-1/autenticar", headers=auth)
    (guardado,) = _config_do_disco(amb)["equipamentos"]
    assert guardado["segredos"]["token"] == "s3gr3d0-da-tv"
    assert guardado["campos"].get("token") is None
    lidos = await (await cliente.get("/api/equipamentos", headers=auth)).json()
    assert "s3gr3d0-da-tv" not in json.dumps(lidos)
    (equipamento,) = lidos["equipamentos"]
    assert "token" in equipamento["segredos_definidos"]


async def test_um_pareamento_que_nao_terminou_nao_grava_nada(abrir, amb):
    """Only pareado is a pairing; aguardando is a dialog still on the screen."""
    manifesto = _manifesto(
        auth=Auth.POPUP_NO_APARELHO,
        config_campos=(*CAMPOS, Campo("chave_cliente", TipoCampo.TEXTO)),
    )
    classe = _fabrica(
        manifesto, resultado="aguardando", credenciais={"chave_cliente": "cedo-demais"}
    )
    cliente, auth = await abrir({TIPO: classe})
    assert (await _cadastrar(cliente, auth)).status == 200
    await cliente.post("/api/equipamentos/uuid-1/autenticar", headers=auth)
    (guardado,) = _config_do_disco(amb)["equipamentos"]
    assert guardado["campos"].get("chave_cliente") in (None, "")


def _manifesto_de_conta() -> Manifesto:
    return _manifesto(
        auth=Auth.CHAVE,
        config_campos=(*CAMPOS, Campo("dispositivo", TipoCampo.TEXTO)),
        lista_da_conta="dispositivo",
    )


async def test_a_rota_lista_o_que_as_credenciais_alcancam_sem_cadastro_nenhum(abrir):
    """The id of a device of an account exists only inside the account.

    Asking the operator to find it and type it is asking him to do what the account
    already answers, and this route is what turns that into a list he clicks.
    """
    classe = _fabrica(
        _manifesto_de_conta(),
        da_conta=(Aparelho(id="ar-1", nome="Sala"), Aparelho(id="ar-2", nome="Quarto")),
    )
    cliente, auth = await abrir({TIPO: classe})
    corpo = {"campos": {"porta": "8080", "senha": "s3gr3d0", "dispositivo": ""}}
    resposta = await cliente.post(f"/api/catalogo/{TIPO}/aparelhos", json=corpo, headers=auth)
    assert resposta.status == 200
    assert await resposta.json() == {
        "ok": True,
        "code": None,
        "campo": "dispositivo",
        "aparelhos": [{"id": "ar-1", "nome": "Sala"}, {"id": "ar-2", "nome": "Quarto"}],
    }


async def test_um_driver_que_nao_lista_recusa_a_rota(abrir):
    """A driver whose device is at an address has no account to list."""
    cliente, auth = await abrir({TIPO: _fabrica()})
    resposta = await cliente.post(f"/api/catalogo/{TIPO}/aparelhos", json={}, headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "nao_suportado"


async def test_uma_conta_que_recusa_o_token_responde_auth_pendente(abrir):
    """The account refusing is the credential being wrong, which is what the operator fixes."""
    classe = _fabrica(_manifesto_de_conta(), conta_estoura="401")
    cliente, auth = await abrir({TIPO: classe})
    corpo = {"campos": {"porta": "8080", "senha": "errado", "dispositivo": ""}}
    resposta = await cliente.post(f"/api/catalogo/{TIPO}/aparelhos", json=corpo, headers=auth)
    # 409, the status this code already had: a credential not accepted yet is a conflict of
    # state and not a malformed request.
    assert resposta.status == 409
    assert (await resposta.json())["code"] == "auth_pendente"


async def test_um_campo_que_o_manifesto_nao_declara_e_recusado_na_listagem(abrir):
    """The fields of this call are the fields of a registration of this driver."""
    cliente, auth = await abrir({TIPO: _fabrica(_manifesto_de_conta())})
    corpo = {"campos": {"inventado": "x"}}
    resposta = await cliente.post(f"/api/catalogo/{TIPO}/aparelhos", json=corpo, headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "campo_invalido"


async def test_o_teto_de_nivel_e_gravado_exposto_e_aplicado(hub, amb):
    """A set above the ceiling of the registration lands on the ceiling, whoever asked."""
    cliente, auth, classe = hub
    assert (await _cadastrar(cliente, auth, nivel_maximo=40)).status == 200
    (lido,) = await _lista(cliente, auth)
    assert lido["nivel_maximo"] == 40
    (guardado,) = _config_do_disco(amb)["equipamentos"]
    assert guardado["nivel_maximo"] == 40
    acao = "/api/equipamentos/uuid-1/acao"
    assert (
        await cliente.post(acao, json={"acao": "volume", "valor": 80}, headers=auth)
    ).status == 200
    assert (
        await cliente.post(acao, json={"acao": "volume", "valor": 25}, headers=auth)
    ).status == 200
    assert classe.instancias[0].executados == [("volume", 40), ("volume", 25)]
    # An edit that says nothing about the ceiling keeps it; one that names a number sets it.
    editar = "/api/equipamentos/uuid-1"
    corpo = {"tipo": TIPO, "identidade": "uuid-1", "nome": "Sala 2", "ip": "192.0.2.10"}
    assert (await cliente.post(editar, json=corpo, headers=auth)).status == 200
    (lido,) = await _lista(cliente, auth)
    assert lido["nivel_maximo"] == 40 and lido["nome"] == "Sala 2"
    assert (
        await cliente.post(editar, json={**corpo, "nivel_maximo": "70"}, headers=auth)
    ).status == 200
    (lido,) = await _lista(cliente, auth)
    assert lido["nivel_maximo"] == 70


@pytest.mark.parametrize("teto", [0, 101, -1, "cem", 1.5, True])
async def test_um_teto_fora_de_um_a_cem_e_recusado(hub, teto):
    cliente, auth, _ = hub
    resposta = await _cadastrar(cliente, auth, nivel_maximo=teto)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "campo_invalido"


async def test_sem_teto_no_corpo_o_cadastro_nasce_com_cem(hub):
    cliente, auth, _ = hub
    assert (await _cadastrar(cliente, auth)).status == 200
    (lido,) = await _lista(cliente, auth)
    assert lido["nivel_maximo"] == 100
