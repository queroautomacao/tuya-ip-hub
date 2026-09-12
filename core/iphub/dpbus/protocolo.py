# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The frames of the DP-bus, as pure functions over dicts and nothing else.

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

# The stable codes an ack carries, the daemon never answers a phrase.
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

# The id is echoed in the ack, so a client that sent a megabyte of id would be answered
# with a megabyte back on every frame; a correlation number does not need more than this.
ID_MAXIMO = 64

# The api_token is a token_urlsafe of 32 bytes, which is 43 ASCII
# characters; anything outside ASCII cannot be it, and comparing it in constant time would
# raise on a non ASCII string instead of answering that it does not match.
TOKEN_MAXIMO = 256
LICENCA_MAXIMO = 40

_CODIGO = re.compile(r"[a-z0-9_]{1,40}")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

# Sentinel of an id the contract does not accept, told apart from an absent id, which is None.
_ID_RECUSADO = object()


@dataclass(frozen=True)
class Auth:
    """The first frame already read: empty strings for anything that is not one."""

    token: str = ""
    licenca: str = ""


@dataclass(frozen=True)
class Pedido:
    """One set frame that already passed the map: the DP exists, takes a set and takes this
    value.
    """

    dp: mapa.Dp
    valor: object


@dataclass(frozen=True)
class Leitura:
    """What one client frame turned into: a request to run, a query, or a refusal with its
    code.

    The id travels apart from the request because an ack answers a frame that was refused
    too, and a client waiting on that id would hang otherwise.
    """

    id: object = None
    pedido: Pedido | None = None
    codigo: str = ""
    consulta: bool = False


def ler_auth(bruto: object) -> Auth:
    """The token and the licence of the first frame, or an empty Auth for anything else."""
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
    ack answers with. The product says which table the dpid is read against.
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
    # The JSON true is an int for Python and 101.0 is a float, and neither is a data
    # point number; taking either would set number 1 from a malformed frame.
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
    """The value against the type and the range the DP declares, and nothing
    wider.
    """
    if dp.tipo is mapa.Tipo.VALOR:
        # True is an int for Python and would land as the level 1 of a number.
        return type(valor) is int and dp.minimo <= valor <= dp.maximo
    if dp.tipo is mapa.Tipo.BOOL:
        return type(valor) is bool
    if dp.tipo is mapa.Tipo.ENUM:
        return isinstance(valor, str) and valor in dp.valores
    # The one string a client sets is the command channel, which the module that owns
    # the numbers reads word by word; what is judged here is that it is a short printable
    # string at all, so a megabyte of text never reaches a parser.
    return (
        isinstance(valor, str)
        and 0 < len(valor) <= mapa.TEXTO_MAXIMO_BYTES
        and not _CONTROLE.search(valor)
        and _gravavel(valor)
    )


def ack(identificador: object, codigo: str | None = None) -> dict:
    """The answer to one set: ok with no code, or the stable code that refused it."""
    if codigo is not None and not (isinstance(codigo, str) and _CODIGO.fullmatch(codigo)):
        # The daemon answers a code the panel translates and never a phrase;
        # a message that leaked in here would reach the bridge as if it were vocabulary.
        raise ValueError(f"an ack carries a stable code and never a phrase, found {codigo!r}")
    return {"t": T_ACK, "id": identificador, "ok": codigo is None, "code": codigo}


def report(dp: mapa.Dp, valor: object, ts: float) -> dict:
    """One published state. Refuses a DP the chip would never confirm,."""
    if not dp.reportavel:
        # The chip never echoes a received DP, so a report of a send only command or
        # scene would publish a state no device confirmed; whoever built it has a defect.
        raise ValueError(f"dp {dp.dpid} is not reportable")
    return {"t": T_REPORT, "dpid": dp.dpid, "v": _valor_de_report(dp, valor), "ts": int(ts)}


def snapshot(produto: str, valores: Mapping[int, object], identificador: object = None) -> dict:
    """Everything the slice of one licence holds that may be reported, in the order of
    .

    A data point with no value yet is absent instead of null, because a bridge that read a
    null would take it for a state and turn an empty number into an equipment that is off.
    """
    # A JSON object key is a string, so the number travels as text and a bridge reads
    # dps["101"] in any language instead of depending on how one of them parses a key.
    dps = {
        str(dpid): valores[dpid]
        for dpid in mapa.reportaveis(produto)
        if dpid in valores and valores[dpid] is not None
    }
    return {"t": T_SNAPSHOT, "id": identificador, "dps": dps}


def _valor_de_report(dp: mapa.Dp, valor: object) -> object:
    """The value on the wire, refused where the contract does not take it."""
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
    """The id as it goes back in the ack, or the sentinel of one the contract refuses."""
    if valor is None or type(valor) is int:
        return valor
    if isinstance(valor, str) and len(valor) <= ID_MAXIMO and _gravavel(valor):
        return valor
    return _ID_RECUSADO


def _gravavel(texto: str) -> bool:
    """False for the lone surrogate a client can send and UTF-8 cannot write back."""
    # The id is echoed in the ack, so a surrogate accepted here comes back out of the
    # socket as an encoding error on a frame the client is waiting for.
    try:
        texto.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
