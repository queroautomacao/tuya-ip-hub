# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The remote door end to end: the window, the relay and the two codes, over HTTP.

Every test here is an attempt to get in with one of the locks missing. What they prove
together is that the relay decides nothing: it carries bytes, and the address of the relay is
not even a host of this hub until its owner opens a window.
"""

import asyncio
import time

import pytest

from iphub import totp
from iphub.api.comum import CONFIG
from iphub.app import criar_app
from iphub.remoto import Janela, Servico

RELAY = "https://remoto.exemplo.com"
HOST = "remoto.exemplo.com"
URL_DA_JANELA = f"{RELAY}/h/abcdefghijklmnopqrstuvwxyz/"


class _Transporte:
    """A socket that is always up: what is under test is the door, not the dialling."""

    def __init__(self) -> None:
        self.fechado = False

    def aberto(self) -> bool:
        return not self.fechado

    def morreu(self) -> bool:
        return False

    def estavel(self) -> bool:
        return True

    def url(self) -> str:
        return "" if self.fechado else URL_DA_JANELA

    def recomecar_a_espera(self) -> None:
        pass

    def espera_s(self) -> float:
        return 0.01

    def adiar(self) -> None:
        pass

    async def abrir(self) -> bool:
        self.fechado = False
        return True

    async def fechar(self) -> None:
        self.fechado = True


@pytest.fixture
async def porta(aiohttp_client, amb):
    """A hub whose remote door rides the relay, with the address already configured."""
    from iphub.config import Config
    from iphub.config import salvar as salvar_config

    amb.dir_data.mkdir(parents=True, exist_ok=True)
    salvar_config(Config(remoto_relay=RELAY), amb.dir_data)
    servico = Servico(Janela(amb.dir_data), _Transporte(), intervalo_s=0.05)
    cliente = await aiohttp_client(criar_app(amb, remoto=servico))
    return cliente, servico


def _de_fora(token: str = "", *, marca: bool = True, cliente: str = "") -> dict[str, str]:
    cabecalhos = {"Host": HOST}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    if marca:
        cabecalhos["X-Iphub-Relay"] = "1"
    if cliente:
        cabecalhos["X-Iphub-Cliente"] = cliente
    return cabecalhos


LICENCA = {"id": "av1", "produto": "av", "nome": "Casa"}
HOME = "123456789"


async def _com_home(cliente, bearer, token, home: str = HOME) -> str | None:
    """The miniApp of the maker writing the home of the app to the hub, through the bus."""
    from iphub.api.comum import aplicar_dp
    from iphub.dpbus import mapa

    await cliente.post("/api/licencas", json=LICENCA, headers=bearer(token))
    return await aplicar_dp(cliente.app, "av1", mapa.DP_REMOTO_HOME, home)


async def _preparar(cliente, posse, bearer, *, com_otp: bool = True) -> str:
    """Ownership, the home of the app and the second factor: a door ready to be armed."""
    token = await posse(cliente)
    assert await _com_home(cliente, bearer, token) is None
    if com_otp:
        comeco = await (await cliente.post("/api/otp", headers=bearer(token))).json()
        codigo = totp.codigo_de(comeco["segredo"], totp.passo_de(time.time()))
        pronto = await cliente.post(
            "/api/otp/confirmar", json={"codigo": codigo}, headers=bearer(token)
        )
        assert pronto.status == 200, await pronto.text()
    return token


async def test_fora_da_janela_o_endereco_do_relay_nao_e_host(porta, posse, bearer):
    """Closed, the address of the relay is not a name this hub answers to at all."""
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    resposta = await cliente.get("/api/sessao", headers=_de_fora(token))
    assert resposta.status == 421
    assert (await resposta.json())["code"] == "host_nao_permitido"


async def test_dentro_da_janela_sem_a_marca_do_relay_nada_passa(porta, posse, bearer):
    """The mark is written by the client of the relay inside this daemon, so a request
    carrying the authority of the relay without it did not come through the relay."""
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    armado = await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    assert armado.status == 200, await armado.text()
    resposta = await cliente.get("/api/sessao", headers=_de_fora(token, marca=False))
    assert resposta.status == 403
    assert (await resposta.json())["code"] == "acesso_negado"


async def test_uma_sessao_de_um_fator_nao_entra_pelo_relay(porta, posse, bearer):
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    resposta = await cliente.get("/api/sessao", headers=_de_fora(token))
    assert resposta.status == 401
    assert (await resposta.json())["code"] == "otp_exigido"


async def test_o_login_de_fora_pede_o_codigo_da_janela_e_o_do_autenticador(
    porta, posse, senha, bearer
):
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    sem_nada = await cliente.post("/api/entrar", json={"senha": senha}, headers=_de_fora())
    assert (await sem_nada.json())["code"] == "acesso_exigido"
    errado = await cliente.post(
        "/api/entrar", json={"senha": senha, "acesso": "AAAA-AAAA"}, headers=_de_fora()
    )
    assert (await errado.json())["code"] == "acesso_invalido"
    so_a_janela = await cliente.post(
        "/api/entrar",
        json={"senha": senha, "acesso": servico.janela.codigo()},
        headers=_de_fora(),
    )
    assert (await so_a_janela.json())["code"] == "otp_exigido"


async def test_com_as_tres_chaves_a_porta_abre(porta, posse, senha, bearer):
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    segredo = cliente.app[CONFIG].valor.otp_segredo
    codigo = totp.codigo_de(segredo, totp.passo_de(time.time()) + 1)
    entrada = await cliente.post(
        "/api/entrar",
        json={"senha": senha, "codigo": codigo, "acesso": servico.janela.codigo()},
        headers=_de_fora(),
    )
    assert entrada.status == 200, await entrada.text()
    dois = await entrada.json()
    assert dois["fatores"] == 2
    assert (await cliente.get("/api/sessao", headers=_de_fora(dois["token"]))).status == 200


async def test_a_posse_e_o_barramento_nunca_atendem_de_fora(porta, posse, bearer):
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    for caminho in ("/api/posse", "/dpbus"):
        resposta = await cliente.post(
            caminho, json={"senha": "outra-senha-longa"}, headers=_de_fora()
        )
        assert resposta.status == 403, caminho


async def test_armar_e_recusado_enquanto_faltar_relay_ou_segundo_fator(
    aiohttp_client, amb, posse, bearer
):
    from iphub.config import Config
    from iphub.config import salvar as salvar_config

    amb.dir_data.mkdir(parents=True, exist_ok=True)
    salvar_config(Config(), amb.dir_data)
    servico = Servico(Janela(amb.dir_data), _Transporte(), intervalo_s=0.05)
    cliente = await aiohttp_client(criar_app(amb, remoto=servico))
    token = await posse(cliente)
    sem_home = await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    assert (await sem_home.json())["code"] == "sem_home"
    await _com_home(cliente, bearer, token)
    sem_relay = await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    assert (await sem_relay.json())["code"] == "sem_relay"


async def test_o_endereco_da_janela_e_publicado_no_painel_e_no_app(porta, posse, bearer):
    from iphub.api.comum import aplicar_dp, valores_dps
    from iphub.dpbus import mapa

    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    assert await aplicar_dp(cliente.app, "av1", mapa.DP_REMOTO, True) is None
    valores = valores_dps(cliente.app, "av1")
    assert valores[mapa.DP_REMOTO] is True
    assert valores[mapa.DP_REMOTO_URL] == URL_DA_JANELA
    assert valores[mapa.DP_REMOTO_CODIGO] == servico.janela.codigo()
    painel = await (await cliente.get("/api/remoto", headers=bearer(token))).json()
    assert painel["url"] == URL_DA_JANELA
    assert painel["conectado"] is True
    assert await aplicar_dp(cliente.app, "av1", mapa.DP_REMOTO, False) is None
    fechado = valores_dps(cliente.app, "av1")
    assert fechado[mapa.DP_REMOTO_URL] == ""
    assert fechado[mapa.DP_REMOTO_CODIGO] == ""


async def test_os_erros_de_um_integrador_nao_trancam_a_porta_dos_outros(
    porta, posse, senha, bearer
):
    """Every request over the relay arrives from the socket of this same daemon, so counting
    the failures by address would let the typos of one lock the door for the whole team."""
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    desastrado = _de_fora(cliente="203.0.113.9")
    errado = {"senha": senha, "acesso": "AAAA-AAAA"}
    for _ in range(6):
        await cliente.post("/api/entrar", json=errado, headers=desastrado)
    certo = {"senha": senha, "acesso": servico.janela.codigo()}
    assert (await cliente.post("/api/entrar", json=certo, headers=desastrado)).status == 429
    outro = _de_fora(cliente="203.0.113.10")
    resposta = await cliente.post("/api/entrar", json=certo, headers=outro)
    assert (await resposta.json())["code"] == "otp_exigido"


async def test_varios_integradores_entram_ao_mesmo_tempo(porta, posse, senha, bearer):
    """Nothing about the door is one at a time; the authenticator is, and it says so."""
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    segredo = cliente.app[CONFIG].valor.otp_segredo
    codigo = totp.codigo_de(segredo, totp.passo_de(time.time()) + 1)
    corpo = {"senha": senha, "codigo": codigo, "acesso": servico.janela.codigo()}
    respostas = await asyncio.gather(
        *(
            cliente.post("/api/entrar", json=corpo, headers=_de_fora(cliente=f"203.0.113.{n}"))
            for n in range(3)
        )
    )
    codigos = [(await r.json()).get("code") for r in respostas]
    assert codigos.count(None) == 1
    assert set(c for c in codigos if c) == {"codigo_invalido"}


async def test_desarmar_fecha_a_porta_e_o_endereco_deixa_de_existir(porta, posse, bearer):
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    assert servico.janela.ativa() is True
    resposta = await cliente.post("/api/remoto", json={"armar": False}, headers=bearer(token))
    assert (await resposta.json())["armado"] is False
    assert (await cliente.get("/api/sessao", headers=_de_fora(token))).status == 421


async def test_tirar_o_segundo_fator_fecha_a_porta_e_mata_as_sessoes(porta, posse, senha, bearer):
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    resposta = await cliente.delete("/api/otp", json={"senha": senha}, headers=bearer(token))
    assert resposta.status == 200
    assert servico.janela.ativa() is False
    assert (await cliente.get("/api/sessao", headers=bearer(token))).status == 401


async def test_na_rede_local_nada_disso_e_pedido(porta, posse, senha, bearer):
    cliente, servico = porta
    await _preparar(cliente, posse, bearer)
    resposta = await cliente.post("/api/entrar", json={"senha": senha})
    assert resposta.status == 200
    assert (await resposta.json())["fatores"] == 1


async def test_sem_o_codigo_da_janela_a_senha_nem_e_olhada(porta, posse, senha, bearer):
    """The code of the window is the first lock from outside. Whoever does not hold it gets
    no answer about the password, right or wrong, and spends nothing of the hub."""
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    curioso = _de_fora(cliente="203.0.113.20")
    for _ in range(8):
        errada = await cliente.post("/api/entrar", json={"senha": "nao-e-esta"}, headers=curioso)
        assert (await errada.json())["code"] == "acesso_exigido"
    certa = await cliente.post("/api/entrar", json={"senha": senha}, headers=curioso)
    # Eight tries with no code counted nothing against this browser and said nothing
    # about the password: the right one gets the very same answer as the wrong one.
    assert (await certa.json())["code"] == "acesso_exigido"
    assert certa.status == 401


async def test_a_sessao_de_fora_morre_quando_a_janela_fecha(porta, posse, senha, bearer):
    """The code of the window is checked once, at the login; a token that outlived the
    window would be a way back in on the next one, with no code asked."""
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    segredo = cliente.app[CONFIG].valor.otp_segredo
    codigo = totp.codigo_de(segredo, totp.passo_de(time.time()) + 1)
    entrada = await cliente.post(
        "/api/entrar",
        json={"senha": senha, "codigo": codigo, "acesso": servico.janela.codigo()},
        headers=_de_fora(),
    )
    de_fora = (await entrada.json())["token"]
    # It is good for the window and not a second longer, whatever the idle rule says.
    assert (await entrada.json())["expira_em_s"] <= servico.janela.restante_s()
    assert (await cliente.get("/api/sessao", headers=_de_fora(de_fora))).status == 200
    await cliente.post("/api/remoto", json={"armar": False}, headers=bearer(token))
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    fechada = await cliente.get("/api/sessao", headers=_de_fora(de_fora))
    assert fechada.status == 401
    assert (await fechada.json())["code"] == "sessao_invalida"
    # The session of the owner, opened on the local network, is not touched by any of it.
    assert (await cliente.get("/api/sessao", headers=bearer(token))).status == 200


async def test_a_sessao_de_fora_vence_com_a_janela_mesmo_sem_ninguem_fechar(
    aiohttp_client, amb, posse, senha, bearer, relogio
):
    from iphub.config import Config
    from iphub.config import salvar as salvar_config
    from iphub.remoto import JANELA_S
    from iphub.sessoes import Sessoes

    amb.dir_data.mkdir(parents=True, exist_ok=True)
    salvar_config(Config(remoto_relay=RELAY), amb.dir_data)
    servico = Servico(Janela(amb.dir_data, relogio), _Transporte(), intervalo_s=0.05)
    cliente = await aiohttp_client(
        criar_app(amb, remoto=servico, sessoes=Sessoes(amb.dir_data, relogio))
    )
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    segredo = cliente.app[CONFIG].valor.otp_segredo
    codigo = totp.codigo_de(segredo, totp.passo_de(time.time()) + 1)
    entrada = await cliente.post(
        "/api/entrar",
        json={"senha": senha, "codigo": codigo, "acesso": servico.janela.codigo()},
        headers=_de_fora(),
    )
    de_fora = (await entrada.json())["token"]
    relogio.avancar(JANELA_S - 60)
    assert (await cliente.get("/api/sessao", headers=_de_fora(de_fora))).status == 200
    relogio.avancar(120)
    # The window is over: the address is not even a host, and the session is gone with it,
    # so it will not open the next window either.
    assert (await cliente.get("/api/sessao", headers=_de_fora(de_fora))).status == 421
    servico.janela.armar()
    assert (await cliente.get("/api/sessao", headers=_de_fora(de_fora))).status == 401


async def test_um_segundo_fator_nao_e_trocado_por_quem_so_tem_sessao(porta, posse, senha, bearer):
    """Removing asks for the password; replacing goes through removing, or a session alone
    could move the factor to a phone of its own."""
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer)
    de_novo = await cliente.post("/api/otp", headers=bearer(token))
    assert de_novo.status == 409
    assert (await de_novo.json())["code"] == "otp_ja_ativo"
    assert cliente.app[CONFIG].valor.otp_segredo != ""
    fora = await cliente.delete("/api/otp", json={"senha": senha}, headers=bearer(token))
    assert fora.status == 200
    # With the factor gone every session went with it, so the owner logs in again to enrol.
    novo = (await (await cliente.post("/api/entrar", json={"senha": senha})).json())["token"]
    assert (await cliente.post("/api/otp", headers=bearer(novo))).status == 200


async def test_o_ambiente_manda_no_endereco_do_relay(aiohttp_client, amb, posse, bearer):
    """IPHUB_REMOTO_RELAY is the setting of the fleet; what config.json holds only answers
    for a box whose environment says nothing."""
    from dataclasses import replace as substituir

    from iphub.config import Config
    from iphub.config import salvar as salvar_config

    amb.dir_data.mkdir(parents=True, exist_ok=True)
    salvar_config(Config(remoto_relay=RELAY), amb.dir_data)
    servico = Servico(Janela(amb.dir_data), _Transporte(), intervalo_s=0.05)
    frota = substituir(amb, remoto_relay="https://frota.exemplo.com")
    cliente = await aiohttp_client(criar_app(frota, remoto=servico))
    token = await _preparar(cliente, posse, bearer)
    await cliente.post("/api/remoto", json={"armar": True}, headers=bearer(token))
    do_arquivo = await cliente.get("/api/sessao", headers=_de_fora(token))
    assert do_arquivo.status == 421
    da_frota = await cliente.get(
        "/api/sessao", headers={**_de_fora(token), "Host": "frota.exemplo.com"}
    )
    assert (await da_frota.json())["code"] == "otp_exigido"


async def test_a_situacao_do_segundo_fator_vive_na_rota_do_remoto(porta, posse, bearer):
    cliente, servico = porta
    token = await _preparar(cliente, posse, bearer, com_otp=False)
    assert (await cliente.get("/api/otp", headers=bearer(token))).status == 405
    situacao = await (await cliente.get("/api/remoto", headers=bearer(token))).json()
    assert situacao["otp_ativo"] is False
    assert "sem_otp" in situacao["faltando"]


async def test_sem_o_home_do_app_a_porta_remota_nem_existe(porta, posse, bearer):
    """The door is a service of the maker for the installations it registers, and what
    names an installation is the home of the app: without it the panel hides the door and
    the app cannot arm it either."""
    from iphub.api.comum import aplicar_dp
    from iphub.dpbus import mapa, protocolo

    cliente, servico = porta
    token = await posse(cliente)
    situacao = await (await cliente.get("/api/remoto", headers=bearer(token))).json()
    assert situacao["disponivel"] is False
    assert situacao["home"] == ""
    assert situacao["faltando"][0] == "sem_home"
    await cliente.post("/api/licencas", json=LICENCA, headers=bearer(token))
    assert await aplicar_dp(cliente.app, "av1", mapa.DP_REMOTO, True) == protocolo.VALOR_INVALIDO
    assert servico.janela.ativa() is False


async def test_o_home_e_escrito_pelo_app_e_so_aceita_um_numero(porta, posse, bearer):
    from iphub.api.comum import aplicar_dp, valores_dps
    from iphub.dpbus import mapa, protocolo

    cliente, servico = porta
    token = await posse(cliente)
    await cliente.post("/api/licencas", json=LICENCA, headers=bearer(token))
    for errado in ("", "abc", "12 34", "1' or 1=1", "1" * 21, 123456, True):
        assert await aplicar_dp(cliente.app, "av1", mapa.DP_REMOTO_HOME, errado) == (
            protocolo.VALOR_INVALIDO
        ), errado
    assert await aplicar_dp(cliente.app, "av1", mapa.DP_REMOTO_HOME, " 123456 ") is None
    assert valores_dps(cliente.app, "av1")[mapa.DP_REMOTO_HOME] == "123456"
    situacao = await (await cliente.get("/api/remoto", headers=bearer(token))).json()
    assert situacao["disponivel"] is True
    assert situacao["home"] == "123456"
    assert "sem_home" not in situacao["faltando"]
    # It survives a restart: the miniApp writes it once.
    from iphub.config import carregar

    assert (
        carregar(
            cliente.app[__import__("iphub.api.comum", fromlist=["AMBIENTE"]).AMBIENTE].dir_data
        ).remoto_home
        == "123456"
    )
