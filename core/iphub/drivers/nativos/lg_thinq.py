# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""LG air conditioner over the ThinQ Connect API, the first cloud driver.

The Wi-Fi split of LG has NO local API: the manufacturer publishes only the cloud one, which
is the same door the lg_thinq integration of Home Assistant goes through. So this driver is
the exception opens, with the four conditions asks for: the host is a
constant of this file and never comes from the registration, the scheme is always https, a
redirect is refused, and the personal token is a secret of the registration that never goes
back to the panel and never lands in the log.

What the API does, so nobody has to read it again:

- the base is https://api-{aic|eic|kic}.lgthinq.com/ and the region comes from the country:
BR and the Americas on aic, Europe, Africa and the Middle East on eic, Asia on kic;
- every request carries the token, the public api key of the SDK of LG, the country, a client
id and a message id of its own; the answer is wrapped in {"response": {...}};
- devices lists the account, devices/{id}/profile says what THAT unit accepts,
devices/{id}/state reads it and devices/{id}/control writes it;
- a command is a pair of resource and property: {"operation": {"airConOperationMode":
"POWER_ON"}}, {"airConJobMode": {"currentJobMode":...}}, {"temperature":
{"targetTemperature": N}}, {"airFlow": {"windStrength":...}};
- x-conditional-control asks the cloud to check the state before acting, and the
documentation says to turn it OFF when the mode is changed to cool or heat on a unit that
is already on, or the command comes back refused;
- a unit that is POWER_OFF accepts no command at all, so a scene turns it on first;
- the words of the modes and of the fan speeds change with the model, so this driver reads
them from the profile of that unit and matches them against the vocabulary
ignoring case and underscores, instead of carrying a table that is wrong on the next model.
"""

import base64
import json
import logging
import re
import uuid

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.drivers import corpo, fio
from iphub.drivers.base import Aparelho, Cadastro, Driver
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

# The host is a constant of the driver and never a field of the registration,
# a hub that dialled whatever a registration named would be a proxy of the internet.
BASE = "https://api-{regiao}.lgthinq.com/{caminho}"

# The public api key the SDK of LG carries, which every client of the Connect API sends.
CHAVE_DE_API = "v6GFvkweNo7DK7yD3ylIZ9w52aKBU0eJ7wLXkSR3"
FASE = "OP"
PREFIXO_DE_CLIENTE = "tuya-ip-hub"

REGIAO_AMERICAS = "aic"
REGIAO_EUROPA = "eic"
REGIAO_ASIA = "kic"
# The countries of each region, from the country table of the SDK of LG.
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

# The resources and the properties of an air conditioner.
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
CHAVE_PROPRIEDADES = "property"
CABECALHO_CONDICIONAL = "x-conditional-control"

# The cloud refuses a change to cool or heat on a unit that is already on
# while the conditional check is on; the documentation says to send those two with it off.
MODOS_SEM_CONDICAO = ("frio", "quente")

# The words of a model are read from its profile, but a word only means something when
# this hub knows which of the vocabulary it is; these are the spellings seen in
# the profiles and in the integration of Home Assistant, normalised the same way.
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

# The poll of the gestor runs every ten seconds, and the cloud of LG throttles a client
# that asks too often; one exchange per poll is what this driver spends, and the timeout is
# generous because a round trip to a cloud is not a round trip on the LAN.
TEMPO_LIMITE_S = 12.0
CORPO_MAXIMO = 256 * 1024
FALHAS_ATE_OFFLINE = 2

# A token is written by a person into a form, so it is checked here before it travels;
# a value with a space or a newline in it is a paste that took the line around it.
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}")
_PAIS = re.compile(r"[A-Z]{2}")
_ID_DE_DISPOSITIVO = re.compile(r"[A-Za-z0-9._~-]{4,128}")
_NAO_PALAVRA = re.compile(r"[^a-z0-9]+")
# Measured on a real unit on 7/set/2026, the cloud refuses a setpoint sent in auto mode
# with HTTP 400 and the error 2305 "Command not supported in AUTO"; the mode it names is what
# the driver learns from that answer.
_RECUSA_DE_MODO = re.compile(r"not supported in (?P<modo>[A-Za-z_]+)", re.IGNORECASE)
CHAVE_ERRO = "error"
CHAVE_MENSAGEM = "message"

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
        "preparo": (
            "100% cloud: the hub talks to LG over the internet and never to the unit on your "
            "network, which is why there is no address field. The air conditioner has to be "
            "registered in the ThinQ app of the phone, on the same account as the token, and the "
            "hub needs internet."
        ),
        "campo_pais": "Country of the LG account (two letters, BR)",
        "campo_token": "Personal access token of the ThinQ account",
        "campo_dispositivo": (
            "Id of the device on ThinQ. Leave it empty: pairing takes it from the account when "
            "there is one air conditioner in it."
        ),
        "auth_ajuda": (
            "1. Open https://connect-pat.lgthinq.com/tokens and sign in with the LG account "
            "that owns the air conditioner, which is the account of the ThinQ app on the "
            "phone. The site sends you to the page of your region on its own.\n"
            "2. Create a personal access token, tick the permissions of the air conditioner, "
            "and copy the token: the site shows it ONCE.\n"
            "3. Paste it here, put the two letters of the country of the account, and save.\n"
            "4. Press Find the devices of the account and pick yours from the list."
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
        "preparo": (
            "100% na nuvem: o hub fala com a LG pela internet e nunca com o aparelho na sua rede, "
            "e por isso não há campo de endereço. O ar condicionado precisa estar cadastrado no "
            "aplicativo ThinQ do celular, na mesma conta do token, e o hub precisa de internet."
        ),
        "campo_pais": "País da conta LG (duas letras, BR)",
        "campo_token": "Token pessoal de acesso da conta ThinQ",
        "campo_dispositivo": (
            "Id do aparelho no ThinQ. Deixe vazio: o parear o pega da conta quando há um ar "
            "condicionado nela."
        ),
        "auth_ajuda": (
            "1. Abra https://connect-pat.lgthinq.com/tokens e entre com a conta LG dona do ar "
            "condicionado, que é a conta do aplicativo ThinQ do celular. O site leva você para "
            "a página da sua região sozinho.\n"
            "2. Crie um token pessoal de acesso, marque as permissões do ar condicionado, e "
            "copie o token: o site o mostra UMA vez.\n"
            "3. Cole aqui, ponha as duas letras do país da conta, e salve.\n"
            "4. Aperte Buscar aparelhos da conta e escolha o seu na lista."
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
    """A stable code on its way out of an exchange with the cloud."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class _RecusaDeModo(_Falha):
    """The cloud refused the order because of the mode the unit is in, and named the mode."""

    def __init__(self, modo: str) -> None:
        super().__init__(ERRO_APARELHO)
        self.modo = modo


class LgThinq(Driver):
    """One air conditioner of a ThinQ account, read and commanded through the cloud of LG."""

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
        lista_da_conta=CAMPO_DISPOSITIVO,
        config_campos=(
            Campo(nome=CAMPO_PAIS, tipo=TipoCampo.TEXTO, obrigatorio=True, padrao="BR"),
            Campo(nome=CAMPO_TOKEN, tipo=TipoCampo.SEGREDO, obrigatorio=True),
            # The id of a device is a string that exists only inside the account, so
            # demanding it before the first call is demanding what nobody has yet. Token and
            # country are enough: parear lists the account and, when there is one air
            # conditioner in it, takes its id and writes it into the registration.
            Campo(nome=CAMPO_DISPOSITIVO, tipo=TipoCampo.TEXTO, obrigatorio=False),
        ),
        textos=TEXTOS,
        marca="LG",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._cliente = f"{PREFIXO_DE_CLIENTE}-{cadastro.identidade}"[:64]
        self._falhas = 0
        # The profile of a unit says which words IT accepts, so it is read once and kept;
        # a hub that asked for it on every command would spend a request of the budget of the
        # account to learn something that does not change.
        self._modos: dict[str, str] = {}
        self._ventos: dict[str, str] = {}
        self._perfil_lido = False
        # A mode in which the cloud refused a setpoint keeps refusing it, so the mode is
        # remembered and the state offers no setpoint while the unit is in it; the last
        # setpoint read is kept so that leaving that mode shows it again without a poll.
        self._sem_setpoint: set[str] = set()
        self._setpoint_lido: int | None = None
        self._adotado = ""

    async def parar(self) -> None:
        sessao = self._sessao
        self._sessao = None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    async def autenticar(self) -> str:
        """Checks the token and that this device is in the account, and writes what
        the account has into the log when it is not.
        """
        try:
            achados = _ares(await self._pedir("GET", CAMINHO_DISPOSITIVOS))
        except _Falha as falha:
            log.warning("%s: the ThinQ account refused the token: %s", self._id(), falha.codigo)
            return FALHOU
        if self._dispositivo() in {ar.id for ar in achados}:
            return PAREADO
        # An account with ONE air conditioner has no ambiguity to resolve, and making the
        # integrator copy an id out of a log to paste it back is a step that exists only
        # because nobody wrote this branch. With two or more the choice is his, and the log is
        # where the ids are.
        if not self._dispositivo() and len(achados) == 1:
            self._adotado = achados[0].id
            self._perfil_lido = False
            log.info("%s: the account has one air conditioner, and it is now this one", self._id())
            return PAREADO
        # The id of a device is a string nobody memorises, and the only place it exists is
        # this listing; writing it to the log is what turns pairing into copy and paste.
        for ar in achados:
            log.warning("air conditioner in the ThinQ account: %s (%s)", ar.id, ar.nome)
        if not achados:
            log.warning("the ThinQ account of %s has no air conditioner", self._id())
        return FALHOU

    async def aparelhos_da_conta(self) -> tuple[Aparelho, ...]:
        """The air conditioners this token reaches, for the operator to pick one."""
        achados = _ares(await self._pedir("GET", CAMINHO_DISPOSITIVOS))
        return tuple(Aparelho(id=ar.id, nome=ar.nome) for ar in achados)

    def credenciais_do_aparelho(self) -> dict[str, str]:
        """The id the pairing found in the account, for the caller to persist."""
        return {CAMPO_DISPOSITIVO: self._adotado} if self._adotado else {}

    async def atualizar(self) -> None:
        """One poll: the state of the unit, and the profile the first time it answers."""
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
        # A unit that is off refuses every other command, and answering the
        # code of a device that cannot do it as it stands is what a scene reads to turn it on.
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
        try:
            await self._controlar({RECURSO_TEMPERATURA: {PROP_ALVO: valor}})
        except _RecusaDeModo as recusa:
            self._sem_setpoint_em(recusa.modo)
            raise
        self._setpoint_lido = valor
        self._defina(temperatura=valor)
        return None

    async def _trocar_modo(self, valor: object) -> str | None:
        palavra = self._modos.get(valor) if isinstance(valor, str) else None
        if palavra is None:
            return INVALID_VALUE
        # The cloud refuses cool and heat on a unit that is already on while
        # the conditional check is on, and the documentation says to send them with it off.
        condicional = valor not in MODOS_SEM_CONDICAO
        await self._controlar({RECURSO_MODO: {PROP_MODO: palavra}}, condicional=condicional)
        self._defina(modo=valor, temperatura=self._setpoint_em(valor))
        return None

    async def _trocar_vento(self, valor: object) -> str | None:
        palavra = self._ventos.get(valor) if isinstance(valor, str) else None
        if palavra is None:
            return INVALID_VALUE
        await self._controlar({RECURSO_VENTO: {PROP_VENTO: palavra}})
        self._defina(vento=valor)
        return None

    def _setpoint_em(self, modo: str | None) -> int | None:
        """The setpoint the state offers in a mode: none in a mode the unit refused it in."""
        return None if modo in self._sem_setpoint else self._setpoint_lido

    def _sem_setpoint_em(self, palavra: str) -> None:
        """Learns that the unit refuses a setpoint in this mode, and stops offering one there.

        The cloud names the mode in its own word (AUTO); the word for it is
        the one the profile mapped, and when the profile mapped none, the mode the state shows
        is the one that refused. The lesson lives in memory: a hub that restarts spends one
        refused request to learn it again, which is cheaper than a lesson that outlives a
        firmware that changed its mind.
        """
        casados = (
            m for m, da_nuvem in self._modos.items() if _normal(da_nuvem) == _normal(palavra)
        )
        nosso = next(casados, self._estado.modo)
        if nosso is None:
            return
        if nosso not in self._sem_setpoint:
            self._sem_setpoint.add(nosso)
            log.info(
                "%s: the unit refuses a setpoint in mode %s, so the state offers none there",
                self._id(),
                nosso,
            )
        if self._estado.modo == nosso:
            self._defina(temperatura=None)

    async def _ler_perfil(self) -> None:
        """What THIS unit accepts, read once: the words of its modes and of its fan speeds."""
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
        modo = _palavra_de(_de(estado, RECURSO_MODO, PROP_MODO), PALAVRAS_DE_MODO)
        self._setpoint_lido = _inteiro(temperatura)
        self._defina(
            online=True,
            ligado=None if not operacao else operacao.upper() == LIGADO,
            temperatura=self._setpoint_em(modo),
            modo=modo,
            vento=_palavra_de(_de(estado, RECURSO_VENTO, PROP_VENTO), PALAVRAS_DE_VENTO),
            detalhe="",
        )

    def _falhar(self, codigo: str) -> None:
        """Of the LinkPlay driver, and the same here: one lost poll keeps the last
        state, two in a row is offline.
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
        """The id in force: the one the pairing adopted, or the one the registration carries.

        The adopted one comes first because the registration only learns it after the
        caller writes it, and the poll that follows the pairing must not wait for that trip.
        """
        return self._adotado or self.cadastro.campos.get(CAMPO_DISPOSITIVO, "").strip()

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
        """One exchange with the cloud, answered as the object inside "response"."""
        pais = self._pais()
        token = self._token()
        dispositivo = self._dispositivo()
        if not _PAIS.fullmatch(pais) or not _TOKEN.fullmatch(token):
            # A registration that was saved with a bad field would otherwise spend a
            # request of the account to be told what this line already knows.
            raise _Falha(AUTH_PENDENTE)
        if caminho != CAMINHO_DISPOSITIVOS and not _ID_DE_DISPOSITIVO.fullmatch(dispositivo):
            raise _Falha(AUTH_PENDENTE)
        url = BASE.format(regiao=_regiao(pais), caminho=caminho)
        # The transcript names the path and the order and never the headers, because
        # the token of the account rides in a header and keeps it out of the log.
        rotina = metodo == "GET" and caminho.endswith("/state")
        transcricao = self._transcricao()
        transcricao.enviado(
            f"{metodo} {caminho}" + (f" {fio.redigir(ordem)}" if ordem else ""), rotina=rotina
        )
        sessao = await self._abrir()
        try:
            async with sessao.request(
                metodo,
                url,
                json=ordem,
                headers=self._cabecalhos(pais, token, cabecalhos),
                # A redirect would send the token of the customer to whatever
                # host answered, which is the one thing that must never travel sideways.
                allow_redirects=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            transcricao.falhou(f"{metodo} {caminho}", erro)
            raise _Falha(EQ_OFFLINE) from erro
        transcricao.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        return _corpo_de(estado, bruto, caminho)

    def _transcricao(self) -> fio.Fio:
        transcricao = getattr(self, "_fio", None)
        if transcricao is None:
            transcricao = fio.Fio(log, self._id())
            self._fio = transcricao
        return transcricao

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
    """One air conditioner of the account, as the listing names it."""

    __slots__ = ("id", "nome")

    def __init__(self, identificador: str, nome: str) -> None:
        self.id = identificador
        self.nome = nome


def _modo_recusado(bruto: bytes) -> str | None:
    """The mode a 400 names when the cloud says "Command not supported in AUTO", else None.

    The value is fine and the mode is what refuses it, and a code that said "invalid
    value" sent the customer to try 23 five times in a row.
    """
    try:
        documento = json.loads(bruto.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return None
    erro = documento.get(CHAVE_ERRO) if isinstance(documento, dict) else None
    if not isinstance(erro, dict):
        return None
    achado = _RECUSA_DE_MODO.search(_texto(erro.get(CHAVE_MENSAGEM)))
    return achado.group("modo").upper() if achado else None


def _corpo_de(estado: int, bruto: bytes, caminho: str) -> dict:
    """The object inside "response", or the stable code the status deserves."""
    if estado in (401, 403):
        # A token that expired or was revoked is not a device that failed, and the panel
        # says so with the one code that means "pair it again".
        raise _Falha(AUTH_PENDENTE)
    if estado == 400:
        recusado = _modo_recusado(bruto)
        if recusado is not None:
            raise _RecusaDeModo(recusado)
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
    """The air conditioners of the account, from the listing of devices."""
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
    """The words this unit accepts, mapped to the words it uses for them.

    A profile answers the spellings of that model, and two models of the same brand do
    not agree; matching without case and without the punctuation is what makes AIR_DRY,
    air_dry and airDry the same word.
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
    """
    bruto = _de(perfil, recurso, propriedade)
    if isinstance(bruto, list):
        return tuple(_texto(valor) for valor in bruto if _texto(valor))
    if not isinstance(bruto, dict):
        return ()
    # The profile of LG carries the writable values under "w" of "value", and older
    # answers put the list straight under the property; both are read instead of one.
    valores = bruto.get("value", bruto)
    if isinstance(valores, dict):
        valores = valores.get("w", valores.get("write", []))
    if not isinstance(valores, list):
        return ()
    return tuple(_texto(valor) for valor in valores if _texto(valor))


def _de(documento: dict, recurso: str, propriedade: str) -> object:
    """One property of one resource, or None when the answer does not carry it."""
    # Measured on a real unit on 7/set/2026, the PROFILE wraps every resource under one
    # "property" key while the STATE puts them at the top level; a reader that only looked at
    # the top level found no mode and no fan speed in the profile, and every modo and vento
    # the customer pressed came back invalid_value while ligar and temperatura worked. Both
    # shapes are read here, because the two documents name the same resources.
    propriedades = documento.get(CHAVE_PROPRIEDADES)
    if isinstance(propriedades, dict) and recurso not in documento:
        documento = propriedades
    bloco = documento.get(recurso)
    if isinstance(bloco, list):
        # A unit with more than one indoor section answers a list of blocks, and the
        # first is the one this registration commands.
        bloco = bloco[0] if bloco and isinstance(bloco[0], dict) else None
    if not isinstance(bloco, dict):
        return None
    return bloco.get(propriedade)


def _palavra_de(bruto: object, palavras: dict[str, tuple[str, ...]]) -> str | None:
    """The word for what the unit answered, or None for one nobody named."""
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
    # The table of LG lists a hundred countries on the European region, so anything the
    # two shorter lists do not claim belongs there; a country nobody supports is refused by
    # the cloud with its own code and not guessed at here.
    return REGIAO_EUROPA


def _id_de_mensagem() -> str:
    """A message id of its own per request, which the API asks for in base64."""
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
