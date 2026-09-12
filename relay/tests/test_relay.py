# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The relay against a hub of the test and a browser of the test.

Every case here is about the one promise this service makes: the address IS the secret, and
it reaches exactly the hub that took it, for as long as that hub is on the other end.
"""

import asyncio
import json

import pytest
from aiohttp import WSMsgType

from relay import protocolo
from relay.app import criar_app


@pytest.fixture
async def relay(aiohttp_client):
    return await aiohttp_client(criar_app(base="https://remoto.exemplo.com"))


class _Hub:
    """A hub on the other end: it answers what the relay asks, the way a daemon would."""

    def __init__(self, socket, endereco: str, url: str) -> None:
        self.socket = socket
        self.endereco = endereco
        self.url = url
        self.pedidos: list[dict] = []
        self.resposta = {
            "status": 200,
            "corpo": b"ok",
            "cabecalhos": {"Content-Type": "text/plain"},
        }
        self.responder = True
        self._tarefa = asyncio.create_task(self._atender())

    async def _atender(self) -> None:
        async for mensagem in self.socket:
            if mensagem.type is not WSMsgType.TEXT:
                return
            quadro = json.loads(mensagem.data)
            if quadro.get("tipo") != protocolo.PEDIDO:
                continue
            self.pedidos.append(quadro)
            if not self.responder:
                continue
            await self.socket.send_str(
                protocolo.escrever(
                    {
                        "tipo": protocolo.RESPOSTA,
                        "id": quadro["id"],
                        "status": self.resposta["status"],
                        "cabecalhos": self.resposta["cabecalhos"],
                        "corpo": protocolo.corpo_para(self.resposta["corpo"]),
                    }
                )
            )

    async def fechar(self) -> None:
        self._tarefa.cancel()
        await self.socket.close()


@pytest.fixture
async def hub(relay):
    abertos: list[_Hub] = []

    async def abrir() -> _Hub:
        socket = await relay.ws_connect("/hub")
        pronto = json.loads((await socket.receive()).data)
        assert pronto["tipo"] == protocolo.PRONTO
        aberto = _Hub(socket, pronto["endereco"], pronto["url"])
        abertos.append(aberto)
        return aberto

    yield abrir
    for aberto in abertos:
        await aberto.fechar()


async def test_um_hub_que_disca_ganha_um_endereco_so_dele(relay, hub):
    primeiro = await hub()
    segundo = await hub()
    assert primeiro.endereco != segundo.endereco
    assert len(primeiro.endereco) >= 20
    assert primeiro.url.endswith(f"/h/{primeiro.endereco}/")


async def test_o_endereco_alcanca_o_hub_que_o_tomou(relay, hub):
    aberto = await hub()
    resposta = await relay.get(f"/h/{aberto.endereco}/api/estado")
    assert resposta.status == 200
    assert await resposta.text() == "ok"
    assert aberto.pedidos[-1]["caminho"] == "/api/estado"
    assert aberto.pedidos[-1]["metodo"] == "GET"


async def test_o_caminho_e_a_query_chegam_inteiros_e_o_corpo_tambem(relay, hub):
    aberto = await hub()
    await relay.post(f"/h/{aberto.endereco}/api/entrar?x=1&y=2", data=b'{"senha":"abc"}')
    pedido = aberto.pedidos[-1]
    assert pedido["caminho"] == "/api/entrar?x=1&y=2"
    assert protocolo.corpo_de(pedido["corpo"]) == b'{"senha":"abc"}'


async def test_um_endereco_que_ninguem_tomou_e_um_que_fechou_respondem_igual(relay, hub):
    """A guess and a window that ended must be indistinguishable from out here."""
    aberto = await hub()
    endereco = aberto.endereco
    inventado = await relay.get("/h/nao-existe-este-endereco/api/estado")
    await aberto.fechar()
    await asyncio.sleep(0.05)
    fechado = await relay.get(f"/h/{endereco}/api/estado")
    assert inventado.status == fechado.status == 404
    assert await inventado.json() == await fechado.json()


async def test_um_hub_nao_alcanca_o_endereco_do_outro(relay, hub):
    primeiro = await hub()
    segundo = await hub()
    await relay.get(f"/h/{segundo.endereco}/api/estado")
    assert primeiro.pedidos == []
    assert len(segundo.pedidos) == 1


async def test_o_hub_que_nao_responde_vira_erro_de_gateway(relay, hub, monkeypatch):
    from relay import app as modulo

    monkeypatch.setattr(modulo, "PRAZO_DA_RESPOSTA_S", 0.1)
    aberto = await hub()
    aberto.responder = False
    resposta = await relay.get(f"/h/{aberto.endereco}/api/estado")
    assert resposta.status == 504
    assert (await resposta.json())["code"] == "hub_demorou"


async def test_o_hub_nao_escolhe_o_host_nem_se_dizer_da_borda(relay, hub):
    """The headers that say WHERE a request came from are the ones the hub decides on its
    own; a browser that forges them must not be able to dress itself as the machine."""
    aberto = await hub()
    await relay.get(
        f"/h/{aberto.endereco}/api/estado",
        headers={"X-Iphub-Relay": "1", "Cf-Access-Jwt-Assertion": "forjado", "X-Real-Header": "ok"},
    )
    chegaram = {nome.lower() for nome in aberto.pedidos[-1]["cabecalhos"]}
    assert "x-iphub-relay" not in chegaram
    assert "cf-access-jwt-assertion" not in chegaram
    assert "host" not in chegaram
    assert "x-real-header" in chegaram


async def test_uma_resposta_com_cabecalho_forjado_nao_quebra_a_do_navegador(relay, hub):
    aberto = await hub()
    aberto.resposta = {
        "status": 200,
        "corpo": b"corpo",
        "cabecalhos": {"X-Bom": "sim", "X-Mau": "quebra\r\nSet-Cookie: roubado=1"},
    }
    resposta = await relay.get(f"/h/{aberto.endereco}/")
    assert resposta.status == 200
    assert resposta.headers.get("X-Bom") == "sim"
    assert "X-Mau" not in resposta.headers
    assert "roubado" not in str(resposta.headers)


async def test_a_resposta_leva_os_cabecalhos_de_seguranca_do_relay(relay, hub):
    aberto = await hub()
    resposta = await relay.get(f"/h/{aberto.endereco}/")
    assert resposta.headers["X-Frame-Options"] == "DENY"
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert resposta.headers["Server"] == "tuya-ip-hub-relay"


async def test_um_status_que_nao_e_status_nao_vira_resposta(relay, hub):
    aberto = await hub()
    aberto.resposta = {"status": 999, "corpo": b"", "cabecalhos": {}}
    resposta = await relay.get(f"/h/{aberto.endereco}/")
    assert resposta.status == 502


async def test_a_saude_diz_quantos_hubs_estao_na_linha(relay, hub):
    vazio = await (await relay.get("/saude")).json()
    assert vazio == {"ok": True, "hubs": 0}
    await hub()
    assert (await (await relay.get("/saude")).json())["hubs"] == 1


async def test_o_relay_nao_atende_caminho_que_nao_e_dele(relay):
    for caminho in ("/", "/api/estado", "/h/", "/qualquer/coisa"):
        resposta = await relay.get(caminho)
        assert resposta.status == 404, caminho


async def test_um_quadro_que_este_protocolo_nao_fala_fecha_o_socket(relay):
    socket = await relay.ws_connect("/hub")
    await socket.receive()
    await socket.send_str("nao sou json")
    mensagem = await socket.receive()
    assert mensagem.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)


async def test_um_hub_so_tem_tantos_pedidos_em_voo(relay, hub, monkeypatch):
    from relay import app as modulo

    monkeypatch.setattr(modulo, "EM_VOO_POR_HUB", 2)
    monkeypatch.setattr(modulo, "PRAZO_DA_RESPOSTA_S", 0.4)
    aberto = await hub()
    aberto.responder = False
    respostas = await asyncio.gather(*(relay.get(f"/h/{aberto.endereco}/{n}") for n in range(4)))
    situacoes = sorted(r.status for r in respostas)
    assert situacoes.count(429) == 2
    assert situacoes.count(504) == 2


async def test_o_endereco_sem_a_barra_final_e_redirecionado(relay, hub):
    """Without the closing slash the browser resolves every relative asset of the panel one
    level up, against this service instead of against the hub."""
    aberto = await hub()
    resposta = await relay.get(f"/h/{aberto.endereco}", allow_redirects=False)
    assert resposta.status == 308
    assert resposta.headers["Location"] == f"/h/{aberto.endereco}/"
    assert aberto.pedidos == []


async def test_um_navegador_recebe_pagina_e_uma_ferramenta_recebe_o_codigo(relay, hub):
    """Whoever lands here got a link that stopped working, and a code on a white page tells
    them nothing about what to do next."""
    aberto = await hub()
    endereco = aberto.endereco
    await aberto.fechar()
    await asyncio.sleep(0.05)
    pagina = await relay.get(f"/h/{endereco}/", headers={"Accept": "text/html"})
    assert pagina.status == 404
    assert pagina.content_type == "text/html"
    corpo = await pagina.text()
    assert "Esta janela está fechada" in corpo
    assert "This window is closed" in corpo
    # Nothing of the page is fetched from anywhere: this service serves no assets.
    assert "src=" not in corpo and "link rel" not in corpo
    ferramenta = await relay.get(f"/h/{endereco}/", headers={"Accept": "application/json"})
    assert await ferramenta.json() == {"ok": False, "code": "sem_hub"}


async def test_a_pagina_do_hub_que_nao_respondeu_diz_o_que_houve(relay, hub, monkeypatch):
    from relay import app as modulo

    monkeypatch.setattr(modulo, "PRAZO_DA_RESPOSTA_S", 0.1)
    aberto = await hub()
    aberto.responder = False
    resposta = await relay.get(f"/h/{aberto.endereco}/", headers={"Accept": "text/html"})
    assert resposta.status == 504
    assert "hub não respondeu" in await resposta.text()


@pytest.fixture
async def atras_do_proxy(aiohttp_client):
    """The relay as it runs on the server: behind a reverse proxy that is the only peer."""
    from relay.app import redes_de

    return await aiohttp_client(
        criar_app(base="https://remoto.exemplo.com", proxies=redes_de("127.0.0.0/8"))
    )


async def test_atras_do_proxy_o_navegador_e_o_ultimo_salto_do_x_forwarded_for(atras_do_proxy, hub):
    """Behind Traefik every peer is Traefik, and counting by peer would put every browser of
    the internet in one bucket of failed logins on every hub."""
    socket = await atras_do_proxy.ws_connect("/hub")
    pronto = json.loads((await socket.receive()).data)
    aberto = _Hub(socket, pronto["endereco"], pronto["url"])
    try:
        await atras_do_proxy.get(
            f"/h/{aberto.endereco}/api/estado",
            headers={"X-Forwarded-For": "10.9.9.9, 203.0.113.7, 127.0.0.1"},
        )
        assert aberto.pedidos[-1]["cabecalhos"]["X-Iphub-Cliente"] == "203.0.113.7"
        # The text left of the proxy's own entry is the browser's to write, and it is ignored.
        await atras_do_proxy.get(
            f"/h/{aberto.endereco}/api/estado",
            headers={"X-Forwarded-For": "198.51.100.1, 127.0.0.1"},
        )
        assert aberto.pedidos[-1]["cabecalhos"]["X-Iphub-Cliente"] == "198.51.100.1"
    finally:
        await aberto.fechar()


async def test_sem_proxy_declarado_o_x_forwarded_for_e_texto_do_navegador(relay, hub):
    aberto = await hub()
    await relay.get(f"/h/{aberto.endereco}/api/estado", headers={"X-Forwarded-For": "203.0.113.7"})
    assert aberto.pedidos[-1]["cabecalhos"]["X-Iphub-Cliente"] == "127.0.0.1"


async def test_atras_do_proxy_os_hubs_sao_contados_pelo_endereco_de_cada_um(
    atras_do_proxy, monkeypatch
):
    """Counted by peer, a fleet behind one reverse proxy would end at the per address cap."""
    from relay import app as modulo

    monkeypatch.setattr(modulo, "HUBS_POR_ENDERECO", 2)
    abertos = []
    for casa in ("203.0.113.1", "203.0.113.1", "203.0.113.2"):
        socket = await atras_do_proxy.ws_connect("/hub", headers={"X-Forwarded-For": casa})
        await socket.receive()
        abertos.append(socket)
    try:
        terceiro = await atras_do_proxy.get(
            "/hub",
            headers={
                "X-Forwarded-For": "203.0.113.1",
                "Connection": "Upgrade",
                "Upgrade": "websocket",
            },
        )
        assert terceiro.status == 429
        assert (await terceiro.json())["code"] == "hubs_demais"
        assert (await (await atras_do_proxy.get("/saude")).json())["hubs"] == 3
    finally:
        for socket in abertos:
            await socket.close()


async def test_o_processo_inteiro_tem_um_teto_de_hubs(relay, hub, monkeypatch):
    from relay import app as modulo

    monkeypatch.setattr(modulo, "HUBS_MAXIMO", 1)
    await hub()
    recusado = await relay.get("/hub", headers={"Connection": "Upgrade", "Upgrade": "websocket"})
    assert recusado.status == 429


async def test_o_caminho_chega_como_o_navegador_escreveu(relay, hub):
    """Decoded here, an encoded slash would become a real one and name another route."""
    aberto = await hub()
    await relay.get(f"/h/{aberto.endereco}/api/equipamentos/a%2Fb?q=x%2Fy")
    assert aberto.pedidos[-1]["caminho"].startswith("/api/equipamentos/a%2Fb?q=")


async def test_um_corpo_alem_do_teto_e_recusado_e_solta_a_vaga(relay, hub, monkeypatch):
    from relay import app as modulo

    monkeypatch.setattr(modulo.protocolo, "PEDIDO_MAXIMO", 16)
    aberto = await hub()
    grande = await relay.post(f"/h/{aberto.endereco}/api/entrar", data=b"x" * 17)
    assert grande.status == 413
    assert aberto.pedidos == []
    assert modulo.HUBS is not None
    hub_do_relay = relay.app[modulo.HUBS][aberto.endereco]
    assert hub_do_relay.em_voo() == 0


def test_as_redes_dos_proxies_vem_de_uma_lista_e_um_erro_e_recusado():
    from relay.app import redes_de

    redes = redes_de(" 10.0.0.0/8, 172.17.0.5 ,, ")
    assert [str(rede) for rede in redes] == ["10.0.0.0/8", "172.17.0.5/32"]
    assert redes_de("") == ()
    with pytest.raises(ValueError):
        redes_de("traefik")


class _Directus:
    """The registry of the maker, as the relay sees it: one collection, one field."""

    def __init__(self) -> None:
        self.homes = {"123456"}
        self.token = "token-do-relay"
        self.perguntas: list[str] = []
        self.fora_do_ar = False
        # A registry that answers its first record whatever the filter says.
        self.ignora_o_filtro = False
        self.esconde_o_campo = False

    async def itens(self, request):
        from aiohttp import web

        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return web.json_response({"errors": [{"message": "no"}]}, status=401)
        if self.fora_do_ar:
            return web.json_response({"errors": [{"message": "down"}]}, status=503)
        # The relay writes the brackets as brackets, the way the app of the maker does.
        assert "filter[home_id][_eq]=" in request.query_string
        home = request.query.get("filter[home_id][_eq]", "")
        self.perguntas.append(home)
        if self.ignora_o_filtro:
            return web.json_response({"data": [{"home_id": "123456"}]})
        if self.esconde_o_campo:
            return web.json_response({"data": [{}] if home in self.homes else []})
        dados = [{"home_id": home}] if home in self.homes else []
        return web.json_response({"data": dados})


@pytest.fixture
async def directus(aiohttp_server):
    from aiohttp import web

    registro = _Directus()
    app = web.Application()
    app.router.add_get("/items/homes", registro.itens)
    servidor = await aiohttp_server(app)
    registro.url = f"http://127.0.0.1:{servidor.port}"
    return registro


@pytest.fixture
async def com_cadastro(aiohttp_client, directus):
    """A relay that asks the registry: only a hub of a registered home gets an address."""
    from relay.cadastro import Cadastro

    cadastro = Cadastro(directus.url, directus.token, "homes", "home_id")
    return await aiohttp_client(criar_app(base="https://remoto.exemplo.com", cadastro=cadastro))


async def _discar_de(cliente, home):
    socket = await cliente.ws_connect("/hub")
    await socket.send_str(protocolo.escrever({"tipo": protocolo.OLA, "versao": 1, "home": home}))
    return socket, await socket.receive()


async def test_com_cadastro_um_home_cadastrado_ganha_endereco(com_cadastro, directus):
    socket, resposta = await _discar_de(com_cadastro, "123456")
    assert resposta.type is WSMsgType.TEXT
    assert json.loads(resposta.data)["tipo"] == protocolo.PRONTO
    assert directus.perguntas == ["123456"]
    assert (await (await com_cadastro.get("/saude")).json())["hubs"] == 1
    await socket.close()


async def test_com_cadastro_um_home_que_ninguem_cadastrou_e_recusado(com_cadastro):
    socket, resposta = await _discar_de(com_cadastro, "999999")
    assert resposta.type is WSMsgType.CLOSE
    assert resposta.data == protocolo.RECUSADO
    assert (await (await com_cadastro.get("/saude")).json())["hubs"] == 0
    await socket.close()


@pytest.mark.parametrize("home", ["", "abc", "12 34", "1' or 1=1", "1" * 21, None, 123456])
async def test_com_cadastro_o_que_nao_e_id_de_home_nem_e_perguntado(com_cadastro, directus, home):
    socket, resposta = await _discar_de(com_cadastro, home)
    assert resposta.type is WSMsgType.CLOSE
    assert resposta.data == protocolo.RECUSADO
    assert directus.perguntas == []
    await socket.close()


async def test_com_cadastro_um_hub_que_nao_se_apresenta_e_fechado(com_cadastro, monkeypatch):
    from relay import app as modulo

    monkeypatch.setattr(modulo, "PRAZO_DO_OLA_S", 0.1)
    socket = await com_cadastro.ws_connect("/hub")
    resposta = await socket.receive()
    assert resposta.type is WSMsgType.CLOSE
    assert resposta.data == 4408
    await socket.close()


async def test_com_o_cadastro_fora_do_ar_a_resposta_nao_e_recusa(com_cadastro, directus):
    """Neither yes nor no: the hub dials again soon, instead of waiting out a refusal."""
    directus.fora_do_ar = True
    socket, resposta = await _discar_de(com_cadastro, "123456")
    assert resposta.type is WSMsgType.CLOSE
    assert resposta.data == protocolo.INDISPONIVEL
    await socket.close()


async def test_um_home_confirmado_nao_e_perguntado_de_novo_por_um_tempo(com_cadastro, directus):
    primeiro, _ = await _discar_de(com_cadastro, "123456")
    segundo, resposta = await _discar_de(com_cadastro, "123456")
    assert json.loads(resposta.data)["tipo"] == protocolo.PRONTO
    assert directus.perguntas == ["123456"]
    await primeiro.close()
    await segundo.close()


async def test_um_home_recusado_e_perguntado_de_novo_a_cada_discagem(com_cadastro, directus):
    """Registering a home must count on the very next dial, so a no is never remembered."""
    primeiro, _ = await _discar_de(com_cadastro, "777777")
    directus.homes.add("777777")
    segundo, resposta = await _discar_de(com_cadastro, "777777")
    assert json.loads(resposta.data)["tipo"] == protocolo.PRONTO
    assert directus.perguntas == ["777777", "777777"]
    await primeiro.close()
    await segundo.close()


async def test_um_cadastro_que_ignora_o_filtro_nao_abre_a_porta(com_cadastro, directus, caplog):
    """A registry that answers its first record to every question would serve every hub;
    the record that comes back has to be the home that was asked about."""
    directus.ignora_o_filtro = True
    socket, resposta = await _discar_de(com_cadastro, "999999")
    assert resposta.type is WSMsgType.CLOSE
    assert resposta.data == protocolo.RECUSADO
    assert "not the home asked about" in caplog.text
    await socket.close()


async def test_um_token_que_nao_le_o_campo_nao_abre_a_porta(com_cadastro, directus):
    directus.esconde_o_campo = True
    socket, resposta = await _discar_de(com_cadastro, "123456")
    assert resposta.type is WSMsgType.CLOSE
    assert resposta.data == protocolo.RECUSADO
    await socket.close()


async def test_sem_cadastro_o_relay_serve_qualquer_hub_sem_perguntar(relay):
    socket = await relay.ws_connect("/hub")
    primeiro = json.loads((await socket.receive()).data)
    assert primeiro["tipo"] == protocolo.PRONTO
    await socket.close()


def test_o_cadastro_recusa_nomes_e_enderecos_que_nao_sao_de_directus():
    from relay.cadastro import Cadastro, home_valido

    with pytest.raises(ValueError):
        Cadastro("http://directus:8055", "t", "homes; drop", "home_id")
    with pytest.raises(ValueError):
        Cadastro("directus:8055", "t", "homes", "home_id")
    assert home_valido("123456") is True
    assert home_valido("0") is True
    assert home_valido("12a") is False
    assert home_valido("") is False
