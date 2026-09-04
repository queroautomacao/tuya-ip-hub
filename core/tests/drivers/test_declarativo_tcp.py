# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The declarative engine over TCP, against a simulated video matrix, section 12: no hardware.

The tests attack sections 6 and 7 as much as they exercise the transport. A declaration is
DATA: what it says becomes bytes on a wire and never a decision taken in runtime. So a value
outside the declared map never reaches the socket, a value carrying a carriage return never
becomes two commands, an action the manifest does not declare never touches the device, an
address that is not an IP literal never leaves the daemon, and no answer of a device, however
long or however broken, leaves an exception loose in the poll.

The bench fact this file guards: ONE TCP session at a time per equipment, because a matrix
accepts a single connection and the command of the integrator lands inside the poll window.

O motor declarativo sobre TCP, contra uma matriz de video simulada, seção 12: sem hardware.

Os testes atacam as seções 6 e 7 tanto quanto exercitam o transporte. Uma declaração é DADO: o
que ela diz vira bytes num fio e nunca decisão tomada em runtime. Então um valor fora do mapa
declarado nunca chega ao socket, um valor levando um retorno de carro nunca vira dois comandos,
uma ação que o manifesto não declara nunca toca o aparelho, um endereço que não é IP literal
nunca sai do daemon, e nenhuma resposta de aparelho, por mais longa ou quebrada, deixa exceção
solta no poll.

O fato de bancada que este arquivo guarda: UMA sessão TCP por vez por equipamento, porque uma
matriz aceita uma conexão só e o comando do integrador cai dentro da janela do poll.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from iphub import regex_seguro
from iphub.config import Cadastro
from iphub.drivers.base import CODIGOS, DETALHES, Driver
from iphub.drivers.declarativo import transporte
from iphub.drivers.declarativo.formato import validar
from iphub.drivers.declarativo.motor import construir
from iphub.drivers.gestor import Gestor
from iphub.drivers.manifesto import Estado
from iphub.drivers.manifesto import validar as validar_manifesto
from iphub.drivers.simulado import ServidorLinha

# Why: the three examples of milestone 3 prove the engine and never ship, so they live with
# the tests and not in the catalogue of the image.
# Por que: os três exemplos do marco 3 provam o motor e nunca embarcam, então vivem com os
# testes e não no catálogo da imagem.
EXEMPLOS = Path(__file__).resolve().parent / "exemplos"

ESPERA_MAXIMA_S = 2.0
ESTADO_LIGADO = b"POWER ON VS IN2 VOL 30\r"
POLL_EM_TRES = {
    "pede": [{"envia": "GET POWER"}, {"envia": "GET VS"}, {"envia": "GET VOL"}],
    "le": {
        "ligado": {"regex": "POWER (ON|OFF)", "verdadeiro": "ON"},
        "fonte": {"regex": "VS (IN[1-8])"},
        "volume": {"regex": "VOL ([0-9]{1,3})"},
    },
}
RESPOSTAS_EM_TRES = {
    b"GET POWER": b"POWER ON\r",
    b"GET VS": b"VS IN2\r",
    b"GET VOL": b"VOL 30\r",
}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "uuid-da-matriz"
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

    def __init__(self, *, morre: bool = False) -> None:
        self.perguntou: list[tuple[str, str]] = []
        self._morre = morre

    async def buscar_async(self, padrao: str, texto: str) -> list[str | None] | None:
        self.perguntou.append((padrao, texto))
        # None is what the worker answers for a blown deadline or a pattern `re` refused.
        # None é o que o trabalhador responde para prazo estourado ou padrão que o `re` recusou.
        if self._morre:
            return None
        casamento = re.search(padrao, texto)
        return list(casamento.groups()) if casamento else []


class AparelhoPartido:
    """A device with no terminator that answers in pieces, which is what a slow one really does.

    The simulated device of the suite answers one whole line at a time, so it cannot show what
    a device that pauses in the middle of its answer does to a driver that reads once. Each
    entry of respostas is the answer to one question, split into the segments it arrives in.

    Um aparelho sem terminador que responde em pedaços, que é o que um aparelho lento faz.

    O aparelho simulado da suíte responde uma linha inteira por vez, então ele não mostra o que
    um aparelho que para no meio da resposta faz com um driver que lê uma vez só. Cada entrada
    de respostas é a resposta de uma pergunta, partida nos segmentos em que ela chega.
    """

    def __init__(self, respostas: tuple[tuple[bytes, ...], ...], *, pausa_s: float = 0.05) -> None:
        self.respostas = respostas
        self.pausa_s = pausa_s
        self.recebidas: list[bytes] = []
        self.endereco = ("", 0)
        self._servidor: asyncio.Server | None = None

    async def __aenter__(self):
        self._servidor = await asyncio.start_server(self._atender, "127.0.0.1", 0)
        self.endereco = self._servidor.sockets[0].getsockname()[:2]
        return self

    async def __aexit__(self, *_erro: object) -> None:
        servidor, self._servidor = self._servidor, None
        if servidor is not None:
            servidor.close()

    async def _atender(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            for segmentos in self.respostas:
                pedido = await leitor.read(transporte.LINHA_MAXIMA)
                if not pedido:
                    return
                self.recebidas.append(pedido)
                for indice, segmento in enumerate(segmentos):
                    if indice:
                        await asyncio.sleep(self.pausa_s)
                    escritor.write(segmento)
                    await escritor.drain()
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            escritor.close()


def _declaracao(porta: int, **mudancas) -> dict:
    """A video matrix over TCP: power, the input routed to output 1, volume and free text.

    Uma matriz de video por TCP: energia, a entrada que vai para a saida 1, volume e texto livre.
    """
    arquivo = {
        "manifesto": {
            "tipo": "matriz_de_teste",
            "rotulo": {"pt": "Matriz de teste", "en": "Test matrix"},
            "categoria": "matriz",
            "capacidades": ["ligar", "desligar", "fonte", "volume", "comando_extra"],
        },
        "transporte": {
            "tcp": {"porta": porta, "terminador": "\r", "timeout_s": 0.5, "intervalo_min_ms": 0}
        },
        "comandos": {
            "ligar": {"envia": "SET POWER ON"},
            "desligar": {"envia": "SET POWER OFF"},
            "fonte": {
                "envia": "SET VS {valor}",
                "valores": {"HDMI 1": "IN1", "HDMI 2": "IN2"},
            },
            "volume": {"envia": "SET VOL {valor_escala}"},
            "comando_extra": {"envia": "{valor}"},
        },
        "estado": {
            "pede": [{"envia": "GET ALL"}],
            "le": {
                "ligado": {"regex": "POWER (ON|OFF)", "verdadeiro": "ON"},
                "fonte": {"regex": "VS (IN[1-8])"},
                "volume": {"regex": "VOL ([0-9]{1,3})"},
            },
        },
        "escala_volume": {"min": 0, "max": 60},
    }
    return {**arquivo, **mudancas}


def _driver(aparelho: ServidorLinha, *, regex=None, cadastro=None, **mudancas) -> Driver:
    dados = _declaracao(aparelho.endereco[1], **mudancas)
    classe = construir(validar(dados, regex=Fogo()), regex=regex or Regex())
    return classe(cadastro or _Cadastro(ip=aparelho.endereco[0]))


async def _no_fio(aparelho: ServidorLinha, quantas: int) -> list[bytes]:
    """The lines the device got, once it got them: a command is written and never read back,
    so the test waits for the device instead of guessing when it was served.

    As linhas que o aparelho recebeu, quando ele as recebeu: um comando é escrito e nunca
    lido de volta, então o teste espera pelo aparelho em vez de adivinhar quando ele foi
    servido.
    """
    laco = asyncio.get_running_loop()
    fim = laco.time() + ESPERA_MAXIMA_S
    while len(aparelho.recebidas) < quantas and laco.time() < fim:
        await asyncio.sleep(0.005)
    return aparelho.recebidas


def _com_comando(aparelho: ServidorLinha, acao: str, comando: dict, **mudancas) -> Driver:
    dados = _declaracao(aparelho.endereco[1])
    dados["comandos"][acao] = comando
    return _driver(aparelho, **{**mudancas, "comandos": dados["comandos"]})


async def test_o_driver_declarativo_e_um_driver_como_qualquer_outro():
    """Rule 1 of section 2: one driver contract, so nothing above knows this came from a file.

    Regra 1 da seção 2: um contrato de driver, então nada acima sabe que isto veio de um arquivo.
    """
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
    manifesto = driver.MANIFESTO
    assert isinstance(driver, Driver)
    assert validar_manifesto(manifesto) is None
    # Why: the motor is never read from the file, so a declaration cannot claim to be code.
    # Por que: o motor nunca é lido do arquivo, então uma declaração não pode se dizer código.
    assert manifesto.motor == "declarativo"
    assert manifesto.tipo == "matriz_de_teste"
    assert driver.estado() == Estado(online=False, fontes=("HDMI 1", "HDMI 2"))


async def test_o_poll_devolve_um_Estado_tipado_com_o_volume_na_escala_do_contrato():
    """Section 6: volume is ALWAYS 0 to 100, and converting the real range is the driver's job.

    Seção 6: o volume é SEMPRE 0 a 100, e converter a faixa real é trabalho do driver.
    """
    async with ServidorLinha({b"GET ALL": ESTADO_LIGADO}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert isinstance(estado, Estado)
    assert estado.online is True
    assert estado.ligado is True
    # 30 of a device that goes to 60 is half the scale, which the contract writes as 50.
    # 30 de um aparelho que vai até 60 é metade da escala, que o contrato escreve como 50.
    assert estado.volume == 50
    # The wire says IN2 and the panel offered "HDMI 2": what comes back is what was offered.
    # O fio diz IN2 e o painel ofereceu "HDMI 2": o que volta é o que foi oferecido.
    assert estado.fonte == "HDMI 2"
    assert estado.detalhe == ""


async def test_o_comando_traduz_o_valor_pelo_mapa_declarado():
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        assert await driver.executar("fonte", "HDMI 2") is None
        assert await _no_fio(aparelho, 1) == [b"SET VS IN2"]
        await driver.parar()
    assert driver.estado().fonte == "HDMI 2"


async def test_o_valor_fora_do_mapa_e_recusado_antes_de_qualquer_byte_no_fio():
    """The map is the whole vocabulary the file gave the action; outside it is invalid_value.

    O mapa é todo o vocabulário que o arquivo deu à ação; fora dele é invalid_value.
    """
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        resposta = await driver.executar("fonte", "HDMI 9")
        await driver.parar()
    assert resposta == "invalid_value"
    assert resposta in CODIGOS
    assert aparelho.recebidas == []
    assert aparelho.conexoes == 0


async def test_a_acao_fora_do_manifesto_nunca_chega_ao_aparelho():
    """Section 6: what the manifest does not declare is refused before the driver acts.

    Seção 6: o que o manifesto não declara é recusado antes de o driver agir.
    """
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        resposta = await driver.executar("mudo", True)
        await driver.parar()
    assert resposta == "nao_suportado"
    assert aparelho.conexoes == 0


async def test_o_volume_fora_de_zero_a_cem_nao_alcanca_o_fio():
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        recusas = [await driver.executar("volume", valor) for valor in (101, -1, "40", True, None)]
        assert await driver.executar("volume", 50) is None
        # Half of the contract on a device that goes to 60 is 30 on the wire.
        # Metade do contrato num aparelho que vai até 60 é 30 no fio.
        assert await _no_fio(aparelho, 1) == [b"SET VOL 30"]
        await driver.parar()
    assert recusas == ["invalid_value"] * 5
    assert driver.estado().volume == 50


async def test_o_valor_com_retorno_de_carro_nao_vira_dois_comandos_no_fio():
    """The bench defect: a label copied from a manual with a \\r in it became TWO commands.

    O defeito de bancada: um rótulo copiado do manual com um \\r virava DOIS comandos.
    """
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        assert await driver.executar("comando_extra", "SET POWER ON\rSET POWER OFF") is None
        assert await _no_fio(aparelho, 1) == [b"SET POWER ONSET POWER OFF"]
        await driver.parar()


async def test_o_aparelho_que_nunca_responde_deixa_o_equipamento_offline():
    """A device that ignores the question is answered by the deadline, never by a hung poll.

    Um aparelho que ignora a pergunta é respondido pelo prazo, nunca por um poll travado.
    """
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is False
    assert estado.detalhe == "eq_offline"
    assert estado.detalhe in DETALHES


async def test_o_aparelho_que_responde_lixo_segue_online_e_nao_inventa_leitura():
    """A device answers what it likes, and what it likes is not a reading; nothing raises.

    Um aparelho responde o que quiser, e o que ele quer não é leitura; nada estoura.
    """
    async with ServidorLinha({b"GET ALL": b"\x00\x01\x1b[31m???\r"}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.fonte, estado.volume) == (None, None, None)


@pytest.mark.parametrize(
    "respondido,esperado",
    [
        ("inf", None),
        ("-inf", None),
        ("nan", None),
        ("1e400", None),
        ("9" * 400, None),
        # A finite number is a reading, however big, and the contract of section 6 clamps it.
        # Um número finito é leitura, por maior que seja, e o contrato da seção 6 o prende.
        ("1e308", 100),
    ],
)
async def test_o_volume_que_nao_e_finito_e_um_campo_sem_leitura_e_nao_um_poll_perdido(
    respondido, esperado
):
    """A number the device answered is never allowed to cost the OTHER readings of that poll.

    Converting infinity raised OverflowError and NaN raised ValueError inside the conversion
    of the scale, and the poll swallowed it whole: the power and the source read in the same
    answer were thrown away, the equipment read offline while it was powered on, and a
    traceback landed in the log of the appliance every ten seconds.

    Um número que o aparelho respondeu nunca pode custar as OUTRAS leituras daquele poll.

    Converter infinito estourava OverflowError e NaN estourava ValueError dentro da conversão
    da escala, e o poll engolia isso inteiro: a energia e a fonte lidas na mesma resposta eram
    jogadas fora, o equipamento lia offline estando ligado, e um traceback caía no log do
    appliance a cada dez segundos.
    """
    leitura = {
        "ligado": {"regex": "POWER (ON|OFF)", "verdadeiro": "ON"},
        "fonte": {"regex": "VS (IN[1-8])"},
        "volume": {"regex": "VOL ([^ ]+)"},
    }
    resposta = f"POWER ON VS IN2 VOL {respondido}\r".encode()
    async with ServidorLinha({b"GET ALL": resposta}) as aparelho:
        driver = _driver(aparelho, estado={"pede": [{"envia": "GET ALL"}], "le": leitura})
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.detalhe == ""
    assert estado.volume == esperado
    # The rest of the same answer still arrives: an unread field is None, never a dead poll.
    # O resto da mesma resposta continua chegando: campo sem leitura é None, nunca poll morto.
    assert estado.ligado is True
    assert estado.fonte == "HDMI 2"


async def test_a_resposta_gigante_nao_e_acumulada_pelo_daemon():
    """A device on the LAN must never make the daemon buffer without bound, section 9 spirit.

    Um aparelho na LAN nunca pode fazer o daemon acumular sem limite, espírito da seção 9.
    """
    gigante = b"POWER ON " + b"Z" * (transporte.LINHA_MAXIMA * 2) + b"\r"
    async with ServidorLinha({b"GET ALL": gigante}) as aparelho:
        driver = _driver(aparelho)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    estado = driver.estado()
    assert estado.online is False
    assert estado.detalhe == "eq_offline"


def _sem_terminador(porta: int, estado: dict) -> dict:
    """The same matrix with a file that declares no terminator, which is what MAKES the hole.

    A mesma matriz com um arquivo que não declara terminador, que é o que ABRE o buraco.
    """
    dados = _declaracao(porta, estado=estado)
    dados["transporte"]["tcp"]["terminador"] = ""
    return dados


def _driver_partido(aparelho: AparelhoPartido, estado: dict) -> Driver:
    dados = _sem_terminador(aparelho.endereco[1], estado)
    return construir(validar(dados, regex=Fogo()), regex=Regex())(
        _Cadastro(ip=aparelho.endereco[0])
    )


async def test_a_resposta_partida_nao_vaza_para_a_leitura_da_pergunta_seguinte():
    """Without a terminator the deadline is the only frame the answer has, so a single read
    kept the first segment and left the rest to be read as the answer to the NEXT question.

    That is a device silently corrupting every reading after the first: the power arrives cut
    in half and reads as nothing, and the piece left behind lands where the source belongs.

    Sem terminador o prazo é a única moldura que a resposta tem, então uma leitura só ficava
    com o primeiro segmento e deixava o resto para ser lido como resposta da PRÓXIMA pergunta.

    Isso é um aparelho corrompendo em silêncio toda leitura depois da primeira: a energia chega
    cortada ao meio e não lê nada, e o pedaço que sobrou cai onde mora a fonte.
    """
    estado = {
        "pede": [{"envia": "GET POWER"}, {"envia": "GET VS"}],
        "le": {
            "ligado": {"regex": "POWER (ON|OFF)", "verdadeiro": "ON"},
            "fonte": {"regex": "VS (IN[1-8])"},
        },
    }
    async with AparelhoPartido(((b"POWER ", b"ON"), (b"VS IN2",))) as aparelho:
        driver = _driver_partido(aparelho, estado)
        await driver.iniciar()
        await driver.atualizar()
        await driver.parar()
    lido = driver.estado()
    assert lido.online is True
    assert lido.ligado is True
    assert lido.fonte == "HDMI 2"
    assert aparelho.recebidas == [b"GET POWER", b"GET VS"]


async def test_sem_terminador_o_aparelho_calado_e_o_falante_demais_ficam_offline():
    """The two ends of a read with no frame: nothing said by the deadline is a device that is
    not there, and more than the ceiling is what the daemon refuses to hold on a small board.

    Os dois extremos de uma leitura sem moldura: nada dito até o prazo é aparelho que não está
    lá, e mais que o teto é o que o daemon se recusa a guardar numa placa pequena.
    """
    estado = {"pede": [{"envia": "GET POWER"}], "le": {}}
    calado: tuple[tuple[bytes, ...], ...] = ((b"",),)
    falante = ((b"POWER ON " + b"Z" * (transporte.LINHA_MAXIMA * 2),),)
    for respostas in (calado, falante):
        async with AparelhoPartido(respostas) as aparelho:
            driver = _driver_partido(aparelho, estado)
            await driver.iniciar()
            await driver.atualizar()
            await driver.parar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "eq_offline"


async def test_uma_sessao_por_vez_mesmo_com_comando_dentro_da_janela_do_poll():
    """The bench fact: the matrix accepts ONE connection, and the command of the integrator
    lands inside the poll window. The poll here asks three questions with a pause between
    them, so a command that did not wait would be visible between two of them.

    O fato de bancada: a matriz aceita UMA conexão, e o comando do integrador cai dentro da
    janela do poll. O poll aqui faz três perguntas com pausa entre elas, então um comando que
    não esperasse apareceria entre duas delas.
    """
    async with ServidorLinha(RESPOSTAS_EM_TRES, atraso_s=0.05) as aparelho:
        driver = _driver(aparelho, estado=POLL_EM_TRES)
        await driver.iniciar()
        poll = asyncio.create_task(driver.atualizar())
        await asyncio.sleep(0.02)
        comando = asyncio.create_task(driver.executar("ligar"))
        assert await comando is None
        await poll
        assert await _no_fio(aparelho, 4) == [b"GET POWER", b"GET VS", b"GET VOL", b"SET POWER ON"]
        await driver.parar()
    # One session for the poll and one for the command, never the two at the same time.
    # Uma sessão para o poll e uma para o comando, nunca as duas ao mesmo tempo.
    assert aparelho.conexoes == 2
    assert driver.estado().volume == 50


async def test_a_saudacao_declarada_e_consumida_antes_da_primeira_resposta():
    """The PJLink shape: a greeting read as an answer puts every read one line behind.

    O formato do PJLink: uma saudação lida como resposta deixa toda leitura uma linha atrás.
    """
    saudacao = b"MATRIX READY\r"
    respostas = {b"GET ALL": ESTADO_LIGADO}
    async with ServidorLinha(respostas, saudacao=saudacao) as aparelho:
        transportes = {"tcp": {"porta": aparelho.endereco[1], "terminador": "\r", "timeout_s": 0.5}}
        atento = _driver(aparelho, transporte={"tcp": {**transportes["tcp"], "saudacao": True}})
        distraido = _driver(aparelho, transporte=transportes)
        await atento.atualizar()
        await distraido.atualizar()
    assert atento.estado().ligado is True
    # Without the declaration the greeting IS the answer, and nothing of the state is read.
    # Sem a declaração a saudação É a resposta, e nada do estado é lido.
    assert distraido.estado().ligado is None


async def test_a_repeticao_declarada_manda_a_sequencia_inteira_de_novo():
    """The infrared bridge of section 7: the repetition is declared, never written as a loop.

    A ponte de infravermelho da seção 7: a repetição é declarada, nunca escrita como laço.
    """
    async with ServidorLinha({}) as aparelho:
        driver = _com_comando(
            aparelho,
            "ligar",
            {"sequencia": [{"envia": "MENU"}, {"envia": "OK"}], "repete": 3},
        )
        await driver.iniciar()
        assert await driver.executar("ligar") is None
        assert await _no_fio(aparelho, 6) == [b"MENU", b"OK"] * 3
        await driver.parar()


async def test_o_intervalo_minimo_declarado_e_respeitado_entre_os_passos():
    """The 200 ms of the iEAST, declared in the file and honoured by the transport.

    Os 200 ms do iEAST, declarados no arquivo e respeitados pelo transporte.
    """
    intervalo_ms = 60
    async with ServidorLinha({}) as aparelho:
        dados = _declaracao(aparelho.endereco[1])
        dados["comandos"]["ligar"] = {"sequencia": [{"envia": "A"}, {"envia": "B"}]}
        dados["transporte"]["tcp"]["intervalo_min_ms"] = intervalo_ms
        classe = construir(validar(dados, regex=Fogo()), regex=Regex())
        driver = classe(_Cadastro(ip=aparelho.endereco[0]))
        await driver.iniciar()
        comeco = asyncio.get_running_loop().time()
        assert await driver.executar("ligar") is None
        gasto = asyncio.get_running_loop().time() - comeco
        assert await _no_fio(aparelho, 2) == [b"A", b"B"]
        await driver.parar()
    assert gasto >= intervalo_ms / 1000


async def test_o_intervalo_declarado_no_comando_separa_os_passos_no_fio():
    """Section 7 lets a command declare its own interval, and a device that needs a pause
    between two lines gets the pause it asked for; the transport declares none here, so the
    only thing that can hold the second line back is the interval written in the command.

    A seção 7 deixa um comando declarar o intervalo dele, e um aparelho que precisa de pausa
    entre duas linhas recebe a pausa que pediu; o transporte não declara nenhuma aqui, então a
    única coisa que pode segurar a segunda linha é o intervalo escrito no comando.
    """
    intervalo_ms = 60
    async with ServidorLinha({}) as aparelho:
        dados = _declaracao(aparelho.endereco[1])
        dados["comandos"]["ligar"] = {
            "sequencia": [{"envia": "MENU"}, {"envia": "OK"}],
            "intervalo_ms": intervalo_ms,
        }
        assert dados["transporte"]["tcp"]["intervalo_min_ms"] == 0
        classe = construir(validar(dados, regex=Fogo()), regex=Regex())
        driver = classe(_Cadastro(ip=aparelho.endereco[0]))
        await driver.iniciar()
        comeco = asyncio.get_running_loop().time()
        assert await driver.executar("ligar") is None
        gasto = asyncio.get_running_loop().time() - comeco
        assert await _no_fio(aparelho, 2) == [b"MENU", b"OK"]
        await driver.parar()
    assert gasto >= intervalo_ms / 1000


async def test_a_leitura_passa_pela_regex_segura_e_nunca_pelo_re_deste_processo():
    """Section 7: `re.search` does not release the GIL, so no read runs it here.

    Seção 7: o `re.search` não solta a GIL, então nenhuma leitura o roda aqui.
    """
    regex = Regex()
    async with ServidorLinha({b"GET ALL": ESTADO_LIGADO}) as aparelho:
        driver = _driver(aparelho, regex=regex)
        await driver.atualizar()
    padroes = [padrao for padrao, _texto in regex.perguntou]
    assert padroes == ["POWER (ON|OFF)", "VS (IN[1-8])", "VOL ([0-9]{1,3})"]
    assert all(texto == ESTADO_LIGADO.decode().strip("\r") for _padrao, texto in regex.perguntou)


async def test_a_regex_que_estoura_o_prazo_derruba_a_leitura_e_nao_o_poll():
    """None from the safe regex is a deadline or a refused pattern: the field is dropped.

    None da regex segura é prazo ou padrão recusado: o campo cai.
    """
    async with ServidorLinha({b"GET ALL": ESTADO_LIGADO}) as aparelho:
        driver = _driver(aparelho, regex=Regex(morre=True))
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is None


async def test_o_poll_inteiro_roda_sobre_a_regex_segura_de_verdade():
    """The wiring itself: the shared worker of the process reads the answer of the device.

    A ligação em si: o trabalhador compartilhado do processo lê a resposta do aparelho.
    """
    async with ServidorLinha({b"GET ALL": ESTADO_LIGADO}) as aparelho:
        dados = _declaracao(aparelho.endereco[1])
        classe = construir(validar(dados, regex=regex_seguro.instancia()))
        driver = classe(_Cadastro(ip=aparelho.endereco[0]))
        await driver.atualizar()
    assert driver.estado() == Estado(
        online=True, ligado=True, volume=50, fonte="HDMI 2", fontes=("HDMI 1", "HDMI 2")
    )


async def test_o_endereco_que_nao_e_ip_literal_nunca_alcanca_o_fio():
    """Section 9: the hub resolves nothing, so it never becomes a proxy into the LAN.

    Seção 9: o hub não resolve nada, então nunca vira um proxy para a LAN.
    """
    async with ServidorLinha({b"GET ALL": ESTADO_LIGADO}) as aparelho:
        for endereco in ("localhost", "matriz.local", "http://127.0.0.1", "127.0.0.1:23", ""):
            driver = _driver(aparelho, cadastro=_Cadastro(ip=endereco))
            await driver.atualizar()
            assert await driver.executar("ligar") == "eq_offline"
            assert driver.estado().online is False
    assert aparelho.conexoes == 0


async def test_o_ip_do_cadastro_entra_no_comando_que_o_pede():
    async with ServidorLinha({}) as aparelho:
        driver = _com_comando(aparelho, "ligar", {"envia": "REGISTER {ip}"})
        assert await driver.executar("ligar") is None
        assert await _no_fio(aparelho, 1) == [b"REGISTER 127.0.0.1"]


async def test_o_comando_que_pede_um_valor_que_ninguem_escolheu_e_recusado():
    """The marker itself on the wire would be an error no integrator can explain.

    O próprio marcador no fio seria um erro que integrador nenhum explica.
    """
    async with ServidorLinha({}) as aparelho:
        driver = _com_comando(aparelho, "ligar", {"envia": "SET POWER {valor}"})
        assert await driver.executar("ligar") == "invalid_value"
    assert aparelho.recebidas == []


async def test_nenhuma_excecao_escapa_do_executar_ou_do_atualizar(monkeypatch):
    """Section 6: a driver answers a stable code, and a broken transport is not an exception
    the gestor has to guess about.

    Seção 6: um driver responde código estável, e um transporte quebrado não é exceção que o
    gestor tenha de adivinhar.
    """

    async def explodir(*_argumentos, **_nomeados):
        raise RuntimeError("the wire is on fire")

    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
        monkeypatch.setattr(driver._canal, "enviar", explodir)
        monkeypatch.setattr(driver._canal, "perguntar", explodir)
        assert await driver.executar("ligar") == "erro_aparelho"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


@pytest.mark.parametrize("acao,esperado", [("ligar", True), ("desligar", False)])
async def test_o_comando_de_energia_reporta_otimista_para_o_painel(acao, esperado):
    async with ServidorLinha({}) as aparelho:
        driver = _driver(aparelho)
        assert await driver.executar(acao) is None
    assert driver.estado().ligado is esperado


def _embarcado(nome: str) -> dict:
    """One example of the catalog that ships in the image, read from the file itself.

    Um exemplo do catálogo que embarca na imagem, lido do próprio arquivo.
    """
    return json.loads((EXEMPLOS / nome).read_text(encoding="utf-8"))


async def test_o_exemplo_embarcado_de_tcp_fala_com_o_aparelho_simulado():
    """The exit gate of milestone 3: the file that ships is the file that was driven.

    O portão de saída do marco 3: o arquivo que embarca é o arquivo que foi dirigido.
    """
    respostas = {b"GET POWER": b"POWER ON\r\n", b"GET OUT1 VS": b"OUT1 VS IN2\r\n"}
    async with ServidorLinha(respostas, terminador=b"\r\n") as aparelho:
        dados = _embarcado("matriz_hdmi_ascii.json")
        dados["transporte"]["tcp"]["porta"] = aparelho.endereco[1]
        classe = construir(validar(dados, regex=Fogo()), regex=Regex())
        driver = classe(_Cadastro(ip=aparelho.endereco[0]))
        await driver.iniciar()
        await driver.atualizar()
        lido = driver.estado()
        assert await driver.executar("fonte", "HDMI 3") is None
        assert await _no_fio(aparelho, 3) == [b"GET POWER", b"GET OUT1 VS", b"SET OUT1 VS IN3"]
        await driver.parar()
    assert lido.online is True
    assert lido.ligado is True
    assert lido.fonte == "HDMI 2"
    # The command reports optimistic, and the next poll is what confirms it from the device.
    # O comando reporta otimista, e o poll seguinte é quem confirma pelo aparelho.
    assert driver.estado().fonte == "HDMI 3"


async def test_o_gestor_dirige_um_declarativo_como_dirige_um_nativo():
    """Rule 1 of section 2 from above: the capability gate, the poll and the typed state are
    the same for a driver that came from a file.

    Regra 1 da seção 2 vista de cima: o portão de capacidade, o poll e o estado tipado são os
    mesmos para um driver que veio de um arquivo.
    """
    async with ServidorLinha({b"GET ALL": ESTADO_LIGADO}) as aparelho:
        dados = _declaracao(aparelho.endereco[1])
        classe = construir(validar(dados, regex=Fogo()), regex=Regex())
        cadastro = Cadastro(
            identidade="uuid-da-matriz", tipo="matriz_de_teste", ip=aparelho.endereco[0]
        )
        # A long interval keeps the scheduled visit out of the way of the one asked for here.
        # Um intervalo longo mantém a visita agendada fora do caminho da que é pedida aqui.
        gestor = Gestor({cadastro.tipo: classe}, [cadastro], intervalo_s=60)
        await gestor.iniciar()
        try:
            gestor.visitar_agora(cadastro.identidade)
            estado = await _online(gestor, cadastro.identidade)
            assert estado == Estado(
                online=True, ligado=True, volume=50, fonte="HDMI 2", fontes=("HDMI 1", "HDMI 2")
            )
            # The gate of section 6 answers before the driver, and no byte reaches the matrix.
            # O portão da seção 6 responde antes do driver, e byte nenhum chega à matriz.
            assert await gestor.executar(cadastro.identidade, "mudo", True) == "nao_suportado"
            assert await gestor.executar(cadastro.identidade, "ligar") is None
            assert await _no_fio(aparelho, 2) == [b"GET ALL", b"SET POWER ON"]
        finally:
            await gestor.parar()


async def _online(gestor: Gestor, identidade: str) -> Estado:
    """The state of an equipment once the visit asked for out of turn has landed.

    O estado de um equipamento quando a visita pedida fora da vez chegou.
    """
    laco = asyncio.get_running_loop()
    fim = laco.time() + ESPERA_MAXIMA_S
    while not gestor.estados()[identidade].online and laco.time() < fim:
        await asyncio.sleep(0.005)
    return gestor.estados()[identidade]
