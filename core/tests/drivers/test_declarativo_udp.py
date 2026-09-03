# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The declarative engine over UDP, against a simulated screen relay, section 12: no hardware.

UDP is the transport with no promises: one datagram out, one in, and the deadline is the only
thing that ends a wait. So the rules attacked here are the ones a datagram makes easy to break.
There is no retransmission the file did not declare, because a relay that closes a contact
twice closes it twice. A hexadecimal literal is bytes and carries no terminator and no
substitution, because half a byte on the wire is a command no device answers. And a datagram
larger than the ceiling is dropped instead of held, because nothing on the LAN gets to grow
the memory of the daemon.

O motor declarativo sobre UDP, contra um relé de tela simulado, seção 12: sem hardware.

UDP é o transporte sem promessas: um datagrama para fora, um para dentro, e o prazo é a única
coisa que termina uma espera. Então as regras atacadas aqui são as que um datagrama facilita
quebrar. Não existe retransmissão que o arquivo não declarou, porque um relé que fecha um
contato duas vezes o fecha duas vezes. Um literal hexadecimal é byte e não leva terminador nem
substituição, porque meio byte no fio é um comando que aparelho nenhum responde. E um datagrama
maior que o teto é descartado em vez de guardado, porque nada na LAN pode crescer a memória do
daemon.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from iphub.drivers.base import Driver
from iphub.drivers.catalogo import PASTA_EMBARCADA
from iphub.drivers.declarativo import transporte
from iphub.drivers.declarativo.formato import validar
from iphub.drivers.declarativo.motor import construir
from iphub.drivers.manifesto import Estado
from iphub.drivers.simulado import ServidorDatagrama

ESPERA_MAXIMA_S = 2.0
ESCALA_MAXIMA = 79
RESPOSTAS = {
    b"PWR?\r": b"PWR ON\r",
    b"VOL?\r": b"VOL 40\r",
    b"SRC?\r": b"SRC NET\r",
}
QUADRO_SOBE = bytes.fromhex("FFEEEE01")


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "uuid-da-tela"
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)


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


def _declaracao(porta: int, **mudancas) -> dict:
    """An amplifier answering over UDP the same text lines its serial port takes.

    Um amplificador respondendo por UDP as mesmas linhas de texto da porta serial dele.
    """
    arquivo = {
        "manifesto": {
            "tipo": "amplificador_de_teste_udp",
            "rotulo": {"pt": "Amplificador UDP de teste", "en": "Test UDP amplifier"},
            "categoria": "audio",
            "capacidades": ["ligar", "desligar", "volume", "fonte"],
        },
        "transporte": {
            "udp": {
                "porta": porta,
                "terminador": "\r",
                "timeout_s": 0.5,
                "intervalo_min_ms": 0,
            }
        },
        "comandos": {
            "ligar": {"envia": "PWR ON"},
            "desligar": {"envia": "PWR OFF"},
            "volume": {"envia": "VOL {valor_escala}"},
            "fonte": {
                "envia": "SRC {valor}",
                "valores": {"Streaming": "NET", "Bluetooth": "BT"},
            },
        },
        "estado": {
            "pede": [{"envia": "PWR?"}, {"envia": "VOL?"}, {"envia": "SRC?"}],
            "le": {
                "ligado": {"regex": "PWR (ON|OFF)", "verdadeiro": "ON"},
                "volume": {"regex": "VOL ([0-9]{1,3})"},
                "fonte": {"regex": "SRC (NET|BT)"},
            },
        },
        "escala_volume": {"min": 0, "max": ESCALA_MAXIMA},
    }
    return {**arquivo, **mudancas}


def _tela(porta: int) -> dict:
    """A screen relay: hexadecimal frames, no state to ask and nothing to read back.

    It declares the terminator its text commands would use, which is what makes the rule
    visible: the frames below are bytes and take none of it.

    Um relé de tela: quadros hexadecimais, sem estado a perguntar e sem nada a ler de volta.

    Ele declara o terminador que os comandos de texto dele usariam, que é o que torna a regra
    visível: os quadros abaixo são byte e não levam nada dele.
    """
    return {
        "manifesto": {
            "tipo": "tela_de_teste",
            "rotulo": {"pt": "Tela de projecao de teste", "en": "Test projection screen"},
            "categoria": "outro",
            "capacidades": ["ligar", "desligar"],
        },
        "transporte": {"udp": {"porta": porta, "terminador": "\r", "timeout_s": 0.5}},
        "comandos": {
            "ligar": {"envia": "FF EE EE 01", "hex": True},
            "desligar": {"envia": "FFEEEE02", "hex": True, "repete": 2, "intervalo_ms": 10},
        },
    }


async def _no_fio(aparelho: ServidorDatagrama, quantos: int) -> list[bytes]:
    """The datagrams the device got, once it got them: a command is sent and never read back,
    so the test waits for the device instead of guessing when it was served.

    Os datagramas que o aparelho recebeu, quando ele os recebeu: um comando é mandado e nunca
    lido de volta, então o teste espera pelo aparelho em vez de adivinhar quando ele foi
    servido.
    """
    laco = asyncio.get_running_loop()
    fim = laco.time() + ESPERA_MAXIMA_S
    while len(aparelho.recebidos) < quantos and laco.time() < fim:
        await asyncio.sleep(0.005)
    return aparelho.recebidos


def _driver(aparelho: ServidorDatagrama, dados: dict | None = None, *, cadastro=None) -> Driver:
    dados = _declaracao(aparelho.endereco[1]) if dados is None else dados
    classe = construir(validar(dados, regex=Fogo()), regex=Regex())
    return classe(cadastro or _Cadastro(ip=aparelho.endereco[0]))


async def test_o_poll_pergunta_uma_vez_cada_e_devolve_um_Estado_tipado():
    """One datagram out and one in per question: no retransmission was declared, so none happens.

    Um datagrama para fora e um para dentro por pergunta: nenhuma retransmissão foi declarada,
    então nenhuma acontece.
    """
    async with ServidorDatagrama(RESPOSTAS) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert isinstance(estado, Estado)
    assert estado.online is True
    assert estado.ligado is True
    assert estado.volume == 51
    assert estado.fonte == "Streaming"
    assert aparelho.recebidos == [b"PWR?\r", b"VOL?\r", b"SRC?\r"]


async def test_o_comando_leva_o_terminador_declarado_e_o_valor_do_mapa():
    async with ServidorDatagrama(RESPOSTAS) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        assert await driver.executar("fonte", "Bluetooth") is None
        assert await driver.executar("volume", 100) is None
        assert await _no_fio(aparelho, 2) == [b"SRC BT\r", f"VOL {ESCALA_MAXIMA}\r".encode()]
        await driver.parar()


async def test_o_literal_hexadecimal_vai_como_bytes_sem_terminador():
    """A hexadecimal literal is a whole frame, and one byte appended is a frame nobody answers.

    Um literal hexadecimal é um quadro inteiro, e um byte colado nele é quadro que ninguém
    responde.
    """
    async with ServidorDatagrama({}) as aparelho:
        driver = _driver(aparelho, _tela(aparelho.endereco[1]))
        await driver.iniciar()
        assert await driver.executar("ligar") is None
        assert await _no_fio(aparelho, 1) == [QUADRO_SOBE]
        await driver.parar()


async def test_a_repeticao_declarada_manda_o_quadro_a_vezes():
    """The infrared bridge and the relay of section 7: the repetition is data, never a loop.

    A ponte de infravermelho e o relé da seção 7: a repetição é dado, nunca um laço.
    """
    async with ServidorDatagrama({}) as aparelho:
        driver = _driver(aparelho, _tela(aparelho.endereco[1]))
        await driver.iniciar()
        assert await driver.executar("desligar") is None
        assert await _no_fio(aparelho, 2) == [bytes.fromhex("FFEEEE02")] * 2
        await driver.parar()


async def test_o_intervalo_declarado_no_comando_separa_as_repeticoes_no_fio():
    """A relay repeated with no pause is a relay that got one frame and dropped the rest, so
    the interval the file declares next to the repetition has to reach the wire; the transport
    declares no minimum here, so the command is the only thing that can hold a frame back.

    Um relé repetido sem pausa é um relé que pegou um quadro e perdeu o resto, então o
    intervalo que o arquivo declara ao lado da repetição precisa chegar ao fio; o transporte
    não declara mínimo aqui, então o comando é a única coisa que pode segurar um quadro.
    """
    intervalo_ms = 60
    repeticoes = 3
    async with ServidorDatagrama({}) as aparelho:
        dados = _tela(aparelho.endereco[1])
        dados["comandos"]["desligar"]["intervalo_ms"] = intervalo_ms
        dados["comandos"]["desligar"]["repete"] = repeticoes
        assert "intervalo_min_ms" not in dados["transporte"]["udp"]
        driver = _driver(aparelho, dados)
        await driver.iniciar()
        comeco = asyncio.get_running_loop().time()
        assert await driver.executar("desligar") is None
        gasto = asyncio.get_running_loop().time() - comeco
        assert await _no_fio(aparelho, repeticoes) == [bytes.fromhex("FFEEEE02")] * repeticoes
        await driver.parar()
    # Three frames are two intervals: the first one has nothing before it to wait for.
    # Três quadros são dois intervalos: o primeiro não tem nada antes dele para esperar.
    assert gasto >= (repeticoes - 1) * intervalo_ms / 1000


async def test_sem_bloco_de_estado_o_hub_nao_inventa_leitura_nem_alarme():
    """A file with nothing to ask leaves the hub with nothing to say, and a false offline
    would hide every fire and forget equipment behind an alarm nobody can clear.

    Um arquivo sem o que perguntar deixa o hub sem o que dizer, e um offline falso esconderia
    todo equipamento de mão única atrás de um alarme que ninguém limpa.
    """
    async with ServidorDatagrama({}) as aparelho:
        driver = _driver(aparelho, _tela(aparelho.endereco[1]))
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.detalhe == ""
    assert (estado.ligado, estado.volume, estado.fonte) == (None, None, None)
    assert aparelho.recebidos == []


async def test_o_aparelho_que_nunca_responde_deixa_o_equipamento_offline():
    """The deadline is the only thing that ends a wait for a datagram that never comes.

    O prazo é a única coisa que termina a espera por um datagrama que nunca vem.
    """
    async with ServidorDatagrama({}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    # The poll stopped at the first question instead of paying the deadline three times.
    # O poll parou na primeira pergunta em vez de pagar o prazo três vezes.
    assert aparelho.recebidos == [b"PWR?\r"]


async def test_o_datagrama_maior_que_o_teto_e_descartado_e_nao_guardado():
    """Nothing on the LAN gets to grow the memory of the daemon, section 9 in spirit.

    Nada na LAN pode crescer a memória do daemon, espírito da seção 9.
    """
    gigante = b"PWR ON " + b"z" * (transporte.DATAGRAMA_MAXIMO + 1) + b"\r"
    async with ServidorDatagrama({b"PWR?\r": gigante}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_o_aparelho_que_responde_lixo_segue_online_e_nao_inventa_leitura():
    respostas = {b"PWR?\r": b"\x00\x1b[31m???\r", b"VOL?\r": b"VOL cheio\r", b"SRC?\r": b"\r"}
    async with ServidorDatagrama(respostas) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is True
    # A word where a number belongs is not a volume of zero: it is a reading that did not happen.
    # Uma palavra onde cabe número não é volume zero: é uma leitura que não aconteceu.
    assert (estado.ligado, estado.volume, estado.fonte) == (None, None, None)


async def test_o_valor_fora_do_mapa_e_a_acao_fora_do_manifesto_nao_viram_datagrama():
    async with ServidorDatagrama({}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        assert await driver.executar("fonte", "Disco de vinil") == "invalid_value"
        assert await driver.executar("mudo", True) == "nao_suportado"
        await driver.parar()
    assert aparelho.recebidos == []


async def test_o_endereco_que_nao_e_ip_literal_nunca_vira_datagrama():
    """Section 9: the hub resolves nothing, so it never becomes a proxy into the LAN.

    Seção 9: o hub não resolve nada, então nunca vira um proxy para a LAN.
    """
    async with ServidorDatagrama(RESPOSTAS) as aparelho:
        for endereco in ("localhost", "tela.local", "127.0.0.1:5000", ""):
            driver = _driver(aparelho, cadastro=_Cadastro(ip=endereco))
            await driver.iniciar()
            await driver.atualizar()
            assert await driver.executar("ligar") == "eq_offline"
            await driver.parar()
            assert driver.estado().online is False
    assert aparelho.recebidos == []


def _embarcado(nome: str) -> dict:
    """One example of the catalog that ships in the image, read from the file itself.

    Um exemplo do catálogo que embarca na imagem, lido do próprio arquivo.
    """
    return json.loads((Path(PASTA_EMBARCADA) / nome).read_text(encoding="utf-8"))


async def test_o_exemplo_embarcado_de_udp_fala_com_o_aparelho_simulado():
    """The exit gate of milestone 3: the file that ships is the file that was driven.

    O portão de saída do marco 3: o arquivo que embarca é o arquivo que foi dirigido.
    """
    respostas = {
        b"PWR?\r": b"PWR ON\r",
        b"VOL?\r": b"VOL 40\r",
        b"SRC?\r": b"SRC BT\r",
    }
    async with ServidorDatagrama(respostas) as aparelho:
        dados = _embarcado("amplificador_udp.json")
        dados["transporte"]["udp"]["porta"] = aparelho.endereco[1]
        classe = construir(validar(dados, regex=Fogo()), regex=Regex())
        driver = classe(_Cadastro(ip=aparelho.endereco[0]))
        await driver.iniciar()
        await driver.atualizar()
        lido = driver.estado()
        assert await driver.executar("volume", 50) is None
        assert await _no_fio(aparelho, 4) == [b"PWR?\r", b"VOL?\r", b"SRC?\r", b"VOL 40\r"]
        await driver.parar()
    assert lido.online is True
    assert lido.ligado is True
    # 40 of a device that goes to 79 is what the contract of section 6 writes as 51, and the
    # 50 of the panel is the 40 that went back on the wire.
    # 40 de um aparelho que vai até 79 é o que o contrato da seção 6 escreve como 51, e o 50
    # do painel é o 40 que voltou ao fio.
    assert lido.volume == 51
    assert lido.fonte == "Bluetooth"
    assert driver.estado().volume == 50
