# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 8: the frames of the DP-bus, as pure functions over dicts and nothing else.

No socket lives here, so every rule of the protocol is tested without a network and the
WebSocket of the bus stays thin: it reads a message, hands the object to this module and
sends back what it answers.

What the client sends: {"t":"auth","token":...,"licenca":...} as the FIRST frame and never
in the URL, then {"t":"set","id":..,"dpid":..,"v":..} and {"t":"consulta","id":..}. What the
server sends: {"t":"ack","id":..,"ok":..,"code":..}, {"t":"report","dpid":..,"v":..,"ts":..}
and {"t":"snapshot","id":..,"dps":..}.

A frame that is not an object, that carries no t, an unknown t or a dpid that is not a
number is refused with a stable code and never with an exception, because the other end is
whatever bridge implemented the public contract and one bad frame must not drop a socket
that is carrying a whole licence. A key the contract does not name is ignored instead of
refused, for the same reason: this is a wire protocol other people implement, not a file
this repository validates.

Seção 8: os quadros do DP-bus, como funções puras sobre dicionários e nada mais.

Socket nenhum vive aqui, então toda regra do protocolo é testada sem rede e o WebSocket do
barramento fica fino: ele lê uma mensagem, entrega o objeto a este módulo e devolve o que
ele responder.

O que o cliente manda: {"t":"auth","token":...,"licenca":...} como PRIMEIRO quadro e nunca
na URL, depois {"t":"set","id":..,"dpid":..,"v":..} e {"t":"consulta","id":..}. O que o
servidor manda: {"t":"ack","id":..,"ok":..,"code":..}, {"t":"report","dpid":..,"v":..,
"ts":..} e {"t":"snapshot","id":..,"dps":..}.

Um quadro que não é objeto, que não carrega t, que carrega um t desconhecido ou um dpid que
não é número é recusado com um código estável e nunca com exceção, porque do outro lado está
a ponte que alguém implementou do contrato público e um quadro ruim não pode derrubar um
socket que carrega uma licença inteira. Uma chave que o contrato não nomeia é ignorada em
vez de recusada, pelo mesmo motivo: isto é protocolo de fio que outros implementam, não um
arquivo que este repositório valida.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from iphub.dpbus import mapa

T_AUTH = "auth"
T_SET = "set"
T_CONSULTA = "consulta"
T_ACK = "ack"
T_REPORT = "report"
T_SNAPSHOT = "snapshot"

# The stable codes an ack carries, section 11: the daemon never answers a phrase.
# Os códigos estáveis que um ack carrega, seção 11: o daemon nunca responde frase.
DP_DESCONHECIDO = "dp_desconhecido"
DP_SOMENTE_LEITURA = "dp_somente_leitura"
VALOR_INVALIDO = "valor_invalido"
NUMERO_OFFLINE = "numero_offline"
NAO_AUTENTICADO = "nao_autenticado"
FRAME_INVALIDO = "frame_invalido"
LICENCA_DESCONHECIDA = "licenca_desconhecida"
CODIGOS = (
    DP_DESCONHECIDO,
    DP_SOMENTE_LEITURA,
    VALOR_INVALIDO,
    NUMERO_OFFLINE,
    NAO_AUTENTICADO,
    FRAME_INVALIDO,
    LICENCA_DESCONHECIDA,
)

# Why: the id is echoed in the ack, so a client that sent a megabyte of id would be answered
# with a megabyte back on every frame; a correlation number does not need more than this.
# Por que: o id volta no ack, então um cliente que mandasse um megabyte de id receberia um
# megabyte de volta a cada quadro; um número de correlação não precisa de mais que isto.
ID_MAXIMO = 64

# Why: the api_token of section 9 is a token_urlsafe of 32 bytes, which is 43 ASCII
# characters; anything outside ASCII cannot be it, and comparing it in constant time would
# raise on a non ASCII string instead of answering that it does not match.
# Por que: o api_token da seção 9 é um token_urlsafe de 32 bytes, que são 43 caracteres
# ASCII; nada fora do ASCII pode ser ele, e compará-lo em tempo constante estouraria numa
# string não ASCII em vez de responder que não casa.
TOKEN_MAXIMO = 256
LICENCA_MAXIMO = 40

_CODIGO = re.compile(r"[a-z0-9_]{1,40}")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

# Sentinel of an id the contract does not accept, told apart from an absent id, which is None.
# Sentinela de um id que o contrato não aceita, distinto de um id ausente, que é None.
_ID_RECUSADO = object()


@dataclass(frozen=True)
class Auth:
    """The first frame already read: empty strings for anything that is not one.

    O primeiro quadro já lido: strings vazias para o que não for um.
    """

    token: str = ""
    licenca: str = ""


@dataclass(frozen=True)
class Pedido:
    """One set frame that already passed the map: the DP exists, takes a set and takes this
    value.

    Um quadro set que já passou pelo mapa: o DP existe, aceita set e aceita este valor.
    """

    dp: mapa.Dp
    valor: object


@dataclass(frozen=True)
class Leitura:
    """What one client frame turned into: a request to run, a query, or a refusal with its
    code.

    The id travels apart from the request because an ack answers a frame that was refused
    too, and a client waiting on that id would hang otherwise.

    No que um quadro de cliente virou: um pedido para executar, uma consulta, ou uma recusa
    com o código.

    O id viaja separado do pedido porque um ack responde também a um quadro recusado, e um
    cliente esperando por aquele id ficaria pendurado sem isso.
    """

    id: object = None
    pedido: Pedido | None = None
    codigo: str = ""
    consulta: bool = False


def ler_auth(bruto: object) -> Auth:
    """The token and the licence of the first frame, or an empty Auth for anything else.

    O token e a licença do primeiro quadro, ou um Auth vazio para o que não for um.
    """
    if not isinstance(bruto, Mapping) or bruto.get("t") != T_AUTH:
        return Auth()
    token = bruto.get("token")
    licenca = bruto.get("licenca")
    if not isinstance(token, str) or not token or len(token) > TOKEN_MAXIMO:
        return Auth()
    if not token.isascii():
        return Auth()
    if not isinstance(licenca, str) or not licenca or len(licenca) > LICENCA_MAXIMO:
        return Auth()
    return Auth(token=token, licenca=licenca)


def ler_quadro(bruto: object, produto: str) -> Leitura:
    """One frame after the auth as a request or a query, or a refusal carrying the code the
    ack answers with. The product says which table of section 8 the dpid is read against.

    Um quadro depois do auth como pedido ou consulta, ou uma recusa com o código com que o
    ack responde. O produto diz contra qual tabela da seção 8 o dpid é lido.
    """
    if not isinstance(bruto, Mapping):
        return Leitura(codigo=FRAME_INVALIDO)
    identificador = _identificador(bruto.get("id"))
    if identificador is _ID_RECUSADO:
        return Leitura(codigo=FRAME_INVALIDO)
    tipo = bruto.get("t")
    if tipo == T_CONSULTA:
        return Leitura(id=identificador, consulta=True)
    if tipo != T_SET:
        return Leitura(id=identificador, codigo=FRAME_INVALIDO)
    # Why: the JSON true is an int for Python and 101.0 is a float, and neither is a data
    # point number; taking either would set number 1 from a malformed frame.
    # Por que: o true do JSON é int para o Python e 101.0 é float, e nenhum dos dois é número
    # de data point; aceitar qualquer um ajustaria o número 1 por quadro malformado.
    dpid = bruto.get("dpid")
    if type(dpid) is not int:
        return Leitura(id=identificador, codigo=FRAME_INVALIDO)
    dp = mapa.de_dp(produto, dpid)
    if dp is None:
        return Leitura(id=identificador, codigo=DP_DESCONHECIDO)
    if not dp.ajustavel:
        return Leitura(id=identificador, codigo=DP_SOMENTE_LEITURA)
    valor = bruto.get("v")
    if not valor_valido(dp, valor):
        return Leitura(id=identificador, codigo=VALOR_INVALIDO)
    return Leitura(id=identificador, pedido=Pedido(dp=dp, valor=valor))


def valor_valido(dp: mapa.Dp, valor: object) -> bool:
    """The value against the type and the range the DP declares in section 8, and nothing
    wider.

    O valor contra o tipo e a faixa que o DP declara na seção 8, e nada mais largo.
    """
    if dp.tipo is mapa.Tipo.VALOR:
        # Why: True is an int for Python and would land as the level 1 of a number.
        # Por que: True é int para o Python e chegaria como o nível 1 de um número.
        return type(valor) is int and dp.minimo <= valor <= dp.maximo
    if dp.tipo is mapa.Tipo.BOOL:
        return type(valor) is bool
    if dp.tipo is mapa.Tipo.ENUM:
        return isinstance(valor, str) and valor in dp.valores
    # Why: the one string a client sets is the command channel, which the module that owns
    # the numbers reads word by word; what is judged here is that it is a short printable
    # string at all, so a megabyte of text never reaches a parser.
    # Por que: a única string que um cliente ajusta é o canal de comando, que o módulo dono
    # dos números lê palavra por palavra; o que se julga aqui é que é uma string curta e
    # imprimível, para um megabyte de texto nunca chegar a um analisador.
    return (
        isinstance(valor, str)
        and 0 < len(valor) <= mapa.TEXTO_MAXIMO_BYTES
        and not _CONTROLE.search(valor)
        and _gravavel(valor)
    )


def ack(identificador: object, codigo: str | None = None) -> dict:
    """The answer to one set: ok with no code, or the stable code that refused it.

    A resposta a um set: ok sem código, ou o código estável que o recusou.
    """
    if codigo is not None and not (isinstance(codigo, str) and _CODIGO.fullmatch(codigo)):
        # Why: section 11, the daemon answers a code the panel translates and never a phrase;
        # a message that leaked in here would reach the bridge as if it were vocabulary.
        # Por que: seção 11, o daemon responde um código que o painel traduz e nunca uma
        # frase; uma mensagem que vazasse aqui chegaria à ponte como se fosse vocabulário.
        raise ValueError(f"an ack carries a stable code and never a phrase, found {codigo!r}")
    return {"t": T_ACK, "id": identificador, "ok": codigo is None, "code": codigo}


def report(dp: mapa.Dp, valor: object, ts: float) -> dict:
    """One published state. Refuses a DP the chip would never confirm, section 8.

    Um estado publicado. Recusa um DP que o chip nunca confirmaria, seção 8.
    """
    if not dp.reportavel:
        # Why: the chip never echoes a received DP, so a report of a send only command or
        # scene would publish a state no device confirmed; whoever built it has a defect.
        # Por que: o chip nunca ecoa um DP recebido, então um report de comando ou cena, que
        # são só de envio, publicaria estado que aparelho nenhum confirmou; quem o montou
        # tem defeito.
        raise ValueError(f"dp {dp.dpid} is not reportable in section 8")
    return {"t": T_REPORT, "dpid": dp.dpid, "v": _valor_de_report(dp, valor), "ts": int(ts)}


def snapshot(produto: str, valores: Mapping[int, object], identificador: object = None) -> dict:
    """Everything the slice of one licence holds that may be reported, in the order of
    section 8.

    A data point with no value yet is absent instead of null, because a bridge that read a
    null would take it for a state and turn an empty number into an equipment that is off.

    Tudo que a fatia de uma licença guarda e pode ser reportado, na ordem da seção 8.

    Um data point ainda sem valor fica ausente em vez de nulo, porque uma ponte que lesse um
    nulo o tomaria por estado e tornaria um número vazio num equipamento desligado.
    """
    # Why: a JSON object key is a string, so the number travels as text and a bridge reads
    # dps["101"] in any language instead of depending on how one of them parses a key.
    # Por que: chave de objeto JSON é string, então o número viaja como texto e uma ponte lê
    # dps["101"] em qualquer linguagem em vez de depender de como uma delas lê a chave.
    dps = {
        str(dpid): valores[dpid]
        for dpid in mapa.reportaveis(produto)
        if dpid in valores and valores[dpid] is not None
    }
    return {"t": T_SNAPSHOT, "id": identificador, "dps": dps}


def _valor_de_report(dp: mapa.Dp, valor: object) -> object:
    """The value on the wire, refused where the contract does not take it.

    O valor no fio, recusado onde o contrato não o aceita.
    """
    if dp.tipo is mapa.Tipo.TEXTO:
        if not isinstance(valor, str):
            raise ValueError(f"dp {dp.dpid} carries a string, found {type(valor).__name__}")
        if not _gravavel(valor):
            raise ValueError(f"dp {dp.dpid} carries a character UTF-8 cannot write")
        if len(valor.encode("utf-8")) > mapa.TEXTO_MAXIMO_BYTES:
            raise ValueError(f"dp {dp.dpid} would carry more than {mapa.TEXTO_MAXIMO_BYTES} bytes")
        return valor
    if not valor_valido(dp, valor):
        raise ValueError(f"dp {dp.dpid} does not take {valor!r} as a {dp.tipo.value}")
    return valor


def _identificador(valor: object) -> object:
    """The id as it goes back in the ack, or the sentinel of one the contract refuses.

    O id como ele volta no ack, ou a sentinela de um que o contrato recusa.
    """
    if valor is None or type(valor) is int:
        return valor
    if isinstance(valor, str) and len(valor) <= ID_MAXIMO and _gravavel(valor):
        return valor
    return _ID_RECUSADO


def _gravavel(texto: str) -> bool:
    """False for the lone surrogate a client can send and UTF-8 cannot write back.

    Falso para o surrogado solto que um cliente pode mandar e o UTF-8 não sabe devolver.
    """
    # Why: the id is echoed in the ack, so a surrogate accepted here comes back out of the
    # socket as an encoding error on a frame the client is waiting for.
    # Por que: o id volta no ack, então um surrogado aceito aqui sai do socket como erro de
    # codificação num quadro que o cliente está esperando.
    try:
        texto.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
