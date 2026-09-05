# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""LG air conditioner over the ThinQ Connect API, the first cloud driver of section 1.

The Wi-Fi split of LG has NO local API: the manufacturer publishes only the cloud one, which
is the same door the lg_thinq integration of Home Assistant goes through. So this driver is
the exception section 1 opens, with the four conditions section 9 asks for: the host is a
constant of this file and never comes from the registration, the scheme is always https, a
redirect is refused, and the personal token is a secret of the registration that never goes
back to the panel and never lands in the log.

What the API does, section 14, so nobody has to read it again:

- the base is https://api-{aic|eic|kic}.lgthinq.com/ and the region comes from the country:
  BR and the Americas on aic, Europe, Africa and the Middle East on eic, Asia on kic;
- every request carries the token, the public api key of the SDK of LG, the country, a client
  id and a message id of its own; the answer is wrapped in {"response": {...}};
- devices lists the account, devices/{id}/profile says what THAT unit accepts,
  devices/{id}/state reads it and devices/{id}/control writes it;
- a command is a pair of resource and property: {"operation": {"airConOperationMode":
  "POWER_ON"}}, {"airConJobMode": {"currentJobMode": ...}}, {"temperature":
  {"targetTemperature": N}}, {"airFlow": {"windStrength": ...}};
- x-conditional-control asks the cloud to check the state before acting, and the
  documentation says to turn it OFF when the mode is changed to cool or heat on a unit that
  is already on, or the command comes back refused;
- a unit that is POWER_OFF accepts no command at all, so a scene turns it on first;
- the words of the modes and of the fan speeds change with the model, so this driver reads
  them from the profile of that unit and matches them against the vocabulary of section 6
  ignoring case and underscores, instead of carrying a table that is wrong on the next model.

Ar condicionado LG pela API ThinQ Connect, o primeiro driver de nuvem da seção 1.

O split Wi-Fi da LG NÃO tem API local: o fabricante publica só a de nuvem, que é a mesma porta
por onde passa a integração lg_thinq do Home Assistant. Então este driver é a exceção que a
seção 1 abre, com as quatro condições que a seção 9 cobra: o host é constante deste arquivo e
nunca vem do cadastro, o esquema é sempre https, redirecionamento é recusado, e o token
pessoal é segredo do cadastro que nunca volta ao painel e nunca cai no log.

O que a API faz, seção 14, para ninguém precisar ler de novo:

- a base é https://api-{aic|eic|kic}.lgthinq.com/ e a região vem do país: BR e as Américas em
  aic, Europa, África e Oriente Médio em eic, Ásia em kic;
- toda requisição leva o token, a chave pública de api do SDK da LG, o país, um id de cliente
  e um id de mensagem próprio; a resposta vem embrulhada em {"response": {...}};
- devices lista a conta, devices/{id}/profile diz o que AQUELA unidade aceita,
  devices/{id}/state lê e devices/{id}/control escreve;
- um comando é um par de recurso e propriedade: {"operation": {"airConOperationMode":
  "POWER_ON"}}, {"airConJobMode": {"currentJobMode": ...}}, {"temperature":
  {"targetTemperature": N}}, {"airFlow": {"windStrength": ...}};
- o x-conditional-control pede à nuvem conferir o estado antes de agir, e a documentação manda
  desligá-lo ao trocar o modo para refrigerar ou aquecer num aparelho já ligado, senão o
  comando volta recusado;
- uma unidade em POWER_OFF não aceita comando nenhum, então uma cena a liga primeiro;
- as palavras dos modos e das velocidades mudam com o modelo, então este driver as lê do
  profile daquela unidade e as casa com o vocabulário da seção 6 ignorando caixa e sublinhado,
  em vez de carregar uma tabela que erra no modelo seguinte.
"""

import base64
import json
import logging
import re
import uuid

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.drivers import corpo
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import (
    MODOS_AR,
    TEMPERATURA_MAXIMA,
    TEMPERATURA_MINIMA,
    VENTOS,
    Auth,
    Campo,
    Manifesto,
    TipoCampo,
)

log = logging.getLogger("iphub.drivers.nativos.lg_thinq")

TIPO = "ar_lg_thinq"

# Why: the host is a constant of the driver and never a field of the registration, section 9:
# a hub that dialled whatever a registration named would be a proxy of the internet.
# Por que: o host é constante do driver e nunca campo do cadastro, seção 9: um hub que
# discasse o que um cadastro nomeasse seria um proxy da internet.
BASE = "https://api-{regiao}.lgthinq.com/{caminho}"

# The public api key the SDK of LG carries, which every client of the Connect API sends.
# A chave pública de api que o SDK da LG carrega, que todo cliente da API Connect manda.
CHAVE_DE_API = "v6GFvkweNo7DK7yD3ylIZ9w52aKBU0eJ7wLXkSR3"
FASE = "OP"
PREFIXO_DE_CLIENTE = "tuya-ip-hub"

REGIAO_AMERICAS = "aic"
REGIAO_EUROPA = "eic"
REGIAO_ASIA = "kic"
# The countries of each region, from the country table of the SDK of LG.
# Os países de cada região, da tabela de países do SDK da LG.
PAISES = {
    REGIAO_ASIA: frozenset("AU BD CN HK ID IN JP KH KR LA LK MM MY NP NZ PH SG TH TW VN".split()),
    REGIAO_AMERICAS: frozenset(
        "AG AR AW BB BO BR BS BZ CA CL CO CR CU DM DO EC GD GT GY HN HT JM KN LC MX NI PA "
        "PE PR PY SR SV TT US UY VC VE".split()
    ),
}

CAMINHO_DISPOSITIVOS = "devices"
CAMINHO_PERFIL = "devices/{id}/profile"
CAMINHO_ESTADO = "devices/{id}/state"
CAMINHO_CONTROLE = "devices/{id}/control"

# The resources and the properties of an air conditioner, section 14.
# Os recursos e as propriedades de um ar condicionado, seção 14.
RECURSO_OPERACAO = "operation"
PROP_OPERACAO = "airConOperationMode"
RECURSO_MODO = "airConJobMode"
PROP_MODO = "currentJobMode"
RECURSO_TEMPERATURA = "temperature"
PROP_ALVO = "targetTemperature"
PROP_ALVO_C = "targetTemperatureC"
RECURSO_VENTO = "airFlow"
PROP_VENTO = "windStrength"

LIGADO = "POWER_ON"
DESLIGADO = "POWER_OFF"

CHAVE_RESPOSTA = "response"
CABECALHO_CONDICIONAL = "x-conditional-control"

# Why: section 14, the cloud refuses a change to cool or heat on a unit that is already on
# while the conditional check is on; the documentation says to send those two with it off.
# Por que: seção 14, a nuvem recusa a troca para refrigerar ou aquecer num aparelho já ligado
# com a conferência condicional ligada; a documentação manda mandar esses dois com ela desligada.
MODOS_SEM_CONDICAO = ("frio", "quente")

# Why: the words of a model are read from its profile, but a word only means something when
# this hub knows which of the vocabulary of section 6 it is; these are the spellings seen in
# the profiles and in the integration of Home Assistant, normalised the same way.
# Por que: as palavras de um modelo são lidas do profile dele, mas uma palavra só significa
# algo quando este hub sabe qual do vocabulário da seção 6 ela é; estas são as grafias vistas
# nos profiles e na integração do Home Assistant, normalizadas do mesmo jeito.
PALAVRAS_DE_MODO = {
    "auto": ("auto", "ai"),
    "frio": ("cool",),
    "quente": ("heat",),
    "vento": ("fan", "airfan"),
    "seco": ("airdry", "dry", "dehumidify"),
}
PALAVRAS_DE_VENTO = {
    "auto": ("auto", "nature", "windfree"),
    "baixo": ("low", "slow", "quiet", "lowmid"),
    "medio": ("mid", "medium", "midhigh"),
    "alto": ("high", "power", "turbo"),
}

# Why: the poll of the gestor runs every ten seconds, and the cloud of LG throttles a client
# that asks too often; one exchange per poll is what this driver spends, and the timeout is
# generous because a round trip to a cloud is not a round trip on the LAN.
# Por que: o poll do gestor roda a cada dez segundos, e a nuvem da LG limita um cliente que
# pergunta demais; uma troca por poll é o que este driver gasta, e o prazo é generoso porque
# uma ida e volta a uma nuvem não é uma ida e volta na LAN.
TEMPO_LIMITE_S = 12.0
CORPO_MAXIMO = 256 * 1024
FALHAS_ATE_OFFLINE = 2

# Why: a token is written by a person into a form, so it is checked here before it travels;
# a value with a space or a newline in it is a paste that took the line around it.
# Por que: um token é escrito por uma pessoa num formulário, então é conferido aqui antes de
# viajar; um valor com espaço ou quebra de linha é uma colagem que levou a linha em volta.
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}")
_PAIS = re.compile(r"[A-Z]{2}")
_ID_DE_DISPOSITIVO = re.compile(r"[A-Za-z0-9._~-]{4,128}")
_NAO_PALAVRA = re.compile(r"[^a-z0-9]+")

CAMPO_PAIS = "pais"
CAMPO_TOKEN = "token"
CAMPO_DISPOSITIVO = "dispositivo"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"
AUTH_PENDENTE = "auth_pendente"

PAREADO = "pareado"
FALHOU = "falhou"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_TEMPERATURA = "temperatura"
ACAO_MODO = "modo"
ACAO_VENTO = "vento"

TEXTOS = {
    "en": {
        "descricao": (
            "LG air conditioner through the ThinQ cloud. The Wi-Fi split of LG has no local "
            "API, so this is the only way to reach it, and it needs the hub to have internet."
        ),
        "campo_pais": "Country of the LG account (two letters, BR)",
        "campo_token": "Personal access token of the ThinQ account",
        "campo_dispositivo": "Device id on ThinQ",
        "auth_ajuda": (
            "Create the personal access token on the LG developer site with the account that "
            "owns the air conditioner. Pair to check the token and the device id; when the id "
            "does not match, the log of the hub lists the air conditioners of the account with "
            "the id of each, and one of them is the one to paste here."
        ),
        "cap_temperatura": (
            "The setpoint travels in degrees Celsius and the unit refuses what is outside its "
            "own range, which changes with the mode."
        ),
        "cap_modo": (
            "The words of this model are read from its profile on the cloud, so a mode the "
            "unit does not have is refused before anything is sent."
        ),
    },
    "pt": {
        "descricao": (
            "Ar condicionado LG pela nuvem ThinQ. O split Wi-Fi da LG não tem API local, "
            "então este é o único caminho até ele, e ele exige internet no hub."
        ),
        "campo_pais": "País da conta LG (duas letras, BR)",
        "campo_token": "Token pessoal de acesso da conta ThinQ",
        "campo_dispositivo": "Id do aparelho no ThinQ",
        "auth_ajuda": (
            "Crie o token pessoal de acesso no site de desenvolvedor da LG com a conta dona do "
            "ar condicionado. Pareie para conferir o token e o id do aparelho; quando o id não "
            "casa, o log do hub lista os ares condicionados da conta com o id de cada um, e um "
            "deles é o que se cola aqui."
        ),
        "cap_temperatura": (
            "O setpoint viaja em graus Celsius e a unidade recusa o que estiver fora da faixa "
            "dela, que muda com o modo."
        ),
        "cap_modo": (
            "As palavras deste modelo são lidas do profile dele na nuvem, então um modo que a "
            "unidade não tem é recusado antes de qualquer coisa ser enviada."
        ),
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the cloud.

    Um código estável a caminho da saída de uma troca com a nuvem.
    """

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class LgThinq(Driver):
    """One air conditioner of a ThinQ account, read and commanded through the cloud of LG.

    Um ar condicionado de uma conta ThinQ, lido e comandado pela nuvem da LG.
    """

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Ar condicionado LG (ThinQ)", "en": "LG air conditioner (ThinQ)"},
        categoria="ar_condicionado",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_TEMPERATURA,
            ACAO_MODO,
            ACAO_VENTO,
        ),
        modos=MODOS_AR,
        ventos=VENTOS,
        auth=Auth.CHAVE,
        nuvem=True,
        config_campos=(
            Campo(nome=CAMPO_PAIS, tipo=TipoCampo.TEXTO, obrigatorio=True, padrao="BR"),
            Campo(nome=CAMPO_TOKEN, tipo=TipoCampo.SEGREDO, obrigatorio=True),
            Campo(nome=CAMPO_DISPOSITIVO, tipo=TipoCampo.TEXTO, obrigatorio=True),
        ),
        textos=TEXTOS,
        motor="nativo",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._cliente = f"{PREFIXO_DE_CLIENTE}-{cadastro.identidade}"[:64]
        self._falhas = 0
        # Why: the profile of a unit says which words IT accepts, so it is read once and kept;
        # a hub that asked for it on every command would spend a request of the budget of the
        # account to learn something that does not change.
        # Por que: o profile de uma unidade diz quais palavras ELA aceita, então é lido uma vez
        # e guardado; um hub que o pedisse a cada comando gastaria uma requisição do orçamento
        # da conta para aprender algo que não muda.
        self._modos: dict[str, str] = {}
        self._ventos: dict[str, str] = {}
        self._perfil_lido = False

    async def parar(self) -> None:
        sessao = self._sessao
        self._sessao = None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    async def autenticar(self) -> str:
        """Section 6: checks the token and that this device is in the account, and writes what
        the account has into the log when it is not.

        Seção 6: confere o token e se este aparelho está na conta, e escreve no log o que a
        conta tem quando ele não está.
        """
        try:
            achados = _ares(await self._pedir("GET", CAMINHO_DISPOSITIVOS))
        except _Falha as falha:
            log.warning("%s: the ThinQ account refused the token: %s", self._id(), falha.codigo)
            return FALHOU
        if self._dispositivo() in {ar.id for ar in achados}:
            return PAREADO
        # Why: the id of a device is a string nobody memorises, and the only place it exists is
        # this listing; writing it to the log is what turns pairing into copy and paste.
        # Por que: o id de um aparelho é uma string que ninguém decora, e o único lugar onde ela
        # existe é esta listagem; escrevê-la no log é o que faz do pareamento um copiar e colar.
        for ar in achados:
            log.warning("air conditioner in the ThinQ account: %s (%s)", ar.id, ar.nome)
        if not achados:
            log.warning("the ThinQ account of %s has no air conditioner", self._id())
        return FALHOU

    async def atualizar(self) -> None:
        """One poll: the state of the unit, and the profile the first time it answers.

        Um poll: o estado da unidade, e o profile na primeira vez que ela responde.
        """
        try:
            if not self._perfil_lido:
                await self._ler_perfil()
            estado = await self._pedir("GET", CAMINHO_ESTADO.format(id=self._dispositivo()))
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        self._falhas = 0
        self._aplicar(estado)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            return await self._operar(LIGADO)
        if acao == ACAO_DESLIGAR:
            return await self._operar(DESLIGADO)
        # Why: section 14, a unit that is off refuses every other command, and answering the
        # code of a device that cannot do it as it stands is what a scene reads to turn it on.
        # Por que: seção 14, uma unidade desligada recusa todo outro comando, e responder o
        # código de um aparelho que não pode como está é o que uma cena lê para ligá-lo.
        if self._estado.ligado is False:
            return EQ_OFFLINE
        if acao == ACAO_TEMPERATURA:
            return await self._trocar_temperatura(valor)
        if acao == ACAO_MODO:
            return await self._trocar_modo(valor)
        if acao == ACAO_VENTO:
            return await self._trocar_vento(valor)
        return await super().executar(acao, valor)

    async def _operar(self, modo: str) -> str | None:
        await self._controlar({RECURSO_OPERACAO: {PROP_OPERACAO: modo}})
        self._defina(ligado=modo == LIGADO)
        return None

    async def _trocar_temperatura(self, valor: object) -> str | None:
        if type(valor) is not int or not TEMPERATURA_MINIMA <= valor <= TEMPERATURA_MAXIMA:
            return INVALID_VALUE
        await self._controlar({RECURSO_TEMPERATURA: {PROP_ALVO: valor}})
        self._defina(temperatura=valor)
        return None

    async def _trocar_modo(self, valor: object) -> str | None:
        palavra = self._modos.get(valor) if isinstance(valor, str) else None
        if palavra is None:
            return INVALID_VALUE
        # Why: section 14, the cloud refuses cool and heat on a unit that is already on while
        # the conditional check is on, and the documentation says to send them with it off.
        # Por que: seção 14, a nuvem recusa refrigerar e aquecer num aparelho já ligado com a
        # conferência condicional ligada, e a documentação manda mandá-los com ela desligada.
        condicional = valor not in MODOS_SEM_CONDICAO
        await self._controlar({RECURSO_MODO: {PROP_MODO: palavra}}, condicional=condicional)
        self._defina(modo=valor)
        return None

    async def _trocar_vento(self, valor: object) -> str | None:
        palavra = self._ventos.get(valor) if isinstance(valor, str) else None
        if palavra is None:
            return INVALID_VALUE
        await self._controlar({RECURSO_VENTO: {PROP_VENTO: palavra}})
        self._defina(vento=valor)
        return None

    async def _ler_perfil(self) -> None:
        """What THIS unit accepts, read once: the words of its modes and of its fan speeds.

        O que ESTA unidade aceita, lido uma vez: as palavras dos modos e dos ventos dela.
        """
        perfil = await self._pedir("GET", CAMINHO_PERFIL.format(id=self._dispositivo()))
        self._modos = _vocabulario(perfil, RECURSO_MODO, PROP_MODO, PALAVRAS_DE_MODO)
        self._ventos = _vocabulario(perfil, RECURSO_VENTO, PROP_VENTO, PALAVRAS_DE_VENTO)
        self._perfil_lido = True
        log.debug(
            "%s: the unit accepts modes %s and fan speeds %s",
            self._id(),
            sorted(self._modos),
            sorted(self._ventos),
        )

    def _aplicar(self, estado: dict) -> None:
        operacao = _texto(_de(estado, RECURSO_OPERACAO, PROP_OPERACAO))
        temperatura = _de(estado, RECURSO_TEMPERATURA, PROP_ALVO)
        if temperatura is None:
            temperatura = _de(estado, RECURSO_TEMPERATURA, PROP_ALVO_C)
        self._defina(
            online=True,
            ligado=None if not operacao else operacao.upper() == LIGADO,
            temperatura=_inteiro(temperatura),
            modo=_palavra_de(_de(estado, RECURSO_MODO, PROP_MODO), PALAVRAS_DE_MODO),
            vento=_palavra_de(_de(estado, RECURSO_VENTO, PROP_VENTO), PALAVRAS_DE_VENTO),
            detalhe="",
        )

    def _falhar(self, codigo: str) -> None:
        """Section 14 of the LinkPlay driver, and the same here: one lost poll keeps the last
        state, two in a row is offline.

        Seção 14 do driver LinkPlay, e o mesmo aqui: um poll perdido guarda o último estado,
        dois seguidos é offline.
        """
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self._id(), self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._perfil_lido = False
        self._defina(online=False, detalhe=codigo)

    def _id(self) -> str:
        return self.cadastro.identidade

    def _dispositivo(self) -> str:
        return self.cadastro.campos.get(CAMPO_DISPOSITIVO, "").strip()

    def _pais(self) -> str:
        return self.cadastro.campos.get(CAMPO_PAIS, "").strip().upper()

    def _token(self) -> str:
        return self.cadastro.segredos.get(CAMPO_TOKEN, "").strip()

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    async def _controlar(self, ordem: dict, *, condicional: bool = True) -> dict:
        cabecalhos = {CABECALHO_CONDICIONAL: "true" if condicional else "false"}
        return await self._pedir(
            "POST",
            CAMINHO_CONTROLE.format(id=self._dispositivo()),
            ordem=ordem,
            cabecalhos=cabecalhos,
        )

    async def _pedir(
        self,
        metodo: str,
        caminho: str,
        *,
        ordem: dict | None = None,
        cabecalhos: dict[str, str] | None = None,
    ) -> dict:
        """One exchange with the cloud, answered as the object inside "response".

        Uma troca com a nuvem, respondida como o objeto de dentro do "response".
        """
        pais = self._pais()
        token = self._token()
        dispositivo = self._dispositivo()
        if not _PAIS.fullmatch(pais) or not _TOKEN.fullmatch(token):
            # Why: a registration that was saved with a bad field would otherwise spend a
            # request of the account to be told what this line already knows.
            # Por que: um cadastro salvo com campo ruim gastaria uma requisição da conta para
            # ouvir o que esta linha já sabe.
            raise _Falha(AUTH_PENDENTE)
        if caminho != CAMINHO_DISPOSITIVOS and not _ID_DE_DISPOSITIVO.fullmatch(dispositivo):
            raise _Falha(AUTH_PENDENTE)
        url = BASE.format(regiao=_regiao(pais), caminho=caminho)
        sessao = await self._abrir()
        try:
            async with sessao.request(
                metodo,
                url,
                json=ordem,
                headers=self._cabecalhos(pais, token, cabecalhos),
                # Why: section 9, a redirect would send the token of the customer to whatever
                # host answered, which is the one thing that must never travel sideways.
                # Por que: seção 9, um redirecionamento mandaria o token do cliente para o host
                # que respondesse, que é a única coisa que nunca pode viajar de lado.
                allow_redirects=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            raise _Falha(EQ_OFFLINE) from erro
        return _corpo_de(estado, bruto, caminho)

    def _cabecalhos(self, pais: str, token: str, extras: dict[str, str] | None) -> dict[str, str]:
        cabecalhos = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-api-key": CHAVE_DE_API,
            "x-country": pais,
            "x-client-id": self._cliente,
            "x-message-id": _id_de_mensagem(),
            "x-service-phase": FASE,
        }
        cabecalhos.update(extras or {})
        return cabecalhos


class _Ar:
    """One air conditioner of the account, as the listing names it.

    Um ar condicionado da conta, como a listagem o nomeia.
    """

    __slots__ = ("id", "nome")

    def __init__(self, identificador: str, nome: str) -> None:
        self.id = identificador
        self.nome = nome


def _corpo_de(estado: int, bruto: bytes, caminho: str) -> dict:
    """The object inside "response", or the stable code the status deserves.

    O objeto de dentro do "response", ou o código estável que o status merece.
    """
    if estado in (401, 403):
        # Why: a token that expired or was revoked is not a device that failed, and the panel
        # says so with the one code that means "pair it again".
        # Por que: um token vencido ou revogado não é aparelho que falhou, e o painel diz isso
        # com o único código que significa "pareie de novo".
        raise _Falha(AUTH_PENDENTE)
    if estado == 400:
        raise _Falha(INVALID_VALUE)
    if estado >= 400:
        log.warning("the ThinQ cloud answered HTTP %d to %s", estado, caminho)
        raise _Falha(ERRO_APARELHO)
    try:
        documento = json.loads(bruto.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError) as erro:
        raise _Falha(ERRO_APARELHO) from erro
    if not isinstance(documento, dict):
        raise _Falha(ERRO_APARELHO)
    resposta = documento.get(CHAVE_RESPOSTA, documento)
    if isinstance(resposta, list):
        return {"lista": resposta}
    if not isinstance(resposta, dict):
        raise _Falha(ERRO_APARELHO)
    return resposta


def _ares(listagem: dict) -> tuple[_Ar, ...]:
    """The air conditioners of the account, from the listing of devices.

    Os ares condicionados da conta, da listagem de aparelhos.
    """
    achados = []
    for bruto in listagem.get("lista", []):
        if not isinstance(bruto, dict):
            continue
        informacao = bruto.get("deviceInfo")
        informacao = informacao if isinstance(informacao, dict) else {}
        tipo = _texto(informacao.get("deviceType"))
        identificador = _texto(bruto.get("deviceId"))
        if not identificador or "AIR_CONDITIONER" not in tipo.upper():
            continue
        achados.append(_Ar(identificador, _texto(informacao.get("alias")) or identificador))
    return tuple(achados)


def _vocabulario(
    perfil: dict, recurso: str, propriedade: str, palavras: dict[str, tuple[str, ...]]
) -> dict[str, str]:
    """The words of section 6 this unit accepts, mapped to the words it uses for them.

    Why: a profile answers the spellings of that model, and two models of the same brand do
    not agree; matching without case and without the punctuation is what makes AIR_DRY,
    air_dry and airDry the same word of section 6.

    As palavras da seção 6 que esta unidade aceita, mapeadas para as palavras que ela usa.

    Por que: um profile responde as grafias daquele modelo, e dois modelos da mesma marca não
    concordam; casar sem caixa e sem a pontuação é o que faz AIR_DRY, air_dry e airDry serem a
    mesma palavra da seção 6.
    """
    aceitas = _valores_do_perfil(perfil, recurso, propriedade)
    saida: dict[str, str] = {}
    for nossa, grafias in palavras.items():
        for aceita in aceitas:
            if _normal(aceita) in grafias:
                saida[nossa] = aceita
                break
    return saida


def _valores_do_perfil(perfil: dict, recurso: str, propriedade: str) -> tuple[str, ...]:
    """The values a profile says are writable for one property, whatever shape it wrapped
    them in.

    Os valores que um profile diz serem graváveis numa propriedade, na forma em que ele os
    embrulhou.
    """
    bruto = _de(perfil, recurso, propriedade)
    if isinstance(bruto, list):
        return tuple(_texto(valor) for valor in bruto if _texto(valor))
    if not isinstance(bruto, dict):
        return ()
    # Why: the profile of LG carries the writable values under "w" of "value", and older
    # answers put the list straight under the property; both are read instead of one.
    # Por que: o profile da LG carrega os valores graváveis sob "w" de "value", e respostas
    # antigas põem a lista direto sob a propriedade; os dois são lidos em vez de um.
    valores = bruto.get("value", bruto)
    if isinstance(valores, dict):
        valores = valores.get("w", valores.get("write", []))
    if not isinstance(valores, list):
        return ()
    return tuple(_texto(valor) for valor in valores if _texto(valor))


def _de(documento: dict, recurso: str, propriedade: str) -> object:
    """One property of one resource, or None when the answer does not carry it.

    Uma propriedade de um recurso, ou None quando a resposta não a carrega.
    """
    bloco = documento.get(recurso)
    if isinstance(bloco, list):
        # Why: a unit with more than one indoor section answers a list of blocks, and the
        # first is the one this registration commands.
        # Por que: uma unidade com mais de uma seção interna responde uma lista de blocos, e o
        # primeiro é o que este cadastro comanda.
        bloco = bloco[0] if bloco and isinstance(bloco[0], dict) else None
    if not isinstance(bloco, dict):
        return None
    return bloco.get(propriedade)


def _palavra_de(bruto: object, palavras: dict[str, tuple[str, ...]]) -> str | None:
    """The word of section 6 for what the unit answered, or None for one nobody named.

    A palavra da seção 6 para o que a unidade respondeu, ou None para uma que ninguém nomeou.
    """
    lido = _normal(_texto(bruto))
    if not lido:
        return None
    for nossa, grafias in palavras.items():
        if lido in grafias:
            return nossa
    return None


def _regiao(pais: str) -> str:
    for regiao, paises in PAISES.items():
        if pais in paises:
            return regiao
    # Why: the table of LG lists a hundred countries on the European region, so anything the
    # two shorter lists do not claim belongs there; a country nobody supports is refused by
    # the cloud with its own code and not guessed at here.
    # Por que: a tabela da LG lista uma centena de países na região europeia, então o que as
    # duas listas curtas não reivindicam é de lá; um país que ninguém suporta é recusado pela
    # nuvem com o código dela e não adivinhado aqui.
    return REGIAO_EUROPA


def _id_de_mensagem() -> str:
    """A message id of its own per request, which the API asks for in base64.

    Um id de mensagem próprio por requisição, que a API pede em base64.
    """
    return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("ascii").rstrip("=")


def _normal(bruto: str) -> str:
    return _NAO_PALAVRA.sub("", bruto.strip().lower())


def _texto(bruto: object) -> str:
    return bruto.strip() if isinstance(bruto, str) else ""


def _inteiro(bruto: object) -> int | None:
    if isinstance(bruto, bool):
        return None
    if isinstance(bruto, int):
        return bruto
    if isinstance(bruto, float):
        return round(bruto)
    return None
