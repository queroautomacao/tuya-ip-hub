# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The client of the relay inside the daemon, against a relay of the test.

What is proved here is the one property the whole door rests on: a request that comes down
the socket is NOT dispatched inside the process. It goes out and comes back through the
front door of this same daemon, on the loopback, with the authority of the window and the
mark of the relay written by this end, so every check of the gate runs on it.
"""

import asyncio
import base64
import json
import socket
import time
from dataclasses import replace

import pytest
from aiohttp import WSMsgType, web

from iphub import totp
from iphub.api.comum import aplicar_dp
from iphub.app import criar_app
from iphub.dpbus import mapa
from iphub.relay import RECUSADO, endereco_aceitavel

ENDERECO = "abcdefghijklmnopqrstuvwxyz"
LICENCA = {"id": "av1", "produto": "av", "nome": "Casa"}
HOME = "123456789"


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Relay:
    """The other end of the socket: hands out an address and asks whatever the test wants."""

    def __init__(self) -> None:
        self.socket: web.WebSocketResponse | None = None
        self.discou = asyncio.Event()
        self.base = ""
        # The registry of the relay of the test: the homes it serves. None serves any hub
        # without looking, the way a relay with no registry does.
        self.homes: set[str] | None = None
        self.olas: list[dict] = []
        self.recusas = 0
        self._quadros: asyncio.Queue = asyncio.Queue()
        self._numero = 0

    async def porta_do_hub(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.socket = ws
        ola = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
        self.olas.append(ola)
        if self.homes is not None and ola.get("home") not in self.homes:
            self.recusas += 1
            self.socket = None
            await ws.close(code=RECUSADO, message=b"home_nao_cadastrado")
            return ws
        await ws.send_str(
            json.dumps({"tipo": "pronto", "versao": 1, "endereco": ENDERECO, "url": self.url()})
        )
        self.discou.set()
        try:
            async for mensagem in ws:
                self._quadros.put_nowait(mensagem)
        finally:
            self.socket = None
        return ws

    def url(self) -> str:
        return f"{self.base}/h/{ENDERECO}/"

    async def pedir(
        self, metodo: str, caminho: str, cabecalhos: dict | None = None, corpo: bytes = b""
    ) -> tuple[int, dict, bytes]:
        assert self.socket is not None
        self._numero += 1
        await self.socket.send_str(
            json.dumps(
                {
                    "tipo": "pedido",
                    "id": self._numero,
                    "metodo": metodo,
                    "caminho": caminho,
                    "cabecalhos": cabecalhos or {},
                    "corpo": base64.b64encode(corpo).decode("ascii"),
                }
            )
        )
        while True:
            mensagem = await asyncio.wait_for(self._quadros.get(), 5)
            assert mensagem.type is WSMsgType.TEXT, mensagem
            quadro = json.loads(mensagem.data)
            if quadro.get("id") == self._numero:
                return quadro["status"], quadro["cabecalhos"], base64.b64decode(quadro["corpo"])


@pytest.fixture
async def relay(aiohttp_server):
    encontro = _Relay()
    app = web.Application()
    app.router.add_get("/hub", encontro.porta_do_hub)
    servidor = await aiohttp_server(app)
    # "localhost" and not the address: the hub of the test answers on 127.0.0.1, and the
    # relay must be a different host, the way it is on a real installation, or the gate
    # would take the requests of the test for requests on the address of the relay.
    encontro.base = f"http://localhost:{servidor.port}"
    return encontro


@pytest.fixture
async def hub(aiohttp_server, aiohttp_client, amb, relay):
    """A daemon wired the way the image wires it, on a real port of the loopback."""
    porta = _porta_livre()
    ambiente = replace(amb, porta=porta, remoto_relay=relay.base)
    servidor = await aiohttp_server(criar_app(ambiente), port=porta)
    return await aiohttp_client(servidor)


async def _abrir_a_janela(hub, posse, bearer) -> str:
    token = await posse(hub)
    licenca = await hub.post("/api/licencas", json=LICENCA, headers=bearer(token))
    assert licenca.status == 200, await licenca.text()
    assert await aplicar_dp(hub.app, "av1", mapa.DP_REMOTO_HOME, HOME) is None
    comeco = await (await hub.post("/api/otp", headers=bearer(token))).json()
    codigo = totp.codigo_de(comeco["segredo"], totp.passo_de(time.time()))
    await hub.post("/api/otp/confirmar", json={"codigo": codigo}, headers=bearer(token))
    armado = await hub.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    assert armado.status == 200, await armado.text()
    return token


def _json(corpo: bytes) -> dict:
    return json.loads(corpo.decode("utf-8"))


async def test_o_hub_disca_quando_a_janela_abre_e_publica_o_endereco(hub, relay, posse, bearer):
    token = await _abrir_a_janela(hub, posse, bearer)
    await asyncio.wait_for(relay.discou.wait(), 5)
    situacao = await (await hub.get("/api/remoto", headers=bearer(token))).json()
    assert situacao["url"] == relay.url()
    assert situacao["conectado"] is True


async def test_o_pedido_volta_pela_porta_da_frente_com_a_marca_e_o_host(
    hub, relay, posse, bearer, senha
):
    """Down the socket comes a request of the internet, and the gate sees it as one: the
    window code is asked before the password is even looked at, whatever the frame said."""
    token = await _abrir_a_janela(hub, posse, bearer)
    await asyncio.wait_for(relay.discou.wait(), 5)
    status, _, corpo = await relay.pedir("GET", "/api/estado")
    assert status == 200 and _json(corpo)["configurado"] is True
    # The frame tries to dress the request as one of the local network and to pick its own
    # authority; this end writes both, so the gate keeps asking what it asks from outside.
    status, _, corpo = await relay.pedir(
        "POST",
        "/api/entrar",
        {"Content-Type": "application/json", "Host": "127.0.0.1", "X-Iphub-Relay": ""},
        json.dumps({"senha": senha}).encode(),
    )
    assert status == 401 and _json(corpo)["code"] == "acesso_exigido"
    # A session of one factor, opened on the LAN, is not a key from outside.
    status, _, corpo = await relay.pedir("GET", "/api/sessao", {"Authorization": f"Bearer {token}"})
    assert status == 401 and _json(corpo)["code"] == "otp_exigido"
    status, _, _ = await relay.pedir("POST", "/api/posse", {}, b'{"senha": "outra-senha-longa"}')
    assert status == 403


async def test_o_hub_fecha_o_socket_quando_a_janela_fecha(hub, relay, posse, bearer):
    token = await _abrir_a_janela(hub, posse, bearer)
    await asyncio.wait_for(relay.discou.wait(), 5)
    assert relay.socket is not None and not relay.socket.closed
    fechado = await hub.post("/api/remoto", json={"armar": False}, headers=bearer(token))
    assert (await fechado.json())["conectado"] is False
    for _ in range(50):
        if relay.socket is None or relay.socket.closed:
            break
        await asyncio.sleep(0.02)
    assert relay.socket is None or relay.socket.closed


async def test_o_hub_se_apresenta_com_o_home_do_app(hub, relay, posse, bearer):
    """The first word down the socket is which home of the app this hub belongs to; the
    relay asks the registry of the maker and only then hands out an address."""
    relay.homes = {HOME}
    token = await _abrir_a_janela(hub, posse, bearer)
    await asyncio.wait_for(relay.discou.wait(), 5)
    assert relay.olas == [{"tipo": "ola", "versao": 1, "home": HOME}]
    situacao = await (await hub.get("/api/remoto", headers=bearer(token))).json()
    assert situacao["conectado"] is True
    assert situacao["recusado"] is False


async def test_um_home_que_o_relay_nao_serve_e_recusado_e_o_painel_fica_sabendo(
    hub, relay, posse, bearer
):
    relay.homes = {"outro-home"}
    token = await _abrir_a_janela(hub, posse, bearer)
    for _ in range(100):
        if relay.recusas:
            break
        await asyncio.sleep(0.02)
    assert relay.recusas == 1
    for _ in range(100):
        situacao = await (await hub.get("/api/remoto", headers=bearer(token))).json()
        if situacao["recusado"]:
            break
        await asyncio.sleep(0.02)
    assert situacao["recusado"] is True
    assert situacao["conectado"] is False
    assert situacao["armado"] is True


@pytest.mark.parametrize(
    ("base", "aceito"),
    [
        ("https://remoto.exemplo.com", True),
        ("http://127.0.0.1:8090", True),
        ("http://localhost:8090", True),
        ("http://remoto.exemplo.com", False),
        ("http://203.0.113.5:8090", False),
        ("ws://remoto.exemplo.com", False),
        ("remoto.exemplo.com", False),
        ("", False),
    ],
)
def test_o_hub_so_disca_em_https_fora_desta_maquina(base, aceito):
    """The socket carries the password and both codes of whoever logs in from outside."""
    assert endereco_aceitavel(base) is aceito
