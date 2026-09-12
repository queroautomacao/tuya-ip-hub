# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 6, 8 and 14: the numbers of every licence, the data points they publish and the
group of a licence of audio and video.

A number is only what says it is, one of the equipment numbers of a licence, which
any registered equipment of the right product may occupy, so there is no second registry
here: the numbers are an ORDER over identities already registered as equipment, the position
IS the number, and an empty string is a number nobody occupies. Removing an equipment empties
its slot instead of shifting the rest, because a shift moves an equipment from number 2 to
number 1 in every automation the customer already built on the platform, and nothing on the
bus would say it happened.

A licence of the product ar carries air conditioners and nothing else; a licence of the
product av carries everything else. What the data points of a number mean follows the
manifest of its driver: the power switch of an equipment that declares both power
capabilities, the level of one that declares volume, and so on.

The group logic the LinkPlay driver deliberately did not take lives here, per licence of
audio and video, and every rule of it was paid for on the bench:

- a group is formed by naming a MASTER, and only speakers of the same tipo are invited: a
mixed group is never offered, so a speaker of another kind is not even asked to join;
- a play on a slave dismantles the group, so the transport of a number that is a slave is
routed to the master;
- the volume of a slave goes through the master, never to the slave itself;
- a slave answers stop even while the group plays, so what the master plays is mirrored onto
every slave and read back from the state of the slave;
- a slave that left the multiroom mode for two polls in a row lost its group to a reboot or
to the application of the manufacturer, and the logical state is reconciled;
- a zombie group of a previous run, or of an address that changed under us, is sanitized on
boot before anything is published;
- forming, sanitizing and reconciling race each other, so ONE lock serializes all of it, and
a command of a number takes the same lock because the routing decision it makes (is this
number a slave, and who leads it) has to be the same one the group logic is holding.

The group data point carries the NUMBER of the master, which is what keeps it stable: a
group defined as an entry in a list would renumber itself the day a number is added, which is
the same silent move the empty slot exists to prevent. Multiroom is a capability of the
equipment, so only a number whose manifest declares it can lead or join.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from iphub.config import Cadastro, Licenca
from iphub.dpbus import comando, mapa, perfil, protocolo
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.gestor import EQ_NAO_ENCONTRADO, EQ_OFFLINE, ERRO_APARELHO, Gestor, com_teto
from iphub.drivers.manifesto import (
    CAPACIDADE_DE_GRUPO,
    CATEGORIA_DE_GRUPO,
    MODOS_AR,
    VENTOS,
    Estado,
    Manifesto,
    produto_de,
)

log = logging.getLogger("iphub.dpbus.numeros")

VAZIA = ""
SOLO = 0

# The lock is held while a speaker answers, so the deadline of one call into a driver is
# what keeps a box that accepted the connection and went quiet from freezing the group of the
# whole licence.
LIMITE_S = 5.0

# A speaker held in a group this hub does not lead is asked to leave, and
# records Ungroup as a command of the master, so a slave that ignores it would be asked
# again on every tick; one request a minute is a reminder, one a second is a flood.
ESPERA_DE_SAIDA_S = 60.0

# The actions a data point of a number turns into.
ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_TEMPERATURA = "temperatura"
ACAO_MODO = "modo"
ACAO_VENTO = "vento"
TRANSPORTE = ("tocar", "pausar", "parar", "proxima", "anterior")
# A play on a slave dismantles the group, and so does a radio or a preset
# pressed on it; everything that starts audio on a member of a group belongs to the master.
DO_MESTRE = (*TRANSPORTE, "atalho")

# The one action of a scene that is not a capability.
ACAO_GRUPO = "grupo"

# The functions of the map this module answers for.
F_LIGADO = "ligado"
F_TEMPERATURA = "temperatura"
F_MODO = "modo"
F_VENTO = "vento"
F_NIVEL = "nivel"
F_GRUPO = "grupo"
F_COMANDO = "comando"
F_ONLINE = "online"
F_MUDOS = "mudos"
F_ENTRADAS = "entradas"
F_MODOS = "modos"
F_TITULOS = "titulos"
F_PERFIS = "perfis"
F_NOMES = "nomes"
F_CENA = "cena"

# The stable codes an order refuses with; the panel translates them.
NUMEROS_DEMAIS = "numeros_demais"
NUMERO_REPETIDO = "numero_repetido"
NUMERO_OCUPADO = "numero_ocupado"
IDENTIDADE_INVALIDA = "identidade_invalida"
PRODUTO_INCOMPATIVEL = "produto_incompativel"
CODIGOS_DE_ORDEM = (
    NUMEROS_DEMAIS,
    NUMERO_REPETIDO,
    NUMERO_OCUPADO,
    EQ_NAO_ENCONTRADO,
    IDENTIDADE_INVALIDA,
    PRODUTO_INCOMPATIVEL,
    mapa.PERFIS_LONGOS,
)

# Everything aplicar may answer, and nothing else: the bus vocabulary plus the
# two codes that say the equipment itself refused.
CODIGOS = (
    protocolo.DP_DESCONHECIDO,
    protocolo.DP_SOMENTE_LEITURA,
    protocolo.VALOR_INVALIDO,
    protocolo.NUMERO_OFFLINE,
    protocolo.LICENCA_DESCONHECIDA,
    NAO_SUPORTADO,
    "auth_pendente",
    ERRO_APARELHO,
)

INVALID_VALUE = "invalid_value"

# The methods a speaker has to offer for a group to be made of it; a multiroom driver that
# does not carry them is a driver this module refuses to command instead of one it breaks.
MOVIMENTOS = (
    "e_escravo",
    "entrar_no_grupo",
    "desfazer_grupo",
    "tirar_do_grupo",
    "volume_de_escravo",
    "ler_grupo",
    "marcar_grupo",
    "espelhar",
    "saiu_do_grupo",
)


class OrdemInvalida(ValueError):
    """Carries the stable code the route answers with, so no route invents one of its own."""

    def __init__(self, codigo: str, detalhe: str) -> None:
        self.codigo = codigo
        super().__init__(f"{codigo}: {detalhe}")


@dataclass(frozen=True)
class _Alvo:
    """One filled number: the number, its registration and the driver mounted for it."""

    numero: int
    cadastro: Cadastro
    driver: Driver


def sem(ordem: Sequence[str], identidade: str) -> tuple[str, ...]:
    """The same order with that identity gone and its NUMBER still there, empty."""
    # Numbers by position, so closing the hole would move every equipment
    # below it one number up, in silence, on a bus a customer already automated.
    return tuple(VAZIA if atual == identidade else atual for atual in ordem)


def _identidade_em(ordem: tuple[str, ...], numero: int) -> str:
    """The identity a given order puts in one number, which is how a change is judged before
    it is written.
    """
    if numero < 1 or numero > len(ordem):
        return VAZIA
    return ordem[numero - 1]


class Numeros:
    """The numbers of ONE licence, the data points they publish and the group they may form."""

    def __init__(
        self,
        gestor: Gestor,
        licenca: Licenca,
        ordem: Iterable[str] = (),
        *,
        limite_s: float = LIMITE_S,
    ) -> None:
        self._gestor = gestor
        self._licenca = licenca
        self._ordem = tuple(ordem)
        self._limite_s = limite_s
        # Forming a group, sanitizing a zombie one on boot and reconciling one that
        # dissolved by itself all rewrite who leads whom, and the bench showed them landing on
        # top of each other; a command of a number reads the same book to decide its route.
        self._trava = asyncio.Lock()
        self._mestre = SOLO
        self._escravos: tuple[int, ...] = ()
        self._pedidos_de_saida: dict[int, float] = {}

    @property
    def licenca(self) -> Licenca:
        return self._licenca

    @property
    def id(self) -> str:
        return self._licenca.id

    @property
    def produto(self) -> str:
        return self._licenca.produto

    @property
    def capacidade(self) -> int:
        return mapa.NUMEROS[self.produto]

    @property
    def ordem(self) -> tuple[str, ...]:
        return self._ordem

    @property
    def multiroom(self) -> bool:
        """Only a licence of audio and video ever forms a group,."""
        return self.produto == mapa.PRODUTO_AV

    def trocar_licenca(self, licenca: Licenca) -> None:
        """Takes the edited identity of the licence, keeping the numbers and the group."""
        if licenca.produto != self._licenca.produto:
            raise ValueError("the product of a licence never changes")
        self._licenca = licenca

    def identidade(self, numero: int) -> str:
        """The identity occupying one number, or the empty string when nobody occupies it."""
        if not 1 <= numero <= self.capacidade or numero > len(self._ordem):
            return VAZIA
        return self._ordem[numero - 1]

    def numero(self, identidade: str) -> int:
        """The number one identity occupies, or 0 for an identity that occupies none."""
        if not identidade:
            return 0
        for posicao, atual in enumerate(self._ordem, start=1):
            if atual == identidade:
                return posicao
        return 0

    def ocupadas(self) -> tuple[str, ...]:
        return tuple(identidade for identidade in self._ordem if identidade)

    def grupo(self) -> int:
        """The value of the group data point right now: 0 solo, n led by number n."""
        return self._mestre

    def escravos(self) -> tuple[int, ...]:
        return self._escravos

    def segue_um_mestre(self, identidade: str) -> bool:
        """Whether this equipment is a slave of the group of this licence right now."""
        return self.numero(identidade) in self._escravos

    def validar(self, ordem: object, alheias: Iterable[str] = ()) -> tuple[str, ...]:
        """The order as it would be saved, or OrdemInvalida with the code that refused it.

        alheias are the identities occupying a number of ANOTHER licence, which this one may
        not take: one equipment in two numbers of the installation would answer two data
        points, and the bridge would read a device that contradicts itself.
        """
        if not isinstance(ordem, list | tuple):
            raise OrdemInvalida(IDENTIDADE_INVALIDA, f"the order is a list, found {ordem!r}")
        lista: list[str] = []
        for bruto in ordem:
            if not isinstance(bruto, str):
                raise OrdemInvalida(IDENTIDADE_INVALIDA, f"an identity is text, found {bruto!r}")
            lista.append(bruto.strip())
        if len(lista) > self.capacidade:
            raise OrdemInvalida(
                NUMEROS_DEMAIS,
                f"the product {self.produto} numbers {self.capacidade}, found {len(lista)}",
            )
        ocupadas = [identidade for identidade in lista if identidade]
        repetidas = sorted({i for i in ocupadas if ocupadas.count(i) > 1})
        if repetidas:
            raise OrdemInvalida(NUMERO_REPETIDO, f"the identidade {repetidas} occupies two numbers")
        tomadas = set(alheias)
        cadastros = self._cadastros()
        for identidade in ocupadas:
            if identidade not in cadastros:
                raise OrdemInvalida(
                    EQ_NAO_ENCONTRADO, f"{identidade!r} is not a registered equipment"
                )
            if identidade in tomadas:
                raise OrdemInvalida(
                    NUMERO_OCUPADO, f"{identidade!r} already occupies a number of another licence"
                )
            manifesto = self._gestor.manifesto(identidade)
            # An air conditioner only enters a licence of ar and everything
            # else only enters a licence of av; a manifest that left the image cannot be
            # judged, and a number is not emptied on boot because a driver failed to load.
            if manifesto is not None and produto_de(manifesto.categoria) != self.produto:
                raise OrdemInvalida(
                    PRODUTO_INCOMPATIVEL,
                    f"{identidade!r} does not belong in a licence of {self.produto}",
                )
        if self.multiroom and not self._perfis_cabem(tuple(lista), cadastros, None):
            raise OrdemInvalida(
                mapa.PERFIS_LONGOS, "the profiles of these numbers do not fit their strings"
            )
        return tuple(lista)

    async def definir_ordem(self, ordem: object, alheias: Iterable[str] = ()) -> tuple[str, ...]:
        """Saves the order after validating it, and drops a group a number just left."""
        nova = self.validar(ordem, alheias)
        async with self._trava:
            # The group has to be taken down while the OLD order still resolves the
            # master, because _multiroom reads the order: rewriting it first makes the master
            # unreachable, so multiroom:Ungroup never reaches the wire and the speakers stay
            # physically grouped forever while the hub publishes solo.
            await self._conferir_membros(nova)
            self._ordem = nova
        return nova

    async def esquecer(self, identidade: str) -> tuple[str, ...]:
        """The number of a removed equipment stays there, empty, and its group is dismantled."""
        async with self._trava:
            nova = sem(self._ordem, identidade)
            await self._conferir_membros(nova)
            self._ordem = nova
        return self._ordem

    async def desligar(self) -> None:
        """The licence is leaving the installation: its group falls, whatever the master says."""
        async with self._trava:
            await self._desfazer(forcar=True)

    def perfis_cabem(self, substituto: Cadastro | None = None) -> bool:
        """True when the profiles of this licence still pack with one registration replaced,
        which is what a route checks before writing an edited registration.
        """
        if not self.multiroom:
            return True
        return self._perfis_cabem(self._ordem, self._cadastros(), substituto)

    def valores(self) -> dict[int, object]:
        """Every reportable data point of this licence, ready for a report or a snapshot."""
        if self.produto == mapa.PRODUTO_AR:
            return self._valores_ar()
        return self._valores_av()

    async def aplicar(self, dpid: object, valor: object) -> str | None:
        """One set on this licence, done or refused with a stable code; nothing
        raises out.
        """
        dp = mapa.de_dp(self.produto, dpid)
        if dp is None:
            return protocolo.DP_DESCONHECIDO
        if not dp.ajustavel:
            return protocolo.DP_SOMENTE_LEITURA
        if dp.funcao == F_CENA:
            # The scene belongs to the module that owns the scenes; a numbers module that
            # answered for it would run a scene from the wrong book.
            return protocolo.DP_DESCONHECIDO
        if not protocolo.valor_valido(dp, valor):
            return protocolo.VALOR_INVALIDO
        async with self._trava:
            if dp.funcao == F_GRUPO:
                return await self._ativar(valor)
            if dp.funcao == F_COMANDO:
                return await self._comando(valor)
            return await self._no_numero(dp, valor)

    async def acionar(self, identidade: str, acao: str, valor: object) -> str | None:
        """One action of a scene on an equipment of this licence, routed through the group the
        way a data point would be.
        """
        async with self._trava:
            if acao == ACAO_GRUPO:
                return await self._grupo_por_nome(identidade, valor)
            alvo = self._alvo(self.numero(identidade))
            if alvo is None:
                return protocolo.NUMERO_OFFLINE
            if acao == ACAO_VOLUME:
                return await self._volume(alvo, valor)
            if acao in DO_MESTRE:
                return await self._transporte(alvo, acao, valor)
            return await self._executar(identidade, acao, valor)

    async def reler(self, dpid: object, valor: object = None) -> None:
        """Asks the equipment that owns a data point for its state, out of turn and awaited.

        The reread is a check against the DEVICE. Publishing from the cache
        1.5 s after the command compares the optimistic value against a cache the command
        itself wrote, so the check agreed with the guess every time and a speaker that
        accepted a volume and ignored it kept the wrong value on the bus until the next poll.
        """
        dp = mapa.de_dp(self.produto, dpid)
        if dp is None:
            return
        numero = dp.numero
        if dp.funcao == F_COMANDO:
            lido = comando.ler(valor, self.capacidade)
            numero = 0 if lido is None else lido.numero
        identidade = self.identidade(numero) if numero else VAZIA
        if identidade:
            await self._gestor.visitar_e_esperar(identidade)

    def escravos_alheios(self) -> tuple[int, ...]:
        """The numbers whose speaker is in multiroom slave mode of a group this hub does NOT
        lead, which is a state the customer can reach with the app of the manufacturer, or a
        lost reply to a join, or a restart while a group was up.

        A speaker in that mode refuses volume, transport, preset and input, and nothing
        here ever put it there, so reporting it as solo drew a panel full of controls that
        only ever answer no, with nothing anywhere saying why.
        """
        nossos = {self._mestre, *self._escravos}
        alheios = []
        for alvo in self._multirooms():
            if alvo.numero in nossos:
                continue
            if alvo.driver.e_escravo():
                alheios.append(alvo.numero)
        return tuple(alheios)

    async def sanear(self) -> None:
        """Boot: a group left behind by a previous run is taken down before anything else."""
        if not self.multiroom:
            return
        async with self._trava:
            self._mestre = SOLO
            self._escravos = ()
            alvos = tuple(self._multirooms())
            for alvo in alvos:
                alvo.driver.marcar_grupo(False)
            # This runs before the listening socket opens, and measured
            # /health answering in about 7 s on the reference appliance. Asking the speakers
            # one after the other spends a deadline per speaker, so a site whose boxes are
            # unreachable (a VLAN change, a router reboot) had no panel for half a minute,
            # which is exactly when the operator needs it most. Asking them together costs the
            # slowest one instead of the sum.
            grupos = await asyncio.gather(
                *(self._ler(alvo.driver.ler_grupo()) for alvo in alvos),
                return_exceptions=True,
            )
            lideres = [
                alvo
                for alvo, grupo in zip(alvos, grupos, strict=True)
                if not isinstance(grupo, BaseException) and getattr(grupo, "escravos", ())
            ]
            for alvo in lideres:
                # A zombie group of a previous run, or one an address change
                # left behind, answers commands nobody in this run asked for; the hub only
                # publishes a state it knows, so the physical group goes down first.
                log.warning("number %d led a group nobody asked for, taking it down", alvo.numero)
            await asyncio.gather(
                *(self._chamar(alvo.driver.desfazer_grupo()) for alvo in lideres),
                return_exceptions=True,
            )
            await self._recuperar_alheios()

    async def sincronizar(self) -> None:
        """Reconciles a group that dissolved by itself and mirrors the master onto the slaves."""
        if not self.multiroom:
            return
        async with self._trava:
            # A speaker the customer grouped with the application of the manufacturer,
            # hours after boot, is held in a group this hub does not lead, and this used to
            # return before ever looking because our books say solo, which is exactly the case
            # in question.
            await self._recuperar_alheios()
            if not self._mestre:
                return
            mestre = self._multiroom(self._mestre)
            if mestre is None:
                self._soltar()
                return
            restantes = []
            for numero in self._escravos:
                escravo = self._multiroom(numero)
                if escravo is None:
                    continue
                # A slave out of the multiroom mode for two polls in a row
                # lost the group to a reboot of the master or to the application of the
                # manufacturer; keeping it in our books would route its volume through a
                # master that no longer commands it.
                if escravo.driver.saiu_do_grupo():
                    log.warning("number %d left the group of number %d", numero, self._mestre)
                    escravo.driver.marcar_grupo(False)
                    continue
                restantes.append(numero)
            self._escravos = tuple(restantes)
            if not restantes:
                await self._desfazer()
                return
            self._espelhar(mestre)

    def _valores_ar(self) -> dict[int, object]:
        """The data points of a licence of air conditioners, read from the typed state."""
        estados = self._gestor.estados()
        valores: dict[int, object] = {}
        online = []
        for numero in range(1, self.capacidade + 1):
            identidade = self.identidade(numero)
            estado = estados.get(identidade) if identidade else None
            # A number nobody occupies publishes nothing at all, because a bridge that
            # read a false there would show an empty number as a machine that is switched off.
            if estado is None:
                continue
            if estado.online:
                online.append(numero)
            if estado.ligado is not None:
                valores[self._dp(F_LIGADO, numero)] = estado.ligado
            if type(estado.temperatura) is int and self._cabe(F_TEMPERATURA, estado.temperatura):
                valores[self._dp(F_TEMPERATURA, numero)] = estado.temperatura
            if estado.modo in MODOS_AR:
                valores[self._dp(F_MODO, numero)] = estado.modo
            if estado.vento in VENTOS:
                valores[self._dp(F_VENTO, numero)] = estado.vento
        valores[self._dp(F_ONLINE)] = mapa.bits(online)
        nomes = _nomes_json_encurtado(
            mapa.CHAVE_NOMES_MAQUINAS, self._nomes(), mapa.NUMEROS[mapa.PRODUTO_AR]
        )
        if nomes is not None:
            valores[self._dp(F_NOMES)] = nomes
        return valores

    def _valores_av(self) -> dict[int, object]:
        """The data points of a licence of audio and video, read from the typed state."""
        estados = self._gestor.estados()
        cadastros = self._cadastros()
        valores: dict[int, object] = {}
        online: list[int] = []
        mudos: list[int] = []
        entradas: dict[int, int] = {}
        modos: dict[int, int] = {}
        titulos: dict[int, str] = {}
        for numero in range(1, self.capacidade + 1):
            identidade = self.identidade(numero)
            estado = estados.get(identidade) if identidade else None
            cadastro = cadastros.get(identidade)
            if estado is None or cadastro is None:
                continue
            if estado.online:
                online.append(numero)
            # An always-on equipment (one whose manifest does not declare the
            # power pair) stays silent on its power data point instead of publishing a state
            # nobody can change.
            if estado.ligado is not None and self._com_energia(identidade):
                valores[self._dp(F_LIGADO, numero)] = estado.ligado
            if estado.volume is not None:
                valores[self._dp(F_NIVEL, numero)] = estado.volume
            if estado.mudo:
                mudos.append(numero)
            indice = _indice_de(cadastro, "entradas", estado.fonte)
            if indice:
                entradas[numero] = indice
            indice = _indice_de(cadastro, "modos", estado.modo)
            if indice:
                modos[numero] = indice
            if estado.tocando:
                titulos[numero] = estado.tocando
        valores[self._dp(F_GRUPO)] = self.grupo()
        valores[self._dp(F_ONLINE)] = mapa.bits(online)
        valores[self._dp(F_MUDOS)] = mapa.bits(mudos)
        valores[self._dp(F_ENTRADAS)] = mapa.pares(entradas)
        valores[self._dp(F_MODOS)] = mapa.pares(modos)
        valores[self._dp(F_TITULOS)] = mapa.titulos(titulos)
        try:
            partes = mapa.empacotar(self._perfis(self._ordem, cadastros, None))
        except mapa.NomesInvalidos as erro:
            # The routes refuse a registration whose profiles do not pack, so this is a
            # config.json edited by hand; the strings stay off the bus instead of leaving cut.
            log.error("licence %s cannot publish its profiles: %s", self.id, erro)
        else:
            for indice, parte in enumerate(partes, start=1):
                valores[self._dp(F_PERFIS, indice=indice)] = parte
        return valores

    def _perfis(
        self,
        ordem: tuple[str, ...],
        cadastros: Mapping[str, Cadastro],
        substituto: Cadastro | None,
    ) -> tuple[str, ...]:
        """The profile of every occupied number whose manifest is known,."""
        perfis = []
        for numero, identidade in enumerate(ordem, start=1):
            cadastro = cadastros.get(identidade) if identidade else None
            if substituto is not None and substituto.identidade == identidade:
                cadastro = substituto
            manifesto = self._manifesto(cadastro)
            if cadastro is None or manifesto is None:
                continue
            perfis.append(perfil.montar(numero, cadastro, manifesto))
        return tuple(perfis)

    def _perfis_cabem(
        self,
        ordem: tuple[str, ...],
        cadastros: Mapping[str, Cadastro],
        substituto: Cadastro | None,
    ) -> bool:
        try:
            mapa.empacotar(self._perfis(ordem, cadastros, substituto))
        except mapa.NomesInvalidos:
            return False
        return True

    def _manifesto(self, cadastro: Cadastro | None) -> Manifesto | None:
        # The profile is judged for the registration as it WILL be, so the manifest is
        # the one of its tipo and never the one of the tipo the gestor still holds for it.
        if cadastro is None:
            return None
        return self._gestor.manifesto_de_tipo(cadastro.tipo)

    def _nomes(self) -> list[str]:
        """The names of the numbers up to the last one somebody occupies."""
        cadastros = self._cadastros()
        ocupados = [numero for numero in range(1, self.capacidade + 1) if self.identidade(numero)]
        ultimo = max(ocupados) if ocupados else 0
        nomes = []
        for numero in range(1, ultimo + 1):
            cadastro = cadastros.get(self.identidade(numero))
            nomes.append(cadastro.nome if cadastro is not None and cadastro.nome else VAZIA)
        return nomes

    def _dp(self, funcao: str, numero: int = 0, indice: int = 0) -> int:
        return mapa.dp_de(self.produto, funcao, numero, indice)

    def _cabe(self, funcao: str, valor: int) -> bool:
        dp = mapa.de_dp(self.produto, self._dp(funcao, 1))
        return dp is not None and dp.minimo <= valor <= dp.maximo

    def _com_transporte(self, identidade: str) -> bool:
        """The transport of a driver that declares both transport capabilities;
        half of the pair is no transport at all.
        """
        return self._declara(identidade, ACAO_TOCAR, ACAO_PAUSAR)

    def _com_energia(self, identidade: str) -> bool:
        """The power switch of a driver that declares both power capabilities; a
        switch that turns on and cannot turn off is a switch the customer cannot trust.
        """
        return self._declara(identidade, ACAO_LIGAR, ACAO_DESLIGAR)

    def _declara(self, identidade: str, *acoes: str) -> bool:
        manifesto = self._gestor.manifesto(identidade)
        if manifesto is None:
            return False
        return all(acao in manifesto.capacidades for acao in acoes)

    def _companheiras(self, numero: int) -> tuple[int, ...]:
        """The numbers a group led by this one may hold: same tipo, and never a mixed one."""
        cadastros = self._cadastros()
        mestre = cadastros.get(self.identidade(numero))
        if mestre is None or not self._e_multiroom(mestre.identidade):
            return ()
        companheiras = []
        for outro in range(1, self.capacidade + 1):
            cadastro = cadastros.get(self.identidade(outro))
            # A group only ever exists between speakers of the same domain,
            # so a speaker of another kind is never even invited; offering a mixed group is
            # what leaves half of it playing and the other half silent.
            if outro != numero and cadastro is not None and cadastro.tipo == mestre.tipo:
                companheiras.append(outro)
        return tuple(companheiras)

    async def _ativar(self, valor: object) -> str | None:
        if not self.multiroom:
            return NAO_SUPORTADO
        numero = int(valor) if type(valor) is int else -1
        if numero < 0 or numero > self.capacidade:
            return protocolo.VALOR_INVALIDO
        if numero == SOLO:
            return await self._desfazer()
        return await self._formar(numero)

    async def _grupo_por_nome(self, identidade: str, valor: object) -> str | None:
        """The group action of a scene: THIS equipment joins the master named in the value, or
        leaves the group when the value is the empty string.

        A master carries up to seven slaves and the customer picks them one
        by one, so a scene picks them one step at a time: "kitchen: group = living room" is
        one member joining, and the step that names the master with an empty value is the one
        that takes the whole group down.
        """
        if not self.multiroom:
            return NAO_SUPORTADO
        proprio = self.numero(identidade)
        if not proprio:
            return protocolo.NUMERO_OFFLINE
        if valor == VAZIA or valor is None:
            return await self._sair(proprio)
        if not isinstance(valor, str):
            return protocolo.VALOR_INVALIDO
        numero = self.numero(valor)
        if not numero or numero == proprio:
            return protocolo.VALOR_INVALIDO
        membros = self._escravos if self._mestre == numero else ()
        return await self._formar(numero, (*membros, proprio))

    async def _sair(self, numero: int) -> str | None:
        """One number leaves the group: the master takes the whole group down with it, and a
        member only takes itself out.
        """
        if not self._mestre or numero == self._mestre:
            return await self._desfazer()
        if numero not in self._escravos:
            return None
        return await self._formar(
            self._mestre, tuple(outro for outro in self._escravos if outro != numero)
        )

    async def formar(self, mestre: object, membros: Sequence[int] | None = None) -> str | None:
        """The group of this licence as the panel sets it: who leads and who follows."""
        async with self._trava:
            if not self.multiroom:
                return NAO_SUPORTADO
            if type(mestre) is not int or mestre < 0 or mestre > self.capacidade:
                return protocolo.VALOR_INVALIDO
            if mestre == SOLO:
                return await self._desfazer()
            return await self._formar(mestre, membros)

    async def _formar(self, numero: int, membros: Sequence[int] | None = None) -> str | None:
        """Forms the group led by one number with the members the customer chose; with no
        choice, every speaker of the tipo of the master joins it.

        A LinkPlay master carries up to seven slaves and the customer picks
        them one by one, so the group of a licence is a master and a SET of members. Re-forming
        the same group with a different set moves only the difference: the ones that left are
        taken out of the master and the ones that arrived are invited, and whoever stays never
        hears a gap.
        """
        mestre = self._multiroom(numero)
        if mestre is None:
            # A number whose equipment cannot group answers the code of a capability the
            # manifest does not declare; offline is only for a number nothing answers for.
            return NAO_SUPORTADO if self._alvo(numero) is not None else protocolo.NUMERO_OFFLINE
        if not mestre.cadastro.ip:
            return protocolo.NUMERO_OFFLINE
        companheiras = self._companheiras(numero)
        if not companheiras:
            # A group of one is not a group, and a bus that answered ok for it would
            # publish a group the customer cannot hear.
            return NAO_SUPORTADO
        if membros is None:
            escolhidos: tuple[int, ...] = companheiras
        else:
            fora = [membro for membro in membros if membro not in companheiras]
            if fora:
                # A number that is not a companion is an empty slot, another tipo or the
                # master itself, and inviting it would be a group the customer cannot hear.
                return protocolo.VALOR_INVALIDO
            escolhidos = tuple(dict.fromkeys(membros))
        if not escolhidos:
            # A group of one is not a group, so choosing nobody is asking for solo.
            return await self._desfazer()
        presentes = [alvo for alvo in map(self._multiroom, escolhidos) if alvo is not None]
        if not presentes:
            return protocolo.NUMERO_OFFLINE
        # The members that are staying are invited again on purpose, because a speaker
        # that silently dropped out of the group heals on the next set of the same group;
        # joining a master a speaker already follows is the same move for this firmware.
        saida: str | None = None
        if self._mestre == numero:
            saida = await self._dispensar(mestre, escolhidos)
        if self._mestre and self._mestre != numero:
            codigo = await self._desfazer()
            if codigo is not None:
                # The old master refused or did not answer, so its slaves are still
                # physically playing its audio; a second group formed over them would leave
                # the first one with nobody in the books to take it down.
                return codigo
        antigos = self._escravos
        # Every slave joins the master on its own, so the invitations go out together and
        # a licence of twelve numbers costs the slowest speaker instead of the sum; the lock of
        # the licence is held meanwhile, and the publish loop waits on it.
        respostas = await asyncio.gather(
            *(self._chamar(alvo.driver.entrar_no_grupo(mestre.cadastro.ip)) for alvo in presentes)
        )
        entraram: list[int] = []
        recusa: str | None = saida
        for alvo, codigo in zip(presentes, respostas, strict=True):
            if codigo is None:
                alvo.driver.marcar_grupo(True)
                entraram.append(alvo.numero)
            else:
                log.warning("number %d did not join the group of number %d", alvo.numero, numero)
                recusa = recusa or codigo
        for antigo in antigos:
            if antigo in entraram or antigo == numero or antigo not in escolhidos:
                continue
            # A member of the group being re-formed that did not answer the invitation is
            # still a slave when the speaker says so, and the books keep it; only a member
            # that really left has its mark cleared, so nothing is evicted for a lost reply.
            alvo = self._multiroom(antigo)
            if alvo is not None and alvo.driver.e_escravo():
                entraram.append(antigo)
            else:
                self._largar((antigo,))
        if not entraram:
            # A master with nobody following it is not a group, so a choice that emptied
            # it takes the group down instead of publishing a leader of nobody.
            if self._mestre == numero and not self._escravos:
                await self._desfazer()
            return recusa
        # A member the master refused to take out is still following it, so it stays in
        # the books even though the choice left it out; the panel showing it as solo is what
        # would leave the customer with a speaker nobody can command.
        entraram.extend(
            preso for preso in self._escravos if preso not in escolhidos and preso not in entraram
        )
        entraram.sort()
        mestre.driver.marcar_grupo(True)
        self._mestre = numero
        self._escravos = tuple(entraram)
        self._espelhar(mestre)
        return saida

    async def _dispensar(self, mestre: _Alvo, escolhidos: Sequence[int]) -> str | None:
        """Takes out of the group the members the new choice left out, one by one on the
        master, so the ones that stay keep playing.
        """
        saem = [numero for numero in self._escravos if numero not in escolhidos]
        if not saem:
            return None
        respostas = await asyncio.gather(
            *(
                self._chamar(mestre.driver.tirar_do_grupo(self._endereco_de(numero)))
                for numero in saem
            )
        )
        ficaram = []
        recusa: str | None = None
        for numero, codigo in zip(saem, respostas, strict=True):
            if codigo is None:
                self._largar((numero,))
            else:
                # A member the master refused to take out is still physically playing the
                # audio of the group, and forgetting it here would leave the panel saying it
                # is solo while it follows a master nobody may command it through.
                log.warning("number %d was not taken out of the group", numero)
                ficaram.append(numero)
                recusa = recusa or codigo
        self._escravos = tuple(
            numero for numero in self._escravos if numero in escolhidos or numero in ficaram
        )
        return recusa

    def _endereco_de(self, numero: int) -> str:
        alvo = self._alvo(numero)
        return "" if alvo is None else alvo.cadastro.ip

    async def _desfazer(self, *, forcar: bool = False) -> str | None:
        """Dismantles the group from the MASTER, which is the only speaker that may do it.

        Forgetting the group when the master refused the command, or did not answer in
        time, tells the customer the speakers are apart while they are still playing together,
        and the retry then finds no group in the books and answers ok without touching the
        wire. The books are only cleared when the physical move landed. forcar is for the
        equipment that is leaving the installation anyway, where there is nothing left to
        retry with.
        """
        if not self._mestre:
            return None
        mestre = self._multiroom(self._mestre)
        codigo = None if mestre is None else await self._chamar(mestre.driver.desfazer_grupo())
        if codigo is not None and not forcar:
            log.warning("number %d refused to dismantle its group: %s", self._mestre, codigo)
            return codigo
        self._soltar()
        return codigo

    def _largar(self, numeros: Iterable[int]) -> None:
        """Takes numbers out of the group in our books, clearing the mark on each speaker.

        A number dropped from the books while its driver still believes it is in a group
        refuses transport and input forever, for a group nobody is in any more.
        """
        for numero in numeros:
            alvo = self._multiroom(numero)
            if alvo is not None:
                alvo.driver.marcar_grupo(False)

    def _soltar(self) -> None:
        """Forgets the group in our books and clears the mark on every speaker of it."""
        self._largar((self._mestre, *self._escravos))
        self._mestre = SOLO
        self._escravos = ()

    async def _conferir_membros(self, nova: tuple[str, ...]) -> None:
        """A group whose master or whose last slave leaves the order is not a group any more,
        and it is taken down while the CURRENT order can still reach the master.

        The books are kept by IDENTITY and never by position, because any registered
        equipment may take a number now; a projector put in the number of a slave would
        inherit its role and receive, as the slave, the volume meant for a speaker.
        """
        if not self._mestre:
            return
        if _identidade_em(nova, self._mestre) != self.identidade(self._mestre):
            await self._desfazer(forcar=True)
            return
        ficam = tuple(
            numero
            for numero in self._escravos
            if _identidade_em(nova, numero) == self.identidade(numero)
        )
        self._largar(numero for numero in self._escravos if numero not in ficam)
        self._escravos = ficam
        if not self._escravos:
            await self._desfazer(forcar=True)

    def _espelhar(self, mestre: _Alvo) -> None:
        """A slave answers stop even while it plays, so it reads what the master
        plays and never what it says about itself.
        """
        estado = self._estado(mestre.cadastro.identidade)
        tocando = None if estado is None else estado.tocando
        reproduzindo = None if estado is None else estado.reproduzindo
        for numero in self._escravos:
            alvo = self._multiroom(numero)
            if alvo is not None:
                alvo.driver.espelhar(tocando, reproduzindo)

    async def _recuperar_alheios(self) -> None:
        """Asks a speaker held in someone else's group to leave it, and says so when it stays.

        A speaker in that mode refuses volume, transport, preset and input, so leaving it
        there is leaving the number dead. records Ungroup as a command of the
        master, so it is not certain a slave obeys it, and the honest behaviour when it does
        not is to keep the number flagged instead of publishing it as an ordinary number.
        """
        agora = time.monotonic()
        for numero in self.escravos_alheios():
            alvo = self._multiroom(numero)
            if alvo is None:
                continue
            if agora - self._pedidos_de_saida.get(numero, -ESPERA_DE_SAIDA_S) < ESPERA_DE_SAIDA_S:
                continue
            self._pedidos_de_saida[numero] = agora
            log.warning(
                "number %d is a multiroom slave of a group this hub does not lead, "
                "asking it to leave",
                numero,
            )
            codigo = await self._chamar(alvo.driver.desfazer_grupo())
            if codigo is not None:
                log.warning(
                    "number %d would not leave the group it is held in: %s, so it refuses "
                    "every command until it does",
                    numero,
                    codigo,
                )

    async def _no_numero(self, dp: mapa.Dp, valor: object) -> str | None:
        """A set on a data point of one number, as the capability it is."""
        alvo = self._alvo(dp.numero)
        if alvo is None:
            return protocolo.NUMERO_OFFLINE
        identidade = alvo.cadastro.identidade
        if dp.funcao == F_LIGADO:
            if not self._com_energia(identidade):
                return NAO_SUPORTADO
            return await self._executar(identidade, ACAO_LIGAR if valor else ACAO_DESLIGAR, None)
        if dp.funcao == F_NIVEL:
            return await self._volume(alvo, valor)
        if dp.funcao == F_TEMPERATURA:
            return await self._executar(identidade, ACAO_TEMPERATURA, valor)
        if dp.funcao == F_MODO:
            return await self._executar(identidade, ACAO_MODO, valor)
        if dp.funcao == F_VENTO:
            return await self._executar(identidade, ACAO_VENTO, valor)
        return protocolo.DP_DESCONHECIDO

    async def _comando(self, valor: object) -> str | None:
        """One string of the command channel, as one capability on one equipment."""
        lido = comando.ler(valor, self.capacidade)
        if lido is None:
            return protocolo.VALOR_INVALIDO
        alvo = self._alvo(lido.numero)
        if alvo is None:
            return protocolo.NUMERO_OFFLINE
        identidade = alvo.cadastro.identidade
        if lido.acao in comando.COM_INDICE:
            itens = perfil.itens(alvo.cadastro, comando.COM_INDICE[lido.acao])
            if not 1 <= lido.indice <= len(itens):
                return protocolo.VALOR_INVALIDO
            escolhido = itens[lido.indice - 1].valor
            if lido.capacidade in DO_MESTRE:
                return await self._transporte(alvo, lido.capacidade, escolhido)
            return await self._executar(identidade, lido.capacidade, escolhido)
        if lido.acao == comando.ACAO_MUDO:
            # The mute of the channel toggles, because the panel has one
            # button and the state comes back by the report of the muted bits.
            estado = self._estado(identidade)
            return await self._executar(identidade, ACAO_MUDO, not (estado and estado.mudo))
        if lido.capacidade in DO_MESTRE:
            return await self._transporte(alvo, lido.capacidade, lido.valor)
        return await self._executar(identidade, lido.capacidade, lido.valor)

    async def _volume(self, alvo: _Alvo, valor: object) -> str | None:
        """The volume of a slave goes through the master, never to the slave."""
        mestre = self._mestre_de(alvo.numero)
        if mestre is None:
            return await self._executar(alvo.cadastro.identidade, ACAO_VOLUME, valor)
        # The road through the master does not pass the gestor, so the ceiling of the
        # slave is applied here, the one place that road goes through.
        valor = com_teto(alvo.cadastro, ACAO_VOLUME, valor)
        return await self._chamar(mestre.driver.volume_de_escravo(alvo.cadastro.ip, valor))

    async def _transporte(self, alvo: _Alvo, acao: str, valor: object) -> str | None:
        """A play, a radio or a preset on a slave dismantles the group, so what
        starts audio goes to the master.
        """
        mestre = self._mestre_de(alvo.numero)
        destino = alvo if mestre is None else mestre
        return await self._executar(destino.cadastro.identidade, acao, valor)

    def _mestre_de(self, numero: int) -> _Alvo | None:
        """The master of a number that is a slave right now, or None when it answers for
        itself.
        """
        if numero not in self._escravos:
            return None
        return self._multiroom(self._mestre)

    async def _executar(self, identidade: str, acao: str, valor: object) -> str | None:
        return traduzir(await self._gestor.executar(identidade, acao, valor))

    async def _chamar(self, chamada: Awaitable[str | None]) -> str | None:
        """One group move straight into a driver, with the deadline and with no exception out."""
        try:
            async with asyncio.timeout(self._limite_s):
                return traduzir(await chamada)
        except TimeoutError:
            # The same as the gestor, a speaker that did not answer within the deadline is
            # offline, and not a fault of the device nor a traceback; the deadline fires while
            # the call waits for the lock a poll of the same unreachable master holds.
            log.warning("a group move did not finish within %.1f s", self._limite_s)
            return protocolo.NUMERO_OFFLINE
        except Exception as erro:
            log.exception("a group move failed: %s", erro or type(erro).__name__)
            return ERRO_APARELHO

    async def _ler(self, chamada: Awaitable[object]) -> object:
        try:
            async with asyncio.timeout(self._limite_s):
                return await chamada
        except Exception as erro:
            log.warning("a speaker did not answer its group: %s", erro or type(erro).__name__)
            return None

    def _cadastros(self) -> dict[str, Cadastro]:
        return {cadastro.identidade: cadastro for cadastro in self._gestor.cadastros}

    def _estado(self, identidade: str) -> Estado | None:
        if not identidade:
            return None
        return self._gestor.estados().get(identidade)

    def _e_multiroom(self, identidade: str) -> bool:
        """What the manifest declares decides, and no second table is consulted."""
        manifesto = self._gestor.manifesto(identidade)
        if manifesto is None:
            return False
        return (
            manifesto.categoria == CATEGORIA_DE_GRUPO
            and CAPACIDADE_DE_GRUPO in manifesto.capacidades
        )

    def _alvo(self, numero: int) -> _Alvo | None:
        """The number as something that can be commanded, or None when nothing answers for
        it.
        """
        identidade = self.identidade(numero)
        cadastro = self._cadastros().get(identidade)
        driver = self._gestor.driver(identidade) if identidade else None
        if cadastro is None or driver is None:
            # An identity that is not registered any more is an empty number and not an
            # error of the bus, because the file may have been edited by hand.
            return None
        return _Alvo(numero=numero, cadastro=cadastro, driver=driver)

    def _multiroom(self, numero: int) -> _Alvo | None:
        """The number only when a group can really be made of what is mounted for it."""
        alvo = self._alvo(numero)
        if alvo is None or not self._e_multiroom(alvo.cadastro.identidade):
            return None
        if not all(hasattr(alvo.driver, movimento) for movimento in MOVIMENTOS):
            log.error("driver of number %d declares agrupar and offers no group move", numero)
            return None
        return alvo

    def _multirooms(self) -> tuple[_Alvo, ...]:
        alvos = (self._multiroom(numero) for numero in range(1, self.capacidade + 1))
        return tuple(alvo for alvo in alvos if alvo is not None)


class Licencas:
    """Every licence of the installation, each with its numbers, as one book."""

    def __init__(
        self,
        gestor: Gestor,
        licencas: Iterable[Licenca] = (),
        numeros: Mapping[str, Sequence[str]] | None = None,
        *,
        limite_s: float = LIMITE_S,
    ) -> None:
        self._gestor = gestor
        self._limite_s = limite_s
        self._por_id: dict[str, Numeros] = {}
        ordens = numeros or {}
        for licenca in licencas:
            self._por_id[licenca.id] = Numeros(
                gestor,
                licenca,
                self._confiavel(licenca, ordens.get(licenca.id, ())),
                limite_s=limite_s,
            )

    def _confiavel(self, licenca: Licenca, ordem: Sequence[str]) -> tuple[str, ...]:
        """The saved order with every number this module refuses left empty.

        The route validates an order and config.json does not, so an order edited by
        hand boots a hub whose numbers name an identity that is not registered at all, or the
        same one twice, or an equipment of the other product. A number it refuses is left
        empty instead of publishing a number nothing can command.
        """
        juiz = Numeros(self._gestor, licenca, limite_s=self._limite_s)
        aceitos: list[str] = []
        for identidade in tuple(ordem)[: juiz.capacidade]:
            try:
                juiz.validar([*aceitos, identidade], self._alheias(licenca.id))
            except OrdemInvalida as erro:
                log.warning(
                    "number %d of licence %s was dropped: %s", len(aceitos) + 1, licenca.id, erro
                )
                aceitos.append(VAZIA)
            else:
                aceitos.append(identidade)
        return tuple(aceitos)

    def _alheias(self, id_licenca: str) -> set[str]:
        """The identities occupying a number of any OTHER licence."""
        return {
            identidade
            for outra in self._por_id.values()
            if outra.id != id_licenca
            for identidade in outra.ocupadas()
        }

    def ids(self) -> tuple[str, ...]:
        return tuple(self._por_id)

    def todas(self) -> tuple[Numeros, ...]:
        return tuple(self._por_id.values())

    def de(self, id_licenca: object) -> Numeros | None:
        if not isinstance(id_licenca, str):
            return None
        return self._por_id.get(id_licenca)

    def produto_de(self, id_licenca: object) -> str | None:
        numeros = self.de(id_licenca)
        return None if numeros is None else numeros.produto

    def onde(self, identidade: str) -> tuple[str, int] | None:
        """The licence and the number one equipment occupies, or None for one that occupies
        none.
        """
        for numeros in self._por_id.values():
            numero = numeros.numero(identidade)
            if numero:
                return numeros.id, numero
        return None

    def numeros(self) -> dict[str, tuple[str, ...]]:
        """The order of every licence, which is what config.json persists."""
        return {id_licenca: numeros.ordem for id_licenca, numeros in self._por_id.items()}

    def adicionar(self, licenca: Licenca) -> Numeros:
        if licenca.id in self._por_id:
            raise ValueError(f"licence {licenca.id!r} already exists")
        numeros = Numeros(self._gestor, licenca, limite_s=self._limite_s)
        self._por_id[licenca.id] = numeros
        return numeros

    def trocar(self, licenca: Licenca) -> None:
        numeros = self._por_id[licenca.id]
        numeros.trocar_licenca(licenca)

    async def remover(self, id_licenca: str) -> None:
        """Takes the licence out of the book after its group fell; the equipment stays."""
        numeros = self._por_id.get(id_licenca)
        if numeros is None:
            return
        await numeros.desligar()
        del self._por_id[id_licenca]

    def validar_ordem(self, id_licenca: str, ordem: object) -> tuple[str, ...]:
        numeros = self._por_id[id_licenca]
        return numeros.validar(ordem, self._alheias(id_licenca))

    async def definir_ordem(self, id_licenca: str, ordem: object) -> tuple[str, ...]:
        numeros = self._por_id[id_licenca]
        return await numeros.definir_ordem(ordem, self._alheias(id_licenca))

    async def esquecer(self, identidade: str) -> dict[str, tuple[str, ...]]:
        """The number of a removed equipment stays empty in whichever licence held it."""
        # A licence removed while a master takes its deadline would change the book under
        # this loop, so it walks a copy.
        for numeros in self.todas():
            if numeros.numero(identidade):
                await numeros.esquecer(identidade)
        return self.numeros()

    def segue_um_mestre(self, identidade: str) -> bool:
        """Whether the licence that holds this equipment has it following a master right now."""
        onde = self.onde(identidade)
        return onde is not None and self._por_id[onde[0]].segue_um_mestre(identidade)

    async def formar(
        self, id_licenca: str, mestre: object, membros: Sequence[int] | None = None
    ) -> str | None:
        """The group of one licence with the members the panel chose."""
        numeros = self.de(id_licenca)
        if numeros is None:
            return protocolo.LICENCA_DESCONHECIDA
        return await numeros.formar(mestre, membros)

    def perfis_cabem(self, substituto: Cadastro) -> bool:
        """True when an edited registration still packs in the licence that holds it."""
        onde = self.onde(substituto.identidade)
        if onde is None:
            return True
        return self._por_id[onde[0]].perfis_cabem(substituto)

    def valores(self, id_licenca: str) -> dict[int, object]:
        numeros = self._por_id.get(id_licenca)
        return {} if numeros is None else numeros.valores()

    async def aplicar(self, id_licenca: object, dpid: object, valor: object) -> str | None:
        numeros = self.de(id_licenca)
        if numeros is None:
            return protocolo.LICENCA_DESCONHECIDA
        return await numeros.aplicar(dpid, valor)

    async def acionar(self, identidade: str, acao: str, valor: object) -> str | None:
        """One action of a scene: through the licence that holds the equipment when one does,
        straight to the gestor when none does.
        """
        onde = self.onde(identidade)
        if onde is not None:
            return await self._por_id[onde[0]].acionar(identidade, acao, valor)
        if acao == ACAO_GRUPO:
            return NAO_SUPORTADO
        return traduzir(await self._gestor.executar(identidade, acao, valor))

    async def reler(self, id_licenca: object, dpid: object, valor: object = None) -> None:
        numeros = self.de(id_licenca)
        if numeros is not None:
            await numeros.reler(dpid, valor)

    async def sanear(self) -> None:
        await asyncio.gather(*(numeros.sanear() for numeros in self.todas()))

    async def sincronizar(self) -> None:
        for numeros in self.todas():
            await numeros.sincronizar()


def traduzir(codigo: str | None) -> str | None:
    """A code in the vocabulary the bus speaks."""
    if codigo is None:
        return None
    if codigo in (EQ_OFFLINE, EQ_NAO_ENCONTRADO):
        # On the bus an equipment that did not answer and a number whose equipment is
        # gone are the same thing to the bridge, which asked a number and got no number.
        return protocolo.NUMERO_OFFLINE
    if codigo == INVALID_VALUE:
        return protocolo.VALOR_INVALIDO
    if codigo in CODIGOS:
        return codigo
    log.error("a driver answered %r, outside the vocabulary of the bus", codigo)
    return ERRO_APARELHO


def _indice_de(cadastro: Cadastro, lista: str, valor: str | None) -> int:
    """The 1-based position of a driver value in a list of the registration, 0 for none."""
    if not valor:
        return 0
    for indice, item in enumerate(perfil.itens(cadastro, lista), start=1):
        if item.valor == valor:
            return indice
    return 0


def _nomes_json_encurtado(chave: str, nomes: Sequence[str], limite: int) -> str | None:
    """The names of a string DP inside its 255 bytes, shortened only when they do not fit."""
    try:
        return mapa.nomes_json(chave, nomes, limite)
    except mapa.NomesInvalidos:
        pass
    # The names of the machines are the names of the equipment, which the registration
    # takes long and in any alphabet; refusing the whole DP would take the names of EIGHT
    # machines off the bus because one of them is long, so each name is shortened to its fair
    # share of the budget instead, on a character boundary, and the JSON always reaches the
    # bridge whole. The budget is squeezed until the encoded JSON really fits, because json
    # escapes a quote as \" and a backslash as \\, and a budget measured in raw bytes lies.
    try:
        moldura = len(mapa.nomes_json(chave, [VAZIA] * len(nomes), limite).encode("utf-8"))
    except mapa.NomesInvalidos:
        log.error("the names of the machines have no room for even the empty names")
        return None
    orcamento = (mapa.TEXTO_MAXIMO_BYTES - moldura) // max(len(nomes), 1)
    while orcamento > 0:
        try:
            return mapa.nomes_json(chave, [_encurtar(nome, orcamento) for nome in nomes], limite)
        except mapa.NomesInvalidos:
            orcamento -= 1
    log.error("the names of the machines do not fit even shortened")
    return None


def _encurtar(nome: str, orcamento: int) -> str:
    """The name inside a byte budget, never cut inside a character."""
    if orcamento <= 0:
        return VAZIA
    bruto = nome.encode("utf-8", errors="ignore")
    if len(bruto) <= orcamento:
        return bruto.decode("utf-8")
    return bruto[:orcamento].decode("utf-8", errors="ignore")
