# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""A scene of section 8: DATA, a list of steps that each run one action on one equipment.

The rule section 7 fixes for a driver holds here for the same reason: what an integrator
saves is data and never program. A step names one equipment, one action of section 6 and
one value, plus an optional wait in milliseconds after it, and that is the whole vocabulary.
No condition, no loop, no expression, no arithmetic. There is no step that runs a scene,
because a scene starting a scene is a loop written in data, and two of them naming each other
would be a hub that never stops.

Section 8 gives the ceiling: the scene data point of every licence carries a number from 1 to
32, so there are thirty two scenes and the POSITION of a scene is its number, the same number
in every licence. A scene that was erased leaves its slot empty instead of pulling the next
one back, because the shift would silently move scene 3 to scene 2 in every automation the
customer already built on the platform. Two string data points carry the names of all of
them, sixteen each, inside 255 bytes, so a name that does not fit there is refused when it is
saved and never cut when it is published.

Running one is fire and forget for whoever asked: the answer comes at once and the steps run
in order on a task of their own. A step that fails is logged with its stable code and the
scene goes on, because a projector that is off must not stop the lights of the same scene.

Uma cena da seção 8: DADO, uma lista de passos que rodam uma ação num equipamento cada.

A regra que a seção 7 fixa para um driver vale aqui pelo mesmo motivo: o que um integrador
salva é dado e nunca programa. Um passo nomeia um equipamento, uma ação da seção 6 e um
valor, mais uma espera opcional em milissegundos depois dele, e esse é o vocabulário inteiro.
Sem condicional, sem laço, sem expressão, sem aritmética. Não existe passo que dispara uma
cena, porque uma cena que dispara uma cena é um laço escrito em dado, e duas delas nomeando
uma à outra seriam um hub que nunca para.

A seção 8 dá o teto: o data point de cena de toda licença leva um número de 1 a 32, então são
trinta e duas cenas e a POSIÇÃO de uma cena é o número dela, o mesmo número em toda licença.
Uma cena apagada deixa a vaga vazia em vez de puxar a seguinte, porque o empurrão moveria em
silêncio a cena 3 para a cena 2 em toda automação que o cliente já montou na plataforma. Dois
data points de string carregam os nomes de todas elas, dezesseis cada, dentro de 255 bytes,
então um nome que não cabe lá é recusado ao ser salvo e nunca cortado ao ser publicado.

Rodar uma é disparar e esquecer para quem pediu: a resposta sai na hora e os passos correm em
ordem numa tarefa própria. Um passo que falha é registrado com o código estável dele e a cena
segue, porque um projetor desligado não pode parar as luzes da mesma cena.
"""

import asyncio
import functools
import logging
import re
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

from iphub.dpbus import mapa
from iphub.drivers.manifesto import (
    CAPACIDADES,
    TECLAS,
    TEMPERATURA_MAXIMA,
    TEMPERATURA_MINIMA,
    VENTOS,
)

log = logging.getLogger("iphub.cenas")

# Section 8 numbers 1 to 32 on the scene data point, and there is no thirty third.
# A seção 8 numera 1 a 32 no data point de cena, e não existe uma trigésima terceira.
MAXIMO = mapa.CENAS

# Why: a scene that touches every equipment of a full installation (twelve of audio and video
# plus eight air conditioners) with a power, a level and an input each is sixty steps, so
# this ceiling holds a scene that touches everything and still refuses a file that grew into
# a program.
# Por que: uma cena que toca em todo equipamento de uma instalação cheia (doze de áudio e
# vídeo mais oito ares condicionados) com um ligar, um nível e uma entrada cada são sessenta
# passos, então este teto cabe uma cena que toca em tudo e ainda recusa um arquivo que virou
# programa.
PASSOS_MAXIMOS = 64

# Why: the longest real wait inside a scene is the warmup of a projector between the power
# command and the input one; a scene is not a scheduler, and anything longer than this is an
# automation on the platform and not a step here.
# Por que: a maior espera real dentro de uma cena é o aquecimento de um projetor entre o
# comando de ligar e o de entrada; uma cena não é agendador, e algo maior que isto é uma
# automação na plataforma e não um passo aqui.
ESPERA_MAXIMA_MS = 30_000

# Why: an AV device needs a moment between one command and the next (a receiver that is
# powering on drops the input it is sent in the same second), so a scene waits this much
# after every step that does not name its own wait, and the integrator edits it per scene.
# Por que: um aparelho de AV precisa de um instante entre um comando e o seguinte (um
# receiver ligando perde a entrada que recebe no mesmo segundo), então uma cena espera isto
# depois de todo passo que não nomeia a própria espera, e o integrador edita por cena.
INTERVALO_PADRAO_MS = 1_000

NOME_MAXIMO = 40
VALOR_TEXTO_MAXIMO = 64
IDENTIDADE_MAXIMA = 200

MILISSEGUNDO_S = 0.001

# The one action of a scene that is not a capability of section 6: the group of the licence
# of audio and video the equipment sits in, led by the identity in the value, or solo.
# A única ação de uma cena que não é capacidade da seção 6: o grupo da licença de áudio e
# vídeo em que o equipamento está, liderado pela identidade no valor, ou solo.
ACAO_GRUPO = "grupo"

# Why: agrupar is the capability a manifest declares to say the equipment CAN group; the move
# itself is the grupo action, so a scene never writes agrupar on a driver.
# Por que: agrupar é a capacidade que um manifesto declara para dizer que o equipamento SABE
# agrupar; o movimento em si é a ação grupo, então uma cena nunca escreve agrupar num driver.
ACOES = (*(acao for acao in CAPACIDADES if acao != "agrupar"), ACAO_GRUPO)

SEM_VALOR = ("ligar", "desligar", "tocar", "pausar", "parar", "proxima", "anterior")
COM_TEXTO = ("fonte", "atalho", "modo", "comando_extra")

CAMPO = "cenas"
CHAVES_CENA = ("nome", "passos", "intervalo_ms")
CHAVES_PASSO = ("equipamento", "acao", "valor", "espera_ms")

# The stable codes a refusal carries, section 11: the daemon answers a code and never a
# phrase, and the panel translates it. The codes of the map come with the verdict of the
# names, which belongs to the map and is not written a second time here.
# Os códigos estáveis que uma recusa carrega, seção 11: o daemon responde um código e nunca
# uma frase, e o painel o traduz. Os códigos do mapa vêm com o veredito dos nomes, que é do
# mapa e não é escrito uma segunda vez aqui.
CENAS_NAO_LISTA = "cenas_nao_lista"
CENAS_DEMAIS = "cenas_demais"
CENA_NAO_OBJETO = "cena_nao_objeto"
CENA_CHAVE_DESCONHECIDA = "cena_chave_desconhecida"
CENA_NOME_INVALIDO = "cena_nome_invalido"
CENA_PASSOS_INVALIDOS = "cena_passos_invalidos"
CENA_PASSOS_DEMAIS = "cena_passos_demais"
CENA_PASSO_NAO_OBJETO = "cena_passo_nao_objeto"
CENA_EQUIPAMENTO_INVALIDO = "cena_equipamento_invalido"
CENA_EQUIPAMENTO_DESCONHECIDO = "cena_equipamento_desconhecido"
CENA_ACAO_DESCONHECIDA = "cena_acao_desconhecida"
CENA_VALOR_INVALIDO = "cena_valor_invalido"
CENA_ESPERA_INVALIDA = "cena_espera_invalida"
CENA_INTERVALO_INVALIDO = "cena_intervalo_invalido"

CODIGOS = (
    CENAS_NAO_LISTA,
    CENAS_DEMAIS,
    CENA_NAO_OBJETO,
    CENA_CHAVE_DESCONHECIDA,
    CENA_NOME_INVALIDO,
    CENA_PASSOS_INVALIDOS,
    CENA_PASSOS_DEMAIS,
    CENA_PASSO_NAO_OBJETO,
    CENA_EQUIPAMENTO_INVALIDO,
    CENA_EQUIPAMENTO_DESCONHECIDO,
    CENA_ACAO_DESCONHECIDA,
    CENA_VALOR_INVALIDO,
    CENA_ESPERA_INVALIDA,
    CENA_INTERVALO_INVALIDO,
    mapa.NOMES_LONGOS,
    mapa.NOME_NAO_GRAVAVEL,
)

# What a request to run a scene answers with, which is not a problem of the saved file.
# O que um pedido para rodar uma cena responde, que não é problema do arquivo salvo.
CENA_NAO_ENCONTRADA = "cena_nao_encontrada"
CENA_EM_CURSO = "cena_em_curso"
CODIGOS_DE_EXECUCAO = (CENA_NAO_ENCONTRADA, CENA_EM_CURSO)

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

type Acionar = Callable[[str, str, object], Awaitable[str | None]]
type Dormir = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Passo:
    """One step: one equipment, one action, one value and the pause that follows it.

    espera_ms is the pause AFTER the step, so a file reads in the order it happens: power the
    projector, wait for it, choose the input. None takes the interval of the scene. The wait
    of the LAST step is not slept, because nothing follows it and holding the task would only
    delay a shutdown.

    Um passo: um equipamento, uma ação, um valor e a pausa que vem depois dele.

    espera_ms é a pausa DEPOIS do passo, para um arquivo se ler na ordem em que acontece:
    liga o projetor, espera por ele, escolhe a entrada. None toma o intervalo da cena. A
    espera do ÚLTIMO passo não é dormida, porque nada vem depois dela e segurar a tarefa só
    atrasaria um desligamento.
    """

    equipamento: str
    acao: str
    valor: object = None
    espera_ms: int | None = None


@dataclass(frozen=True)
class Cena:
    """One scene, whose number is its position; a slot nobody uses carries no step.

    Uma cena, cujo número é a posição dela; uma vaga que ninguém usa não carrega passo.
    """

    nome: str = ""
    passos: tuple[Passo, ...] = ()
    intervalo_ms: int = INTERVALO_PADRAO_MS


class CenasInvalidas(ValueError):
    """Carries every problem as (campo, codigo), so the panel fixes the list in one pass.

    Carrega todo problema como (campo, codigo), para o painel consertar a lista numa passada.
    """

    def __init__(self, problemas: tuple[tuple[str, str], ...]) -> None:
        self.problemas = problemas
        super().__init__("; ".join(f"{campo}: {codigo}" for campo, codigo in problemas))


def validar(dados: object, identidades: Collection[str] | None = None) -> tuple[Cena, ...]:
    """The scenes as typed data, or CenasInvalidas listing EVERY problem at once.

    With identidades, a step that names an equipment outside them is refused; without them,
    which is how a config.json is read on boot, the equipment is judged when the step runs,
    because a registration erased by hand must not keep the whole file from loading.

    Nothing but CenasInvalidas leaves here, for any input at all: this judges what a route
    received and what a hand edited config.json holds, and a validation that raised anything
    else would take the boot of the appliance down with it.

    As cenas como dado tipado, ou CenasInvalidas listando TODO problema de uma vez.

    Com identidades, um passo que nomeia um equipamento fora delas é recusado; sem elas, que
    é como um config.json é lido no boot, o equipamento é julgado quando o passo roda, porque
    um cadastro apagado na mão não pode impedir o arquivo inteiro de carregar.

    Nada além de CenasInvalidas sai daqui, para entrada nenhuma: isto julga o que uma rota
    recebeu e o que um config.json editado na mão guarda, e uma validação que estourasse
    outra coisa levaria o boot do appliance junto.
    """
    leitor = _Leitor(identidades)
    cenas = leitor.cenas(dados)
    if leitor.problemas:
        raise CenasInvalidas(tuple(leitor.problemas))
    return cenas


def nomes(cenas: Sequence[Cena]) -> tuple[str, ...]:
    """The names in the order of the scenes, which is what the two name data points publish.

    Os nomes na ordem das cenas, que é o que os dois data points de nomes publicam.
    """
    return tuple(cena.nome for cena in cenas)


def numero_de(valor: object) -> int | None:
    """The scene number of a scene data point value, or None for anything outside 1..32.

    O número de cena de um valor do data point de cena, ou None para o que está fora de 1..32.
    """
    return mapa.numero_de_cena(valor)


class Executor:
    """Holds the saved scenes and runs one on a task of its own, one run at a time each.

    Guarda as cenas salvas e roda uma numa tarefa própria, uma execução por vez cada.
    """

    def __init__(
        self,
        cenas: Sequence[Cena],
        acionar: Acionar,
        *,
        dormir: Dormir = asyncio.sleep,
    ) -> None:
        self._cenas = tuple(cenas)
        self._acionar = acionar
        self._dormir = dormir
        self._em_curso: dict[int, asyncio.Task] = {}

    @property
    def cenas(self) -> tuple[Cena, ...]:
        return self._cenas

    def trocar(self, cenas: Sequence[Cena]) -> None:
        """Takes the saved list, with no restart. A run already going keeps the steps it
        started with, because half of one file and half of the next is a scene nobody wrote.

        Assume a lista salva, sem reiniciar. Uma execução já em curso mantém os passos com que
        começou, porque metade de um arquivo e metade do seguinte é uma cena que ninguém
        escreveu.
        """
        self._cenas = tuple(cenas)

    def nomes(self) -> tuple[str, ...]:
        return nomes(self._cenas)

    def cena_de(self, numero: object) -> Cena | None:
        """The scene of a number from 1 to 32, or None for a number outside the contract.

        A cena de um número de 1 a 32, ou None para um número fora do contrato.
        """
        posicao = self._posicao(numero)
        return None if posicao is None else self._cenas[posicao - 1]

    def em_curso(self, numero: object) -> bool:
        posicao = self._posicao(numero)
        return posicao is not None and self._rodando(posicao)

    def executar(self, numero: object) -> str | None:
        """Answers at once: None when the scene started, or a stable code that refused it.

        Responde na hora: None quando a cena começou, ou um código estável que a recusou.
        """
        posicao = self._posicao(numero)
        if posicao is None:
            return CENA_NAO_ENCONTRADA
        cena = self._cenas[posicao - 1]
        if not cena.passos:
            # Why: a slot with no step is a scene that was erased and whose number is held
            # open for the automations already built on it, not a scene that does nothing.
            # Por que: uma vaga sem passo é uma cena apagada cujo número fica guardado para as
            # automações já montadas em cima dele, e não uma cena que não faz nada.
            return CENA_NAO_ENCONTRADA
        if self._rodando(posicao):
            # Why: the same scene twice at once would interleave two sequences over the same
            # equipment, and the volume the customer ends up with is whichever step landed
            # last; one run at a time is the only outcome that matches what was written.
            # Por que: a mesma cena duas vezes ao mesmo tempo intercalaria duas sequências
            # sobre os mesmos equipamentos, e o volume que o cliente recebe é o do passo que
            # chegou por último; uma execução por vez é o único resultado igual ao escrito.
            return CENA_EM_CURSO
        tarefa = asyncio.create_task(self._rodar(posicao, cena), name=f"cena:{posicao}")
        self._em_curso[posicao] = tarefa
        tarefa.add_done_callback(functools.partial(self._fim, posicao))
        return None

    async def parar(self) -> None:
        """Takes every run off the wire, so a shutdown leaves no task behind.

        Tira toda execução do fio, para um desligamento não deixar tarefa para trás.
        """
        tarefas = tuple(self._em_curso.values())
        for tarefa in tarefas:
            tarefa.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        self._em_curso.clear()

    def _posicao(self, numero: object) -> int | None:
        """The number as a slot of this list, or None for anything that is not one.

        O número como uma vaga desta lista, ou None para o que não for uma.
        """
        # Why: the number comes from a route path or from a data point, so True and "2" reach
        # here; neither is a scene number and neither may pick one by luck.
        # Por que: o número vem do caminho de uma rota ou de um data point, então True e "2"
        # chegam aqui; nenhum dos dois é número de cena e nenhum pode acertar uma por sorte.
        if type(numero) is not int or not 1 <= numero <= min(MAXIMO, len(self._cenas)):
            return None
        return numero

    def _rodando(self, posicao: int) -> bool:
        tarefa = self._em_curso.get(posicao)
        return tarefa is not None and not tarefa.done()

    async def _rodar(self, numero: int, cena: Cena) -> None:
        ultimo = len(cena.passos) - 1
        for posicao, passo in enumerate(cena.passos):
            await self._passo(numero, passo)
            espera = cena.intervalo_ms if passo.espera_ms is None else passo.espera_ms
            if espera and posicao < ultimo:
                await self._dormir(espera * MILISSEGUNDO_S)

    async def _passo(self, numero: int, passo: Passo) -> None:
        try:
            codigo = await self._acionar(passo.equipamento, passo.acao, passo.valor)
        except asyncio.CancelledError:
            raise
        except BaseException as erro:
            # Why: a device library raising outside Exception deep in a socket call would end
            # the scene at that step, and the lights of the same scene would never be reached;
            # one failed step is all a failure here is allowed to be.
            # Por que: uma biblioteca de aparelho estourando fora de Exception no fundo de uma
            # chamada de socket encerraria a cena naquele passo, e as luzes da mesma cena nunca
            # seriam alcançadas; uma falha aqui só pode ser um passo que falhou.
            log.warning(
                "scene %s could not run %s on %s: %s",
                numero,
                passo.acao,
                passo.equipamento,
                _causa(erro),
            )
            return
        if codigo is not None:
            log.warning(
                "scene %s was refused on %s of %s: %s",
                numero,
                passo.acao,
                passo.equipamento,
                codigo,
            )

    def _fim(self, numero: int, tarefa: asyncio.Task) -> None:
        if self._em_curso.get(numero) is tarefa:
            del self._em_curso[numero]


class _Leitor:
    """Collects every problem instead of stopping at the first, and never raises.

    Junta todo problema em vez de parar no primeiro, e nunca estoura.
    """

    def __init__(self, identidades: Collection[str] | None) -> None:
        self.problemas: list[tuple[str, str]] = []
        self._identidades = None if identidades is None else set(identidades)

    def anotar(self, campo: str, codigo: str) -> None:
        self.problemas.append((campo, codigo))

    def cenas(self, dados: object) -> tuple[Cena, ...]:
        if not isinstance(dados, list | tuple):
            self.anotar(CAMPO, CENAS_NAO_LISTA)
            return ()
        if len(dados) > MAXIMO:
            self.anotar(CAMPO, CENAS_DEMAIS)
            return ()
        cenas = tuple(self.cena(item, indice) for indice, item in enumerate(dados))
        self.conferir_nomes(cenas)
        return cenas

    def conferir_nomes(self, cenas: tuple[Cena, ...]) -> None:
        """The two name data points carry every name, so the list is judged whole and by the
        map, which owns the 255 bytes and the code that says why they do not fit.

        Os dois data points de nomes carregam todo nome, então a lista é julgada inteira e
        pelo mapa, que é dono dos 255 bytes e do código que diz por que eles não cabem.
        """
        try:
            mapa.nomes_das_cenas(nomes(cenas))
        except mapa.NomesInvalidos as erro:
            self.anotar(CAMPO, erro.codigo)

    def cena(self, item: object, indice: int) -> Cena:
        onde = f"{CAMPO}[{indice}]"
        if not isinstance(item, Mapping):
            self.anotar(onde, CENA_NAO_OBJETO)
            return Cena()
        self.chaves(item, CHAVES_CENA, onde)
        return Cena(
            nome=self.nome(item.get("nome", ""), onde),
            passos=self.passos(item.get("passos", ()), onde),
            intervalo_ms=self.intervalo(item.get("intervalo_ms", INTERVALO_PADRAO_MS), onde),
        )

    def chaves(self, item: Mapping, aceitas: tuple[str, ...], onde: str) -> None:
        # Why: a key that was typed and that nothing reads is a scene silently doing less
        # than what the integrator wrote, which is section 7 for a driver and holds here.
        # Por que: uma chave digitada que ninguém lê é uma cena fazendo em silêncio menos do
        # que o integrador escreveu, que é a seção 7 para um driver e vale aqui.
        for chave in item:
            if chave not in aceitas:
                self.anotar(f"{onde}.{chave}", CENA_CHAVE_DESCONHECIDA)

    def nome(self, valor: object, onde: str) -> str:
        if not isinstance(valor, str) or len(valor) > NOME_MAXIMO or _CONTROLE.search(valor):
            self.anotar(f"{onde}.nome", CENA_NOME_INVALIDO)
            return ""
        return valor

    def passos(self, valor: object, onde: str) -> tuple[Passo, ...]:
        campo = f"{onde}.passos"
        if not isinstance(valor, list | tuple):
            self.anotar(campo, CENA_PASSOS_INVALIDOS)
            return ()
        if len(valor) > PASSOS_MAXIMOS:
            self.anotar(campo, CENA_PASSOS_DEMAIS)
            return ()
        lidos = [self.passo(item, f"{campo}[{indice}]") for indice, item in enumerate(valor)]
        return tuple(passo for passo in lidos if passo is not None)

    def passo(self, item: object, onde: str) -> Passo | None:
        if not isinstance(item, Mapping):
            self.anotar(onde, CENA_PASSO_NAO_OBJETO)
            return None
        self.chaves(item, CHAVES_PASSO, onde)
        espera = self.espera(item.get("espera_ms"), onde)
        equipamento = self.equipamento(item.get("equipamento"), onde)
        acao = self.acao(item.get("acao"), onde)
        if equipamento is None or acao is None:
            return None
        valor = item.get("valor")
        if not valor_valido(acao, valor):
            self.anotar(f"{onde}.valor", CENA_VALOR_INVALIDO)
            return None
        return Passo(equipamento=equipamento, acao=acao, valor=valor, espera_ms=espera)

    def equipamento(self, valor: object, onde: str) -> str | None:
        campo = f"{onde}.equipamento"
        if not _texto(valor, IDENTIDADE_MAXIMA):
            self.anotar(campo, CENA_EQUIPAMENTO_INVALIDO)
            return None
        if self._identidades is not None and valor not in self._identidades:
            # Why: a scene saved over an identity nobody registered is a button that will
            # never do anything, and the integrator is at the keyboard right now to fix it.
            # Por que: uma cena salva sobre uma identidade que ninguém cadastrou é um botão
            # que nunca vai fazer nada, e o integrador está no teclado agora para consertar.
            self.anotar(campo, CENA_EQUIPAMENTO_DESCONHECIDO)
            return None
        return valor

    def acao(self, valor: object, onde: str) -> str | None:
        if not isinstance(valor, str) or valor not in ACOES:
            self.anotar(f"{onde}.acao", CENA_ACAO_DESCONHECIDA)
            return None
        return valor

    def espera(self, valor: object, onde: str) -> int | None:
        # Why: an absent wait is the interval of the scene, so a file only names the waits
        # that differ from it.
        # Por que: uma espera ausente é o intervalo da cena, então um arquivo só nomeia as
        # esperas que diferem dele.
        if valor is None:
            return None
        if not _milissegundos(valor):
            self.anotar(f"{onde}.espera_ms", CENA_ESPERA_INVALIDA)
            return None
        return valor

    def intervalo(self, valor: object, onde: str) -> int:
        if not _milissegundos(valor):
            self.anotar(f"{onde}.intervalo_ms", CENA_INTERVALO_INVALIDO)
            return INTERVALO_PADRAO_MS
        return valor


def valor_valido(acao: str, valor: object) -> bool:
    """The value against what the action of section 6 takes, and nothing wider.

    O valor contra o que a ação da seção 6 recebe, e nada mais largo.
    """
    if acao in SEM_VALOR:
        return valor is None
    if acao == "volume":
        return type(valor) is int and mapa.VALOR_MINIMO <= valor <= mapa.VALOR_MAXIMO
    if acao == "temperatura":
        return type(valor) is int and TEMPERATURA_MINIMA <= valor <= TEMPERATURA_MAXIMA
    if acao == "mudo":
        return type(valor) is bool
    if acao == "tecla":
        return isinstance(valor, str) and valor in TECLAS
    if acao == "vento":
        return isinstance(valor, str) and valor in VENTOS
    if acao == ACAO_GRUPO:
        # Why: the empty value is solo, so a scene can take a group down by name.
        # Por que: o valor vazio é solo, então uma cena consegue desfazer um grupo pelo nome.
        return isinstance(valor, str) and (valor == "" or _texto(valor, IDENTIDADE_MAXIMA))
    return acao in COM_TEXTO and _texto(valor, VALOR_TEXTO_MAXIMO)


def _milissegundos(valor: object) -> bool:
    # Why: the JSON true is an int for Python, and it is not a millisecond count.
    # Por que: o true do JSON é int para o Python, e não é uma contagem de milissegundos.
    return type(valor) is int and 0 <= valor <= ESPERA_MAXIMA_MS


def _texto(valor: object, maximo: int) -> bool:
    return (
        isinstance(valor, str)
        and 0 < len(valor) <= maximo
        and not _CONTROLE.search(valor)
        and _gravavel(valor)
    )


def _gravavel(texto: str) -> bool:
    """False for the lone surrogate JSON accepts and UTF-8 cannot write back to the bridge.

    Falso para o surrogado solto que o JSON aceita e o UTF-8 não sabe devolver para a ponte.
    """
    try:
        texto.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _causa(erro: BaseException) -> str:
    return str(erro) or type(erro).__name__
