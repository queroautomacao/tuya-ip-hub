# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The declarative engine over HTTP, against a simulated amplifier, section 12: no hardware.

Two rules of section 7 are attacked here more than anywhere else. The base is written with the
placeholder and nothing else, so a driver received ready made cannot aim a poll at a host of
its own and hand it the internal address of the customer. And the VALUE of a header comes from
the registration, never from the file, so a JSON shared between installations never carries a
token; the file names the field, and the field is what the integrator typed.

The ceiling on the body is the third: a device that answers its whole web page must not be
read whole, on a board where that memory is the memory of everything else.

O motor declarativo sobre HTTP, contra um amplificador simulado, seção 12: sem hardware.

Duas regras da seção 7 são atacadas aqui mais que em qualquer outro lugar. A base é escrita com
o marcador e nada mais, então um driver recebido pronto não pode apontar um poll para um host
próprio e entregar a ele o endereço interno do cliente. E o VALOR de um cabeçalho vem do
cadastro, nunca do arquivo, então um JSON compartilhado entre instalações nunca leva token; o
arquivo nomeia o campo, e o campo é o que o integrador digitou.

O teto do corpo é a terceira: um aparelho que responde a página inteira dele não pode ser lido
inteiro, numa placa em que essa memória é a memória de todo o resto.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from aiohttp import web

from iphub.drivers.base import CODIGOS, Driver
from iphub.drivers.declarativo import transporte
from iphub.drivers.declarativo.formato import validar
from iphub.drivers.declarativo.motor import construir
from iphub.drivers.manifesto import Estado
from iphub.drivers.simulado import ServidorHttp

# Why: the three examples of milestone 3 prove the engine and never ship, so they live with
# the tests and not in the catalogue of the image.
# Por que: os três exemplos do marco 3 provam o motor e nunca embarcam, então vivem com os
# testes e não no catálogo da imagem.
EXEMPLOS = Path(__file__).resolve().parent / "exemplos"

TOKEN = "token-do-integrador"
ESCALA_MAXIMA = 79

ESTADO = json.dumps({"power": {"state": "on"}, "mute": 1, "now": "Radio Um"})
VOLUME = json.dumps({"volume": {"value": 40}})
ROTAS = {"/api/status": (200, ESTADO), "/api/volume": (200, VOLUME)}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "uuid-do-amplificador"
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=lambda: {"token": TOKEN})


class Fogo:
    """The fire test as a double: this layer proves the engine, never the worker process.

    A prova de fogo como dublê: esta camada prova o motor, nunca o processo trabalhador.
    """

    def perigosa(self, padrao: str) -> bool:
        return False


class Regex:
    """The safe regex driven by hand, so a test proves the engine asks IT and never `re`.

    A regex segura dirigida na mão, para um teste provar que o motor pergunta a ELA e nunca
    ao `re`.
    """

    def __init__(self) -> None:
        self.perguntou: list[str] = []

    async def buscar_async(self, padrao: str, texto: str) -> list[str | None] | None:
        self.perguntou.append(padrao)
        casamento = re.search(padrao, texto)
        return list(casamento.groups()) if casamento else []


class Desviador:
    """A device that answers a redirect, which the simulated one has no way to write.

    /api/status answers 302 pointing at /desviado, and /desviado answers a state that would be
    read as a device that is on, so a redirect followed shows up in the reading itself.

    Um aparelho que responde um redirecionamento, que o simulado não tem como escrever.

    /api/status responde 302 apontando para /desviado, e /desviado responde um estado que seria
    lido como aparelho ligado, então um desvio seguido aparece na própria leitura.
    """

    def __init__(self, destino: str) -> None:
        self.destino = destino
        self.pedidos: list[str] = []
        self.endereco = ("", 0)
        self._runner: web.AppRunner | None = None

    async def __aenter__(self):
        app = web.Application()
        app.router.add_route("*", "/{cauda:.*}", self._atender)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        sitio = web.TCPSite(self._runner, "127.0.0.1", 0)
        await sitio.start()
        self.endereco = self._runner.addresses[0][:2]
        return self

    async def __aexit__(self, *_erro: object) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()

    async def _atender(self, request: web.Request) -> web.Response:
        self.pedidos.append(request.path_qs)
        if request.path == self.destino:
            return web.Response(status=200, text=ESTADO)
        return web.Response(status=302, headers={"Location": self.destino})


def _declaracao(porta: int, **mudancas) -> dict:
    """An amplifier over HTTP: a body with braces, a header from the registration, a scale.

    Um amplificador por HTTP: corpo com chaves, cabeçalho vindo do cadastro, uma escala.
    """
    arquivo = {
        "manifesto": {
            "tipo": "amplificador_de_teste",
            "rotulo": {"pt": "Amplificador de teste", "en": "Test amplifier"},
            "categoria": "audio",
            "capacidades": ["ligar", "desligar", "volume", "mudo", "fonte"],
            "config_campos": [{"nome": "token", "tipo": "segredo", "obrigatorio": True}],
            "textos": {
                "pt": {"descricao": "Amplificador com API HTTP", "campo_token": "Token da API"},
                "en": {"descricao": "Amplifier with an HTTP API", "campo_token": "API token"},
            },
        },
        "transporte": {
            "http": {
                "base": f"http://{{ip}}:{porta}",
                "metodo": "GET",
                "timeout_s": 0.5,
                "cabecalhos": {"Authorization": "token"},
            }
        },
        "comandos": {
            "ligar": {"envia": "/api/power", "metodo": "POST", "corpo": '{"on": true}'},
            "desligar": {"envia": "/api/power", "metodo": "POST", "corpo": '{"on": false}'},
            "volume": {"envia": "/api/volume?v={valor_escala}"},
            "mudo": {"envia": "/api/mute?v={valor}", "valores": {"true": "1", "false": "0"}},
            "fonte": {
                "envia": "/api/source?s={valor}",
                "valores": {"Streaming": "net", "Bluetooth": "bt"},
            },
        },
        "estado": {
            "pede": [{"envia": "/api/status"}, {"envia": "/api/volume"}],
            "le": {
                "ligado": {"json": "power.state", "verdadeiro": "on"},
                "mudo": {"json": "mute", "verdadeiro": "1"},
                "tocando": {"json": "now"},
                "volume": {"json": "volume.value"},
            },
        },
        "escala_volume": {"min": 0, "max": ESCALA_MAXIMA},
    }
    return {**arquivo, **mudancas}


def _driver(aparelho: ServidorHttp, *, regex=None, cadastro=None, **mudancas) -> Driver:
    dados = _declaracao(aparelho.endereco[1], **mudancas)
    classe = construir(validar(dados, regex=Fogo()), regex=regex or Regex())
    return classe(cadastro or _Cadastro(ip=aparelho.endereco[0]))


async def test_o_poll_le_duas_requisicoes_num_Estado_tipado():
    """Section 7: state in more than one request, and the first answer with a value wins.

    Seção 7: estado em mais de uma requisição, e a primeira resposta com valor vence.
    """
    async with ServidorHttp(ROTAS) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert isinstance(estado, Estado)
    assert estado.online is True
    assert estado.ligado is True
    assert estado.mudo is True
    assert estado.tocando == "Radio Um"
    # 40 of a device that goes to 79 is what the contract of section 6 writes as 51.
    # 40 de um aparelho que vai até 79 é o que o contrato da seção 6 escreve como 51.
    assert estado.volume == 51
    assert [pedido.caminho for pedido in aparelho.pedidos] == ["/api/status", "/api/volume"]


async def test_o_cabecalho_leva_o_campo_do_cadastro_e_o_arquivo_nunca_leva_o_segredo():
    """Section 7: the file names the field; a JSON shared between installations carries no token.

    Seção 7: o arquivo nomeia o campo; um JSON compartilhado entre instalações não leva token.
    """
    async with ServidorHttp(ROTAS) as aparelho:
        dados = _declaracao(aparelho.endereco[1])
        assert TOKEN not in json.dumps(dados)
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    assert aparelho.pedidos[0].cabecalhos["Authorization"] == TOKEN


async def test_o_cabecalho_do_campo_vazio_nao_e_enviado_vazio():
    """An empty Authorization answers 401 and sends the integrator hunting the network.

    Um Authorization vazio responde 401 e manda o integrador caçar a rede.
    """
    async with ServidorHttp(ROTAS) as aparelho:
        driver = _driver(aparelho, cadastro=_Cadastro(segredos={}))
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    assert "Authorization" not in aparelho.pedidos[0].cabecalhos


async def test_o_comando_manda_o_metodo_e_o_corpo_declarados_com_as_chaves_intactas():
    """The braces of a JSON body are not a substitution: only three markers exist, by name.

    As chaves de um corpo JSON não são substituição: só existem três marcadores, pelo nome.
    """
    async with ServidorHttp({"/api/power": (200, "")}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        assert await driver.executar("ligar") is None
        await driver.parar()
    pedido = aparelho.pedidos[0]
    assert (pedido.metodo, pedido.caminho) == ("POST", "/api/power")
    assert pedido.corpo == '{"on": true}'
    assert driver.estado().ligado is True


async def test_o_volume_vai_convertido_para_a_escala_do_aparelho():
    async with ServidorHttp({"/api/volume": (200, VOLUME)}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        assert await driver.executar("volume", 100) is None
        assert await driver.executar("volume", 0) is None
        await driver.parar()
    caminhos = [pedido.caminho for pedido in aparelho.pedidos]
    assert caminhos == [f"/api/volume?v={ESCALA_MAXIMA}", "/api/volume?v=0"]


async def test_o_valor_booleano_passa_pelo_mapa_declarado():
    async with ServidorHttp({"/api/mute": (200, "")}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        assert await driver.executar("mudo", True) is None
        assert await driver.executar("mudo", False) is None
        recusado = await driver.executar("mudo", "talvez")
        await driver.parar()
    assert [pedido.caminho for pedido in aparelho.pedidos] == [
        "/api/mute?v=1",
        "/api/mute?v=0",
    ]
    assert recusado == "invalid_value"
    assert driver.estado().mudo is False


async def test_o_valor_fora_do_mapa_nao_vira_requisicao():
    async with ServidorHttp(ROTAS) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        resposta = await driver.executar("fonte", "Disco de vinil")
        await driver.parar()
    assert resposta == "invalid_value"
    assert aparelho.pedidos == []


async def test_a_acao_fora_do_manifesto_nao_vira_requisicao():
    async with ServidorHttp(ROTAS) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        resposta = await driver.executar("tocar", "url")
        await driver.parar()
    assert resposta == "nao_suportado"
    assert aparelho.pedidos == []


async def test_o_aparelho_que_responde_erro_devolve_codigo_de_aparelho():
    """A device that answered is not a device that is offline, and the codes say which.

    Um aparelho que respondeu não é um aparelho offline, e os códigos dizem qual é qual.
    """
    async with ServidorHttp({"/api/power": (500, ""), "/api/status": (500, "")}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        resposta = await driver.executar("ligar")
        await driver.atualizar()
        await driver.parar()
    assert resposta == "erro_aparelho"
    assert resposta in CODIGOS
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_o_caminho_que_o_aparelho_nao_tem_e_erro_e_nao_leitura_vazia():
    """A 404 read as an empty answer would report a device online forever, reading nothing.

    Um 404 lido como resposta vazia reportaria um aparelho online para sempre, sem ler nada.
    """
    async with ServidorHttp({}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_o_aparelho_que_nao_atende_deixa_o_equipamento_offline():
    aparelho = ServidorHttp(ROTAS)
    await aparelho.iniciar()
    driver = _driver(aparelho)
    await driver.iniciar()
    await aparelho.parar()
    await driver.atualizar()
    resposta = await driver.executar("ligar")
    await driver.parar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    assert resposta == "eq_offline"


async def test_o_corpo_gigante_e_lido_ate_o_teto_e_nao_alem():
    """The bench ceiling of 64 KB: what is past it is what the daemon refuses to hold.

    O teto de bancada de 64 KB: o que passa dele é o que o daemon se recusa a guardar.
    """
    leitura = {"volume": {"regex": "VOL ([0-9]{1,3})"}}
    perto = "VOL 40 " + "x" * (transporte.CORPO_MAXIMO * 2)
    longe = "x" * (transporte.CORPO_MAXIMO * 2) + " VOL 40"
    for corpo, esperado in ((perto, 51), (longe, None)):
        async with ServidorHttp({"/api/status": (200, corpo)}) as aparelho:
            driver = _driver(
                aparelho,
                estado={"pede": [{"envia": "/api/status"}], "le": leitura},
            )
            await driver.iniciar()
            await driver.atualizar()
            await driver.parar()
        assert driver.estado().online is True
        assert driver.estado().volume == esperado


async def test_o_corpo_que_nao_e_json_nao_derruba_o_poll():
    """A device is free to answer its home page, and no reading of it may raise.

    Um aparelho é livre para responder a página inicial dele, e nenhuma leitura pode estourar.
    """
    async with ServidorHttp({"/api/status": (200, "<html>ligado</html>")}) as aparelho:
        driver = _driver(aparelho, estado={"pede": [{"envia": "/api/status"}], "le": {}})
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.volume, estado.mudo) == (None, None, None)


@pytest.mark.parametrize(
    "respondido,esperado",
    [
        (float("inf"), None),
        (float("-inf"), None),
        (float("nan"), None),
        (10**400, None),
        # A finite number is a reading, however big, and the contract of section 6 clamps it.
        # Um número finito é leitura, por maior que seja, e o contrato da seção 6 o prende.
        (1e308, 100),
    ],
)
async def test_o_volume_que_nao_e_finito_e_um_campo_sem_leitura_e_nao_um_poll_perdido(
    respondido, esperado
):
    """A JSON that carries infinity or NaN where a volume belongs is a field that was not
    read, never a poll that died: the power, the mute and what is playing came in the FIRST
    request of the same poll, and converting the number of the second threw all three away.

    Um JSON que leva infinito ou NaN onde mora o volume é um campo que não foi lido, nunca um
    poll que morreu: a energia, o mudo e o que toca vieram na PRIMEIRA requisição do mesmo
    poll, e converter o número da segunda jogava os três fora.
    """
    rotas = {"/api/status": (200, ESTADO), "/api/volume": (200, json.dumps({"volume": respondido}))}
    async with ServidorHttp(rotas) as aparelho:
        driver = _driver(
            aparelho,
            estado={
                "pede": [{"envia": "/api/status"}, {"envia": "/api/volume"}],
                "le": {
                    "ligado": {"json": "power.state", "verdadeiro": "on"},
                    "tocando": {"json": "now"},
                    "volume": {"json": "volume"},
                },
            },
        )
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.detalhe == ""
    assert estado.volume == esperado
    # The readings of the other request still arrive: an unread field is None, not a dead poll.
    # As leituras da outra requisição continuam chegando: campo sem leitura é None, não poll morto.
    assert estado.ligado is True
    assert estado.tocando == "Radio Um"


async def test_o_volume_que_nao_e_finito_lido_por_regex_tambem_e_apenas_um_campo_sem_leitura():
    """The same rule on the other reading style, because the conversion is the same one.

    A mesma regra no outro estilo de leitura, porque a conversão é a mesma.
    """
    async with ServidorHttp({"/api/status": (200, "POWER ON VOL inf")}) as aparelho:
        driver = _driver(
            aparelho,
            estado={
                "pede": [{"envia": "/api/status"}],
                "le": {
                    "ligado": {"regex": "POWER (ON|OFF)", "verdadeiro": "ON"},
                    "volume": {"regex": "VOL ([^ ]+)"},
                },
            },
        )
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert (estado.online, estado.ligado, estado.volume) == (True, True, None)


async def test_o_redirecionamento_do_aparelho_nunca_e_seguido():
    """Section 9: the answer of an equipment is data, never an instruction. A device that
    answers a redirect would send the hub to whatever host it names, and the hub fetching a
    host that a device chose is the LAN proxy that section 9 refuses.

    The redirect here points at a path that WOULD be read as a state, so following it is
    visible in the reading and not only in the log.

    Seção 9: a resposta de um equipamento é dado, nunca instrução. Um aparelho respondendo um
    redirecionamento mandaria o hub para o host que ele nomear, e o hub buscando um host que um
    aparelho escolheu é o proxy de LAN que a seção 9 recusa.

    O desvio aqui aponta para um caminho que SERIA lido como estado, então seguir o desvio
    aparece na leitura e não só no log.
    """
    async with Desviador("/desviado") as aparelho:
        dados = _declaracao(aparelho.endereco[1])
        dados["estado"] = {
            "pede": [{"envia": "/api/status"}],
            "le": {"ligado": {"json": "power.state", "verdadeiro": "on"}},
        }
        classe = construir(validar(dados, regex=Fogo()), regex=Regex())
        driver = classe(_Cadastro(ip=aparelho.endereco[0]))
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    assert aparelho.pedidos == ["/api/status"]
    # The state of the redirect target is exactly what a followed redirect would have read.
    # O estado do destino do desvio é exatamente o que um desvio seguido teria lido.
    assert driver.estado().ligado is None
    assert driver.estado().online is True


async def test_um_json_que_leva_objeto_onde_cabe_valor_nao_vira_texto_no_painel():
    """str() of an object would put a piece of JSON on the card of the panel.

    O str() de um objeto poria um pedaço de JSON no cartão do painel.
    """
    corpo = json.dumps({"now": {"artist": "x"}, "power": {"state": ["on"]}})
    async with ServidorHttp({"/api/status": (200, corpo)}) as aparelho:
        driver = _driver(
            aparelho,
            estado={
                "pede": [{"envia": "/api/status"}],
                "le": {
                    "tocando": {"json": "now"},
                    "ligado": {"json": "power.state", "verdadeiro": "on"},
                },
            },
        )
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.tocando is None
    assert estado.ligado is None


async def test_o_endereco_que_nao_e_ip_literal_nunca_vira_requisicao():
    """Section 9: the base is rendered from the registered address, and only from an IP.

    Seção 9: a base nasce do endereço cadastrado, e só de um IP.
    """
    async with ServidorHttp(ROTAS) as aparelho:
        for endereco in ("localhost", "amplificador.local", "127.0.0.1:80", ""):
            driver = _driver(aparelho, cadastro=_Cadastro(ip=endereco))
            await driver.iniciar()
            await driver.atualizar()
            assert await driver.executar("ligar") == "eq_offline"
            await driver.parar()
            assert driver.estado().online is False
    assert aparelho.pedidos == []


def _embarcado(nome: str) -> dict:
    """One example of the catalog that ships in the image, read from the file itself.

    Um exemplo do catálogo que embarca na imagem, lido do próprio arquivo.
    """
    return json.loads((EXEMPLOS / nome).read_text(encoding="utf-8"))


async def test_o_exemplo_embarcado_de_http_fala_com_o_aparelho_simulado():
    """The exit gate of milestone 3: the file that ships is the file that was driven.

    O portão de saída do marco 3: o arquivo que embarca é o arquivo que foi dirigido.
    """
    chave = "chave-da-placa"
    rotas = {"/status.json": (200, json.dumps({"rele1": "on"})), "/relay/1/off": (200, "")}
    async with ServidorHttp(rotas) as aparelho:
        dados = _embarcado("rele_http.json")
        dados["transporte"]["http"]["base"] = f"http://{{ip}}:{aparelho.endereco[1]}"
        classe = construir(validar(dados, regex=Fogo()), regex=Regex())
        driver = classe(_Cadastro(ip=aparelho.endereco[0], segredos={"chave": chave}))
        await driver.iniciar()
        await driver.atualizar()
        assert await driver.executar("desligar") is None
        await driver.parar()
    assert driver.estado().online is True
    assert driver.estado().ligado is False
    assert [pedido.caminho for pedido in aparelho.pedidos] == ["/status.json", "/relay/1/off"]
    assert aparelho.pedidos[0].cabecalhos["X-Api-Key"] == chave


async def test_a_resposta_partida_em_dois_segmentos_e_lida_inteira():
    """Section 7: the reading of a state is of the whole answer, not of the first segment.

    Why: one read of the stream returns only what the buffer already holds, so a device on a
    busy network answered a body the regex and the json path then read from a piece of it.

    Seção 7: a leitura de um estado é da resposta inteira, não do primeiro segmento.

    Por que: uma leitura do fluxo devolve só o que o buffer já tem, então um aparelho numa
    rede cheia respondia um corpo que a regex e o caminho json liam de um pedaço dele.
    """
    async with ServidorHttp(ROTAS, partir=True) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    assert estado.mudo is True
    assert estado.tocando == "Radio Um"
    assert estado.volume == 51
