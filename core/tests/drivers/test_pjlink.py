# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The example native driver against a simulated projector, section 12: no hardware.

The poll of one atualizar, and what it reads, has a file of its own beside this one.

O poll de um atualizar, e o que ele lê, tem arquivo próprio ao lado deste.

The tests attack the contract of section 6 as much as they exercise the protocol: an action
outside the capabilities must never reach the socket, a value must never carry a second
command onto the wire, and no answer of a device may leave an exception loose in the daemon.

O driver nativo de exemplo contra um projetor simulado, seção 12: sem hardware.

Os testes atacam o contrato da seção 6 tanto quanto exercitam o protocolo: uma ação fora das
capacidades nunca pode chegar ao socket, um valor nunca pode levar um segundo comando ao
fio, e nenhuma resposta de aparelho pode deixar exceção solta no daemon.
"""

import hashlib
from dataclasses import dataclass, field

import pytest

from iphub.drivers import catalogo
from iphub.drivers.base import CODIGOS, RESULTADOS
from iphub.drivers.manifesto import Auth, Estado, TipoCampo, validar
from iphub.drivers.nativos import pjlink
from iphub.drivers.nativos.pjlink import PJLink, porta_de
from iphub.drivers.simulado import ServidorLinha

PRAZO_DE_TESTE_S = 0.3
ORCAMENTO_DE_TESTE_S = 0.45
SEMENTE = "498e4a67"
SENHA = "JBMIAProjectorLink"
# The digest the published protocol gives for this seed and this password, written by hand so
# a change of the algorithm inside the driver cannot agree with itself.
# O digesto que o protocolo publicado dá para esta semente e esta senha, escrito na mão para
# uma mudança do algoritmo dentro do driver não concordar consigo mesma.
DIGESTO_DO_MANUAL = "5d8409bc1c3fa39749434aa3a5c38682"
SAUDACAO_ABERTA = b"PJLINK 0\r"
SAUDACAO_SEGURA = f"PJLINK 1 {SEMENTE}\r".encode("ascii")

POLL_LIGADO = {
    b"%1POWR ?": b"%1POWR=1\r",
    b"%1INST ?": b"%1INST=11 31 32\r",
    b"%1INPT ?": b"%1INPT=31\r",
    b"%1AVMT ?": b"%1AVMT=30\r",
}
ENERGIA_OK = {b"%1POWR 1": b"%1POWR=OK\r", b"%1POWR 0": b"%1POWR=OK\r"}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "uuid-do-projetor"
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)


@pytest.fixture(autouse=True)
def prazo_curto(monkeypatch):
    """A device that ignores a line is answered by the deadline, and no suite waits 4 s.

    Um aparelho que ignora uma linha é respondido pelo prazo, e nenhuma suíte espera 4 s.
    """
    monkeypatch.setattr(pjlink, "TEMPO_LIMITE_S", PRAZO_DE_TESTE_S)
    monkeypatch.setattr(pjlink, "ORCAMENTO_DO_POLL_S", ORCAMENTO_DE_TESTE_S)


def _digesto(senha: str) -> bytes:
    """The digest as the protocol defines it, computed here and never asked of the driver.

    O digesto como o protocolo o define, calculado aqui e nunca pedido ao driver.
    """
    return hashlib.md5(f"{SEMENTE}{senha}".encode()).hexdigest().encode("ascii")


def _seguras(senha: str, respostas: dict[bytes, bytes]) -> dict[bytes, bytes]:
    prefixo = _digesto(senha)
    return {prefixo + linha: resposta for linha, resposta in respostas.items()}


def _driver(aparelho: ServidorLinha, *, senha: str = "") -> PJLink:
    anfitriao, porta = aparelho.endereco
    return PJLink(_Cadastro(ip=anfitriao, campos={"porta": str(porta)}, segredos={"senha": senha}))


def test_o_manifesto_e_valido_e_nao_promete_volume():
    """Section 6: what class 1 cannot do is omitted, never implemented to refuse.

    Seção 6: o que a classe 1 não faz é omitido, nunca implementado para recusar.
    """
    manifesto = PJLink.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.tipo == "projetor_pjlink"
    assert manifesto.categoria == "projetor"
    assert manifesto.motor == "nativo"
    assert manifesto.capacidades == ("ligar", "desligar", "fonte", "mudo")
    assert "volume" not in manifesto.capacidades
    assert manifesto.auth is Auth.CODIGO
    exigidas = {"descricao", "campo_porta", "campo_senha", "auth_ajuda"}
    for idioma in ("pt", "en"):
        assert set(manifesto.textos[idioma]) == exigidas


def test_a_senha_e_um_campo_segredo_e_o_ip_nao_e_campo():
    campos = {campo.nome: campo for campo in PJLink.MANIFESTO.config_campos}
    assert campos["senha"].tipo is TipoCampo.SEGREDO
    # Why: security off means no password, and demanding one would block a working setup.
    # Por que: segurança desligada é sem senha, e exigir uma travaria um cadastro que serve.
    assert campos["senha"].obrigatorio is False
    assert campos["porta"].padrao == "4352"
    assert "ip" not in campos


def test_os_codigos_do_driver_sao_os_estaveis_do_contrato():
    """A code invented here would be a phrase the panel cannot translate, section 11.

    Um código inventado aqui seria uma frase que o painel não traduz, seção 11.
    """
    usados = {
        pjlink.EQ_OFFLINE,
        pjlink.INVALID_VALUE,
        pjlink.AUTH_PENDENTE,
        pjlink.ERRO_APARELHO,
        *pjlink.CODIGO_POR_ERRO.values(),
    }
    assert usados <= set(CODIGOS)
    assert pjlink.FALHOU in RESULTADOS


def test_o_catalogo_encontra_o_pjlink_sem_lista_na_mao():
    catalogo.esquecer()
    try:
        assert catalogo.carregar()["projetor_pjlink"] is PJLink
    finally:
        catalogo.esquecer()


async def test_sem_seguranca_o_comando_vai_sem_digesto():
    async with ServidorLinha(ENERGIA_OK, saudacao=SAUDACAO_ABERTA) as aparelho:
        assert await _driver(aparelho).executar("ligar") is None
        assert aparelho.recebidas == [b"%1POWR 1"]


async def test_com_seguranca_o_comando_leva_o_digesto_md5_da_semente_e_da_senha():
    respostas = _seguras(SENHA, ENERGIA_OK)
    async with ServidorLinha(respostas, saudacao=SAUDACAO_SEGURA) as aparelho:
        assert await _driver(aparelho, senha=SENHA).executar("ligar") is None
        assert aparelho.recebidas == [DIGESTO_DO_MANUAL.encode("ascii") + b"%1POWR 1"]


@pytest.mark.parametrize(
    ("saudacao", "respostas", "senha", "esperado"),
    [
        (SAUDACAO_ABERTA, {b"%1POWR ?": b"%1POWR=0\r"}, "", "pareado"),
        (SAUDACAO_SEGURA, _seguras(SENHA, {b"%1POWR ?": b"%1POWR=0\r"}), SENHA, "pareado"),
        (SAUDACAO_SEGURA, _seguras("errada", {b"%1POWR ?": b"PJLINK ERRA\r"}), "errada", "falhou"),
        # A busy projector answered PJLink, so the credential was accepted.
        # Um projetor ocupado respondeu PJLink, então a credencial foi aceita.
        (SAUDACAO_SEGURA, _seguras(SENHA, {b"%1POWR ?": b"%1POWR=ERR3\r"}), SENHA, "pareado"),
        (SAUDACAO_ABERTA, {}, "", "falhou"),
    ],
)
async def test_autenticar_responde_pareado_ou_falhou_e_nunca_aguardando(
    saudacao, respostas, senha, esperado
):
    """Section 6 and section 14: this protocol has no popup on the device, so it never waits.

    Seção 6 e seção 14: este protocolo não tem popup no aparelho, então ele nunca aguarda.
    """
    async with ServidorLinha(respostas, saudacao=saudacao) as aparelho:
        resultado = await _driver(aparelho, senha=senha).autenticar()
    assert resultado == esperado
    assert resultado in RESULTADOS
    assert resultado != "aguardando"


async def test_autenticar_de_aparelho_fora_do_ar_responde_falhou():
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
    assert await driver.autenticar() == "falhou"


async def test_ligar_desligar_e_o_estado_lido_de_volta():
    async with ServidorLinha(ENERGIA_OK | POLL_LIGADO, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("ligar") is None
        assert driver.estado().ligado is True
        await driver.atualizar()
        assert driver.estado() == Estado(
            online=True, ligado=True, fonte="31", fontes=("11", "31", "32"), mudo=False
        )
        assert await driver.executar("desligar") is None
        assert driver.estado().ligado is False
        assert b"%1POWR 0" in aparelho.recebidas


async def test_fonte_troca_no_aparelho_e_no_estado():
    respostas = {b"%1INPT 32": b"%1INPT=OK\r"}
    async with ServidorLinha(respostas, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("fonte", "32") is None
        assert aparelho.recebidas == [b"%1INPT 32"]
    assert driver.estado().fonte == "32"


async def test_fonte_fora_da_lista_do_aparelho_e_invalid_value():
    async with ServidorLinha(POLL_LIGADO, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
        antes = len(aparelho.recebidas)
        assert await driver.executar("fonte", "21") == "invalid_value"
        assert len(aparelho.recebidas) == antes


@pytest.mark.parametrize("valor", ["31\r%1POWR 1", "3", "60", "10", "", "ab", 31, None, True])
async def test_valor_de_fonte_recusado_nunca_chega_ao_fio(valor):
    """The value decides bytes on a socket: a terminator inside it would be a second command.

    O valor decide bytes num socket: um terminador dentro dele seria um segundo comando.
    """
    async with ServidorLinha({}, saudacao=SAUDACAO_ABERTA) as aparelho:
        assert await _driver(aparelho).executar("fonte", valor) == "invalid_value"
        assert aparelho.recebidas == []
        assert aparelho.conexoes == 0


@pytest.mark.parametrize(
    ("ligado", "comando", "leitura", "esperado"),
    [
        (True, b"%1AVMT 21", b"%1AVMT=21\r", True),
        # 31 blanks the picture as well, which the driver never asks for, but it does mute.
        # 31 apaga a imagem também, o que o driver nunca pede, mas ele muda mesmo assim.
        (False, b"%1AVMT 20", b"%1AVMT=31\r", True),
        (False, b"%1AVMT 20", b"%1AVMT=11\r", False),
        (False, b"%1AVMT 20", b"%1AVMT=20\r", False),
        (False, b"%1AVMT 20", b"%1AVMT=30\r", False),
    ],
)
async def test_mudo_manda_o_codigo_certo_e_le_o_de_volta(ligado, comando, leitura, esperado):
    respostas = {comando: b"%1AVMT=OK\r", **POLL_LIGADO, b"%1AVMT ?": leitura}
    async with ServidorLinha(respostas, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("mudo", ligado) is None
        assert comando in aparelho.recebidas
        assert driver.estado().mudo is ligado
        await driver.atualizar()
    assert driver.estado().mudo is esperado


async def test_o_mudo_do_painel_nunca_apaga_a_imagem_do_projetor():
    """Section 6 mudo is the audio one: 31 and 30 blank the picture, and nobody asked for that.

    O mudo da seção 6 é o de áudio: 31 e 30 apagam a imagem, e ninguém pediu isso.
    """
    respostas = {b"%1AVMT 21": b"%1AVMT=OK\r", b"%1AVMT 20": b"%1AVMT=OK\r"}
    async with ServidorLinha(respostas, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar("mudo", True) is None
        assert await driver.executar("mudo", False) is None
    assert aparelho.recebidas == [b"%1AVMT 21", b"%1AVMT 20"]


def test_o_texto_do_manifesto_diz_que_o_mudo_e_de_audio():
    """Section 6: what the panel shows about the driver comes from textos, in both languages."""
    descricoes = PJLink.MANIFESTO.textos
    assert "audio mute" in descricoes["en"]["descricao"]
    assert "mudo de áudio" in descricoes["pt"]["descricao"]


@pytest.mark.parametrize("valor", ["true", 1, 0, None, "sim"])
async def test_mudo_com_valor_que_nao_e_booleano_e_invalid_value(valor):
    async with ServidorLinha({}, saudacao=SAUDACAO_ABERTA) as aparelho:
        assert await _driver(aparelho).executar("mudo", valor) == "invalid_value"
        assert aparelho.conexoes == 0


@pytest.mark.parametrize(
    ("resposta", "codigo"),
    [
        (b"%1POWR=ERR1\r", "invalid_value"),
        (b"%1POWR=ERR2\r", "invalid_value"),
        (b"%1POWR=ERR3\r", "eq_offline"),
        (b"%1POWR=ERR4\r", "erro_aparelho"),
        (b"PJLINK ERRA\r", "auth_pendente"),
        # A device out of step with us: nothing it says serves this exchange.
        # Um aparelho fora de passo conosco: nada do que ele diz serve para esta troca.
        (b"%1INPT=31\r", "erro_aparelho"),
        (b"%1POWR=QUALQUER\r", "erro_aparelho"),
    ],
)
async def test_cada_resposta_de_erro_vira_um_codigo_estavel(resposta, codigo):
    async with ServidorLinha({b"%1POWR 1": resposta}, saudacao=SAUDACAO_ABERTA) as aparelho:
        assert await _driver(aparelho).executar("ligar") == codigo


@pytest.mark.parametrize("saudacao", [b"OLA\r", b"PJLINK 1 zznaohex\r", b"PJLINK\r", b"\r"])
async def test_saudacao_que_nao_e_do_protocolo_e_erro_aparelho_sem_mandar_comando(saudacao):
    async with ServidorLinha(ENERGIA_OK, saudacao=saudacao) as aparelho:
        assert await _driver(aparelho).executar("ligar") == "erro_aparelho"
        assert aparelho.recebidas == []


async def test_aparelho_que_nao_responde_e_eq_offline_e_nunca_uma_excecao():
    async with ServidorLinha({}, saudacao=SAUDACAO_ABERTA) as aparelho:
        assert await _driver(aparelho).executar("ligar") == "eq_offline"


async def test_porta_fechada_e_eq_offline():
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
    assert await driver.executar("ligar") == "eq_offline"


async def test_resposta_gigante_e_cortada_em_vez_de_encher_a_memoria():
    """A device on the LAN must not be able to make the daemon buffer without bound.

    Um aparelho na LAN não pode fazer o daemon acumular sem limite.
    """
    enorme = b"x" * (pjlink.LINHA_MAXIMA * 4) + b"\r"
    async with ServidorLinha({b"%1POWR 1": enorme}, saudacao=SAUDACAO_ABERTA) as aparelho:
        assert await _driver(aparelho).executar("ligar") == "eq_offline"


async def test_cadastro_sem_ip_nunca_abre_conexao():
    """The hub only talks to an address somebody registered, never to a resolver default.

    O hub só fala com um endereço que alguém cadastrou, nunca com o padrão do resolvedor.
    """
    async with ServidorLinha(ENERGIA_OK, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = PJLink(_Cadastro(ip="", campos={"porta": str(aparelho.endereco[1])}))
        assert await driver.executar("ligar") == "eq_offline"
        await driver.atualizar()
        assert aparelho.conexoes == 0
    assert driver.estado().online is False


@pytest.mark.parametrize("acao", ["volume", "tocar", "pausar", "agrupar", "formatar_o_disco"])
async def test_acao_fora_das_capacidades_nunca_chega_ao_soquete(acao):
    """Section 6: the driver never implements a method only to refuse, and never dials out.

    Seção 6: o driver nunca implementa método só para recusar, e nunca disca para fora.
    """
    async with ServidorLinha(ENERGIA_OK, saudacao=SAUDACAO_ABERTA) as aparelho:
        assert await _driver(aparelho).executar(acao, 50) == "nao_suportado"
        assert aparelho.conexoes == 0
        assert aparelho.recebidas == []


async def test_o_driver_nao_guarda_conexao_entre_comandos():
    """A class 1 device usually accepts one connection: holding it would lock out the remote.

    Um aparelho classe 1 costuma aceitar uma conexão: segurá-la trancaria o controle remoto.
    """
    async with ServidorLinha(ENERGIA_OK, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        assert aparelho.conexoes == 0
        await driver.executar("ligar")
        await driver.executar("desligar")
        assert aparelho.conexoes == 2
        await driver.parar()


@pytest.mark.parametrize(
    ("campos", "esperado"),
    [
        ({}, 4352),
        ({"porta": "5000"}, 5000),
        ({"porta": " 4352 "}, 4352),
        ({"porta": "0"}, 4352),
        ({"porta": "70000"}, 4352),
        ({"porta": "-1"}, 4352),
        ({"porta": "abc"}, 4352),
        ({"porta": ""}, 4352),
        # Unicode digits: str.isdigit takes them, and int then raises or reads another port.
        # Dígitos Unicode: str.isdigit os toma, e o int então estoura ou lê outra porta.
        ({"porta": "\u00b2"}, 4352),
        ({"porta": "\u00b9\u00b2"}, 4352),
        ({"porta": "\u0661\u0662\u0663"}, 4352),
        ({"porta": "\uff11\uff12\uff13"}, 4352),
    ],
)
def test_a_porta_do_cadastro_manda_e_o_padrao_cobre_o_resto(campos, esperado):
    assert porta_de(campos) == esperado
