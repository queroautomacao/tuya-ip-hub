# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""A scene of section 8: DATA, a list of steps that each set one data point of the map.

The rule section 7 fixes for a driver holds here for the same reason: what an integrator
saves is data and never program. A step names one data point and one value, plus an optional
wait in milliseconds after it, and that is the whole vocabulary. No condition, no loop, no
expression, no arithmetic. A step that sets DP 131 is refused on top of that, because a
scene starting a scene is a loop written in data, and two of them naming each other would be
a hub that never stops.

Section 8 gives the ceiling: DP 131 is an enum of cena1 to cena8, so there are eight scenes
and the POSITION of a scene is its number. A scene that was erased leaves its slot empty
instead of pulling the next one back, because the shift would silently move scene 3 to scene
2 in every automation the customer already built on the platform. DP 134 carries the names
of all of them in one string of at most 255 bytes, so a name that does not fit there is
refused when it is saved and never cut when it is published.

Running one is fire and forget for whoever asked: the answer comes at once and the steps run
in order on a task of their own. A step that fails is logged with its stable code and the
scene goes on, because a projector that is off must not stop the lights of the same scene.

Uma cena da seção 8: DADO, uma lista de passos que ajustam um data point do mapa cada.

A regra que a seção 7 fixa para um driver vale aqui pelo mesmo motivo: o que um integrador
salva é dado e nunca programa. Um passo nomeia um data point e um valor, mais uma espera
opcional em milissegundos depois dele, e esse é o vocabulário inteiro. Sem condicional, sem
laço, sem expressão, sem aritmética. Um passo que ajusta o DP 131 é recusado ainda por cima,
porque uma cena que dispara uma cena é um laço escrito em dado, e duas delas nomeando uma à
outra seriam um hub que nunca para.

A seção 8 dá o teto: o DP 131 é um enum de cena1 a cena8, então são oito cenas e a POSIÇÃO de
uma cena é o número dela. Uma cena apagada deixa a vaga vazia em vez de puxar a seguinte,
porque o empurrão moveria em silêncio a cena 3 para a cena 2 em toda automação que o cliente
já montou na plataforma. O DP 134 carrega os nomes de todas elas numa string de no máximo 255
bytes, então um nome que não cabe lá é recusado ao ser salvo e nunca cortado ao ser publicado.

Rodar uma é disparar e esquecer para quem pediu: a resposta sai na hora e os passos correm em
ordem numa tarefa própria. Um passo que falha é registrado com o código estável dele e a cena
segue, porque um projetor desligado não pode parar as luzes da mesma cena.
"""

import asyncio
import functools
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from iphub.dpbus import mapa, protocolo

log = logging.getLogger("iphub.cenas")

# Section 8 numbers cena1 to cena8 on DP 131, and there is no ninth.
# A seção 8 numera cena1 a cena8 no DP 131, e não existe uma nona.
MAXIMO = mapa.CENAS

# Why: the whole of section 8 one scene may set is four data points per block plus the group,
# twenty five of them, so this ceiling holds a scene that touches everything and still
# refuses a file that grew into a program.
# Por que: tudo da seção 8 que uma cena pode ajustar são quatro data points por bloco mais o
# grupo, vinte e cinco deles, então este teto cabe uma cena que toca em tudo e ainda recusa um
# arquivo que virou programa.
PASSOS_MAXIMOS = 32

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

MILISSEGUNDO_S = 0.001

CAMPO = "cenas"
CHAVES_CENA = ("nome", "passos", "intervalo_ms")
CHAVES_PASSO = ("dpid", "valor", "espera_ms")

# The stable codes a refusal carries, section 11: the daemon answers a code and never a
# phrase, and the panel translates it. The two of the map come with the verdict of DP 134,
# which belongs to the map and is not written a second time here.
# Os códigos estáveis que uma recusa carrega, seção 11: o daemon responde um código e nunca
# uma frase, e o painel o traduz. Os dois do mapa vêm com o veredito do DP 134, que é do mapa
# e não é escrito uma segunda vez aqui.
CENAS_NAO_LISTA = "cenas_nao_lista"
CENAS_DEMAIS = "cenas_demais"
CENA_NAO_OBJETO = "cena_nao_objeto"
CENA_CHAVE_DESCONHECIDA = "cena_chave_desconhecida"
CENA_NOME_INVALIDO = "cena_nome_invalido"
CENA_PASSOS_INVALIDOS = "cena_passos_invalidos"
CENA_PASSOS_DEMAIS = "cena_passos_demais"
CENA_PASSO_NAO_OBJETO = "cena_passo_nao_objeto"
CENA_DP_DESCONHECIDO = "cena_dp_desconhecido"
CENA_DP_SOMENTE_LEITURA = "cena_dp_somente_leitura"
CENA_DP_PROIBIDO = "cena_dp_proibido"
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
    CENA_DP_DESCONHECIDO,
    CENA_DP_SOMENTE_LEITURA,
    CENA_DP_PROIBIDO,
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

type Ajuste = Callable[[int, object], Awaitable[str | None]]
type Dormir = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Passo:
    """One step: one data point, one value and the pause that follows it.

    espera_ms is the pause AFTER the step, so a file reads in the order it happens: power the
    projector, wait for it, choose the input. None takes the interval of the scene. The wait
    of the LAST step is not slept, because nothing follows it and holding the task would only
    delay a shutdown.

    Um passo: um data point, um valor e a pausa que vem depois dele.

    espera_ms é a pausa DEPOIS do passo, para um arquivo se ler na ordem em que acontece:
    liga o projetor, espera por ele, escolhe a entrada. None toma o intervalo da cena. A
    espera do ÚLTIMO passo não é dormida, porque nada vem depois dela e segurar a tarefa só
    atrasaria um desligamento.
    """

    dpid: int
    valor: object
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


def validar(dados: object) -> tuple[Cena, ...]:
    """The scenes as typed data, or CenasInvalidas listing EVERY problem at once.

    Nothing but CenasInvalidas leaves here, for any input at all: this judges what a route
    received and what a hand edited config.json holds, and a validation that raised anything
    else would take the boot of the appliance down with it.

    As cenas como dado tipado, ou CenasInvalidas listando TODO problema de uma vez.

    Nada além de CenasInvalidas sai daqui, para entrada nenhuma: isto julga o que uma rota
    recebeu e o que um config.json editado na mão guarda, e uma validação que estourasse
    outra coisa levaria o boot do appliance junto.
    """
    leitor = _Leitor()
    cenas = leitor.cenas(dados)
    if leitor.problemas:
        raise CenasInvalidas(tuple(leitor.problemas))
    return cenas


def nomes(cenas: Sequence[Cena]) -> tuple[str, ...]:
    """The names in the order of the scenes, which is what DP 134 publishes.

    Os nomes na ordem das cenas, que é o que o DP 134 publica.
    """
    return tuple(cena.nome for cena in cenas)


def numero_de(valor: object) -> int | None:
    """The scene number of a DP 131 value, or None for anything the enum does not name.

    O número de cena de um valor do DP 131, ou None para o que o enum não nomeia.
    """
    if not isinstance(valor, str) or valor not in mapa.VALORES_CENA:
        return None
    return mapa.VALORES_CENA.index(valor) + 1


class Executor:
    """Holds the saved scenes and runs one on a task of its own, one run at a time each.

    Guarda as cenas salvas e roda uma numa tarefa própria, uma execução por vez cada.
    """

    def __init__(
        self,
        cenas: Sequence[Cena],
        ajustar: Ajuste,
        *,
        dormir: Dormir = asyncio.sleep,
    ) -> None:
        self._cenas = tuple(cenas)
        self._ajustar = ajustar
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
        """The scene of a number from 1 to 8, or None for a number outside the contract.

        A cena de um número de 1 a 8, ou None para um número fora do contrato.
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
            # data points, and the volume the customer ends up with is whichever step landed
            # last; one run at a time is the only outcome that matches what was written.
            # Por que: a mesma cena duas vezes ao mesmo tempo intercalaria duas sequências
            # sobre os mesmos data points, e o volume que o cliente recebe é o do passo que
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
        # Why: the number comes from a route path or from the enum of DP 131, so True and
        # "2" reach here; neither is a scene number and neither may pick one by luck.
        # Por que: o número vem do caminho de uma rota ou do enum do DP 131, então True e "2"
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
            codigo = await self._ajustar(passo.dpid, passo.valor)
        except asyncio.CancelledError:
            raise
        except BaseException as erro:
            # Why: a device library raising outside Exception deep in a socket call would end
            # the scene at that step, and the lights of the same scene would never be reached;
            # one failed step is all a failure here is allowed to be.
            # Por que: uma biblioteca de aparelho estourando fora de Exception no fundo de uma
            # chamada de socket encerraria a cena naquele passo, e as luzes da mesma cena nunca
            # seriam alcançadas; uma falha aqui só pode ser um passo que falhou.
            log.warning("scene %s could not set dp %s: %s", numero, passo.dpid, _causa(erro))
            return
        if codigo is not None:
            log.warning("scene %s was refused on dp %s: %s", numero, passo.dpid, codigo)

    def _fim(self, numero: int, tarefa: asyncio.Task) -> None:
        if self._em_curso.get(numero) is tarefa:
            del self._em_curso[numero]


class _Leitor:
    """Collects every problem instead of stopping at the first, and never raises.

    Junta todo problema em vez de parar no primeiro, e nunca estoura.
    """

    def __init__(self) -> None:
        self.problemas: list[tuple[str, str]] = []

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
        """DP 134 carries every name in one string, so the list is judged whole and by the
        map, which owns the 255 bytes and the code that says why they do not fit.

        O DP 134 carrega todo nome numa string só, então a lista é julgada inteira e pelo
        mapa, que é dono dos 255 bytes e do código que diz por que eles não cabem.
        """
        try:
            mapa.nomes_json(mapa.NOMES_CENAS, nomes(cenas))
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
        dp = self.dp(item.get("dpid"), onde)
        if dp is None:
            return None
        valor = item.get("valor")
        if not _valor_valido(dp, valor):
            self.anotar(f"{onde}.valor", CENA_VALOR_INVALIDO)
            return None
        return Passo(dpid=dp.dpid, valor=valor, espera_ms=espera)

    def dp(self, dpid: object, onde: str) -> mapa.Dp | None:
        campo = f"{onde}.dpid"
        dp = mapa.de_dp(dpid)
        if dp is None:
            self.anotar(campo, CENA_DP_DESCONHECIDO)
            return None
        if not dp.ajustavel:
            # Why: section 8, the chip never echoes and a report is only ever born of real
            # state; a scene writing DP 104 would publish a speaker as online because a file
            # said so, and the bridge has no way to tell that from the truth.
            # Por que: seção 8, o chip nunca ecoa e um report só nasce de estado real; uma cena
            # escrevendo o DP 104 publicaria uma caixa como online porque um arquivo disse
            # isso, e a ponte não tem como distinguir aquilo da verdade.
            self.anotar(campo, CENA_DP_SOMENTE_LEITURA)
            return None
        if dp.dpid == mapa.CENA:
            self.anotar(campo, CENA_DP_PROIBIDO)
            return None
        return dp

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


def _milissegundos(valor: object) -> bool:
    # Why: the JSON true is an int for Python, and it is not a millisecond count.
    # Por que: o true do JSON é int para o Python, e não é uma contagem de milissegundos.
    return type(valor) is int and 0 <= valor <= ESPERA_MAXIMA_MS


def _valor_valido(dp: mapa.Dp, valor: object) -> bool:
    """The value against what the data point takes; the protocol of the bus owns the types.

    O valor contra o que o data point aceita; o protocolo do barramento é dono dos tipos.
    """
    if dp.tipo is mapa.Tipo.ENUM and not dp.valores:
        # Why: the values of the input of a block come from the hardware (section 14,
        # plm_support) and the map does not know them, so a speaker that was offline when the
        # scene was saved would have its input refused forever. The shape is judged here and
        # the value the speaker does not have is refused by the bus when the step runs, which
        # the scene logs with its code and walks past.
        # Por que: os valores da entrada de um bloco vêm do hardware (seção 14, plm_support) e
        # o mapa não os conhece, então uma caixa offline na hora de salvar teria a entrada dela
        # recusada para sempre. A forma é julgada aqui e o valor que a caixa não tem é recusado
        # pelo barramento quando o passo roda, o que a cena registra com o código e ultrapassa.
        return _texto_de_valor(valor)
    return protocolo.valor_valido(dp, valor)


def _texto_de_valor(valor: object) -> bool:
    return (
        isinstance(valor, str)
        and 0 < len(valor) <= VALOR_TEXTO_MAXIMO
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
