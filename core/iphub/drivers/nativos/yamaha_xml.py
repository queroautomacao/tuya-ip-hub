# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Yamaha receivers of the generation before MusicCast, over the XML control of the receiver.

What the protocol is, so nobody has to read it again:

- One POST of XML to http://<address>/YamahaRemoteControl/ctrl, no password of any kind. The
  envelope is <YAMAHA_AV cmd="GET|PUT"> around one zone element, and the answer carries an RC
  attribute where zero is success and anything else is a refusal of the receiver.
- The whole state of a zone comes in ONE question, Basic_Status, which answers power, input,
  mute and the volume, so a poll is one request and not five.
- The volume travels in TENTHS of a decibel: -805 is -80.5 dB. Setting one only accepts whole
  half decibel steps, which is why the value is rounded to them. The contract of the hub
  counts from zero to a hundred, so the two are converted here, and the ceiling in decibels
  is a field of the registration because the receiver does not answer what its own is.
- The inputs of THAT model are read from the receiver: Input_Sel_Item answers one item per
  input the model has, with the word the receiver takes and the name the owner gave it.
- A zone of the receiver is a registration of its own, exactly as MusicCast is: the element
  around the request is the zone, and Main_Zone is the one every model has.

This is not the MusicCast driver: these receivers only carry this XML control, and the ones
that carry both answer this one too.
"""

import logging
import re
from xml.etree import ElementTree

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import Cadastro, ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.yamaha_xml")

TIPO = "receiver_yamaha_xml"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

CAMINHO = "/YamahaRemoteControl/ctrl"
PORTA = 80
TEMPO_LIMITE_S = 4.0
CORPO_MAXIMO = 256 * 1024
FALHAS_ATE_OFFLINE = 2

CAMPO_ZONA = "zona"
CAMPO_DB_MAXIMO = "db_maximo"

ZONAS = ("Main_Zone", "Zone_2", "Zone_3", "Zone_4")
ZONA_PADRAO = ZONAS[0]

# Every model of this generation mutes at -80.0 dB; the ceiling is what changes between them.
DB_MINIMO = -80.0
DB_MAXIMO_PADRAO = 16.5
DECIMOS = 10.0
PASSO = 0.5

LIGADO = "On"
DESLIGADO = "Standby"
MUDO_LIGADO = "On"
MUDO_DESLIGADO = "Off"
DIRETO = "Straight"

PEDIR = "GetParam"
RC_OK = "0"

# A DTD is what an entity bomb needs, and a receiver never sends one.
_DTD = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_NUMERO = re.compile(r"^-?[0-9]+$")

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_MODO = "modo"
ACAO_ATALHO = "atalho"

TEXTOS = {
    "en": {
        "descricao": (
            "Yamaha receiver of the generation before MusicCast (RX-V, RX-A and the AVENTAGE "
            "of those years) over the XML control of the receiver, with no cloud and no "
            "account. One registration is ONE ZONE."
        ),
        "preparo": (
            "On the receiver: Network, and leave Network Standby on, or the receiver stops "
            "answering when it is off. On a model that also has MusicCast, the MusicCast "
            "driver is the one to use."
        ),
        "campo_zona": "Main_Zone, Zone_2, Zone_3 or Zone_4. One zone is one registration.",
        "campo_db_maximo": (
            "The ceiling of the volume in decibels, 16.5 on most models and 0.0 on some. The "
            "receiver does not answer what its own is, and a wrong one makes the level of the "
            "panel land above or below the level of the receiver."
        ),
        "cap_volume": "Converted from the decibels of the receiver, which mute at -80.0 dB.",
        "cap_atalho": "The scenes of the receiver, which is what its own remote calls a scene.",
        "cap_modo": "The sound program of the receiver, Straight being the one without any.",
    },
    "pt": {
        "descricao": (
            "Receiver Yamaha da geração anterior ao MusicCast (RX-V, RX-A e os AVENTAGE "
            "daqueles anos) pelo controle XML do próprio receiver, sem nuvem e sem conta. "
            "Um cadastro é UMA ZONA."
        ),
        "preparo": (
            "No receiver: Network, e deixe o Network Standby ligado, senão o receiver para de "
            "responder quando está desligado. Num modelo que também tem MusicCast, o driver "
            "de MusicCast é o que se usa."
        ),
        "campo_zona": "Main_Zone, Zone_2, Zone_3 ou Zone_4. Uma zona é um cadastro.",
        "campo_db_maximo": (
            "O teto do volume em decibéis, 16.5 na maioria dos modelos e 0.0 em alguns. O "
            "receiver não responde qual é o dele, e um teto errado faz o nível do painel cair "
            "acima ou abaixo do nível do receiver."
        ),
        "cap_volume": "Convertido dos decibéis do receiver, que emudece em -80.0 dB.",
        "cap_atalho": "As cenas do receiver, que é como o controle dele mesmo as chama.",
        "cap_modo": "O programa de som do receiver, sendo o Straight o sem programa nenhum.",
    },
}

SUGESTOES = (
    Sugestao("atalhos", "Cena 1", "Scene 1"),
    Sugestao("atalhos", "Cena 2", "Scene 2"),
    Sugestao("atalhos", "Cena 3", "Scene 3"),
    Sugestao("modos", "Direto", DIRETO),
    Sugestao("modos", "Filme", "Adventure"),
    Sugestao("modos", "Estereo", "2ch Stereo"),
)


class _Falha(Exception):
    """A stable code on its way out of an exchange with the receiver."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class YamahaXml(Driver):
    """One zone of a Yamaha receiver of the XML generation."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={
            "pt": "Receiver Yamaha (antes do MusicCast)",
            "en": "Yamaha receiver (before MusicCast)",
        },
        categoria="receiver",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_MODO,
            ACAO_ATALHO,
        ),
        config_campos=(
            Campo(nome=CAMPO_ZONA, tipo=TipoCampo.TEXTO, padrao=ZONA_PADRAO),
            Campo(nome=CAMPO_DB_MAXIMO, tipo=TipoCampo.TEXTO, padrao=str(DB_MAXIMO_PADRAO)),
        ),
        # The MusicCast driver is the one that claims the announcement of a Yamaha, because a
        # receiver that answers both is better served by it; this one is found by the range
        # sweep, which asks every address who it is.
        descoberta=Descoberta(),
        sugestoes=SUGESTOES,
        textos=TEXTOS,
        marca="Yamaha",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._entradas: dict[str, str] = {}
        self._falhas = 0
        self._fio = fio.Fio(log, cadastro.identidade)

    async def parar(self) -> None:
        sessao, self._sessao = self._sessao, None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    async def atualizar(self) -> None:
        try:
            await self._ler_estado()
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        self._falhas = 0

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            await self._mandar(f"<Power_Control><Power>{LIGADO}</Power></Power_Control>")
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._mandar(f"<Power_Control><Power>{DESLIGADO}</Power></Power_Control>")
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if type(valor) is not int or not 0 <= valor <= 100:
                return INVALID_VALUE
            decimos = self._para_o_aparelho(valor)
            await self._mandar(
                f"<Volume><Lvl><Val>{decimos}</Val><Exp>1</Exp><Unit>dB</Unit></Lvl></Volume>"
            )
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            palavra = MUDO_LIGADO if valor else MUDO_DESLIGADO
            await self._mandar(f"<Volume><Mute>{palavra}</Mute></Volume>")
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            entrada = self._entrada_valida(valor)
            if entrada is None:
                return INVALID_VALUE
            await self._mandar(f"<Input><Input_Sel>{entrada}</Input_Sel></Input>")
            self._defina(fonte=entrada)
            return None
        if acao == ACAO_MODO:
            programa = _palavra_simples(valor)
            if programa is None:
                return INVALID_VALUE
            # Straight is not a program, it is the absence of one, and the receiver takes it
            # in a field of its own; asking for it as a program is refused by the receiver.
            dentro = (
                f"<Straight>{LIGADO}</Straight>"
                if programa == DIRETO
                else f"<Sound_Program>{programa}</Sound_Program>"
            )
            await self._mandar(
                f"<Surround><Program_Sel><Current>{dentro}</Current></Program_Sel></Surround>"
            )
            self._defina(modo=programa)
            return None
        if acao == ACAO_ATALHO:
            cena = _palavra_simples(valor)
            if cena is None:
                return INVALID_VALUE
            await self._mandar(f"<Scene><Scene_Sel>{cena}</Scene_Sel></Scene>")
            return None
        return NAO_SUPORTADO

    async def _ler_estado(self) -> None:
        """One poll: the whole state of the zone in one question, plus the inputs once."""
        raiz = await self._pedir("GET", f"<Basic_Status>{PEDIR}</Basic_Status>", rotina=True)
        basico = _achar(raiz, "Basic_Status")
        if basico is None:
            raise _Falha(ERRO_APARELHO)
        if not self._entradas:
            await self._ler_entradas()
        energia = _texto_de(basico, "Power_Control/Power")
        decimos = _texto_de(basico, "Volume/Lvl/Val")
        programa = _texto_de(basico, "Surround/Program_Sel/Current/Sound_Program")
        direto = _texto_de(basico, "Surround/Program_Sel/Current/Straight")
        self._defina(
            online=True,
            ligado=None if not energia else energia == LIGADO,
            volume=self._do_aparelho(decimos),
            mudo=_sim_ou_nao(_texto_de(basico, "Volume/Mute")),
            fonte=_texto_de(basico, "Input/Input_Sel") or None,
            fontes=tuple(self._entradas),
            modo=DIRETO if direto == LIGADO else (programa or None),
            detalhe="",
        )

    async def _ler_entradas(self) -> None:
        """Which inputs this model HAS and what the owner named them, read from the receiver."""
        try:
            raiz = await self._pedir(
                "GET", f"<Input><Input_Sel_Item>{PEDIR}</Input_Sel_Item></Input>", rotina=True
            )
        except _Falha as falha:
            if falha.codigo == EQ_OFFLINE:
                raise
            log.debug("%s: the receiver did not list its inputs", self._id())
            return
        achadas: dict[str, str] = {}
        for item in raiz.iter("Input_Sel_Item_Info"):
            palavra = _texto_de(item, "Param")
            if palavra:
                achadas[palavra] = _texto_de(item, "Title") or palavra
        self._entradas = achadas
        log.info("%s: the receiver has inputs %s", self._id(), sorted(achadas))

    async def _mandar(self, dentro: str) -> None:
        await self._pedir("PUT", dentro)

    async def _pedir(self, comando: str, dentro: str, *, rotina: bool = False):
        """One exchange with the receiver, answered as the element of the zone."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        zona = self._zona()
        pedido = f'<YAMAHA_AV cmd="{comando}"><{zona}>{dentro}</{zona}></YAMAHA_AV>'
        self._fio.enviado(f"{comando} {dentro}", rotina=rotina)
        bruto = await self._enviar(f"http://{endereco}:{PORTA}{CAMINHO}", pedido, rotina=rotina)
        raiz = _arvore(bruto)
        if raiz is None:
            raise _Falha(ERRO_APARELHO)
        if raiz.get("RC", RC_OK) != RC_OK:
            # The receiver refuses a word it does not have, and that is not a broken device.
            self._fio.recusado(f"{comando} {dentro}", INVALID_VALUE)
            raise _Falha(INVALID_VALUE)
        da_zona = _achar(raiz, zona)
        if da_zona is None:
            raise _Falha(ERRO_APARELHO)
        return da_zona

    async def _enviar(self, url: str, pedido: str, *, rotina: bool) -> bytes:
        sessao = await self._abrir()
        try:
            async with sessao.post(
                url,
                data=pedido.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=UTF-8"},
                allow_redirects=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            self._fio.falhou(pedido, erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        if estado != 200:
            raise _Falha(ERRO_APARELHO)
        return bruto

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self._id(), self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._entradas = {}
        self._defina(online=False, detalhe=codigo)

    def _zona(self) -> str:
        bruto = self.cadastro.campos.get(CAMPO_ZONA, "").strip()
        return bruto if bruto in ZONAS else ZONA_PADRAO

    def _teto(self) -> float:
        bruto = self.cadastro.campos.get(CAMPO_DB_MAXIMO, "").strip()
        try:
            teto = float(bruto)
        except ValueError:
            return DB_MAXIMO_PADRAO
        return teto if DB_MINIMO < teto <= 30.0 else DB_MAXIMO_PADRAO

    def _do_aparelho(self, decimos: str) -> int | None:
        if not _NUMERO.match(decimos):
            return None
        db = int(decimos) / DECIMOS
        faixa = self._teto() - DB_MINIMO
        return max(0, min(100, round((db - DB_MINIMO) * 100 / faixa)))

    def _para_o_aparelho(self, valor: int) -> int:
        """The level of the contract as the tenths of a decibel the receiver takes.

        The receiver only accepts whole half decibel steps, so the decibels are snapped to
        them before they become tenths.
        """
        faixa = self._teto() - DB_MINIMO
        db = DB_MINIMO + valor * faixa / 100
        passos = round(db / PASSO)
        return int(passos * PASSO * DECIMOS)

    def _entrada_valida(self, valor: object) -> str | None:
        """The word the receiver takes, from that word or from the name the owner gave it."""
        palavra = _palavra_simples(valor)
        if palavra is None:
            return None
        if palavra in self._entradas:
            return palavra
        for oficial, nome in self._entradas.items():
            if nome.casefold() == palavra.casefold():
                return oficial
        return palavra if not self._entradas else None

    def _id(self) -> str:
        return self.cadastro.identidade

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The system id of the receiver, which it keeps for good and answers with no pairing."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        pedido = f'<YAMAHA_AV cmd="GET"><System><Config>{PEDIR}</Config></System></YAMAHA_AV>'
        try:
            async with (
                ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) as sessao,
                sessao.post(
                    f"http://{endereco}:{PORTA}{CAMINHO}",
                    data=pedido.encode("utf-8"),
                    headers={"Content-Type": "text/xml; charset=UTF-8"},
                    allow_redirects=False,
                ) as resposta,
            ):
                if resposta.status != 200:
                    return None
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
        except (TimeoutError, ClientError, OSError, ValueError):
            return None
        raiz = _arvore(bruto)
        if raiz is None or raiz.get("RC", RC_OK) != RC_OK:
            return None
        sistema = _achar(raiz, "System")
        if sistema is None:
            return None
        return _texto_de(sistema, "Config/System_ID") or None


def _arvore(bruto: bytes) -> ElementTree.Element | None:
    if not bruto or _DTD.search(bruto):
        return None
    try:
        return ElementTree.fromstring(bruto)
    except (ElementTree.ParseError, ValueError):
        return None


def _achar(raiz: ElementTree.Element, nome: str) -> ElementTree.Element | None:
    return raiz.find(nome)


def _texto_de(elemento: ElementTree.Element, caminho: str) -> str:
    achado = elemento.find(caminho)
    return (achado.text or "").strip() if achado is not None else ""


def _sim_ou_nao(palavra: str) -> bool | None:
    if palavra == MUDO_LIGADO:
        return True
    return False if palavra == MUDO_DESLIGADO else None


def _palavra_simples(valor: object) -> str | None:
    """A word of the receiver: text with no angle bracket and no control character in it.

    The word travels inside XML this driver builds, so what could close an element early is
    refused here instead of being escaped and sent as something else.
    """
    if not isinstance(valor, str):
        return None
    limpo = valor.strip()
    if not limpo or len(limpo) > 64:
        return None
    if any(caractere < " " for caractere in limpo) or set("<>&\"'") & set(limpo):
        return None
    return limpo
