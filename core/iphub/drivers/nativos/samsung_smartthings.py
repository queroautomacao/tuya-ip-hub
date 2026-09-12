# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Samsung Wi-Fi air conditioner over the SmartThings API, the second cloud driver.

The Wi-Fi split of Samsung has no local API of its own: what the unit speaks on the network
is the SmartThings protocol of the account, and the door the manufacturer publishes is the
cloud one, which is the same door the smartthings integration of Home Assistant goes through.
So this driver takes the exception with the same four conditions the first one did: the host
is a constant of this file and never comes from the registration, the scheme is always https,
a redirect is refused, and the personal token is a secret of the registration that never goes
back to the panel and never lands in the log.

What the API does, so nobody has to read it again:

- one host for the whole world, https://api.smartthings.com/v1, and the token rides in the
  Authorization header of every request;
- devices lists the account and accepts a capability as a filter, devices/{id}/status reads
  one unit and devices/{id}/commands writes it;
- the status of a unit answers EVERYTHING in one request, and that includes the lists of what
  THAT model accepts (supportedAcModes and supportedAcFanModes), so unlike the cloud of LG
  there is no separate profile to read and the vocabulary of the unit is refreshed by the
  poll it was already paying for;
- a command is a list of {component, capability, command, arguments}; this driver always
  commands the component "main", which is the unit itself on every model seen;
- the answer to a command is 200 with a result per command, and a result that is not accepted
  is the unit refusing, not the transport failing;
- the setpoint carries the UNIT the account is set to, and an account in Fahrenheit answers
  73 where the customer asked for 23, so both directions are converted here and the panel
  never learns that there was another scale.

WHAT THE INTEGRATOR HAS TO KNOW, and the reason this is written in the manifest too: a
personal access token created before 30 December 2024 does not expire, and one created after
that date lasts 24 HOURS. Samsung made that change to push clients to the account linking
flow, which needs a registered application and a public address to come back to, and this hub
has neither. So the token of an old account works forever, a token created today has to be
made again tomorrow, and this driver says auth_pendente when it dies instead of pretending
the air conditioner went away.
"""

import json
import logging
import re

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

log = logging.getLogger("iphub.drivers.nativos.samsung_smartthings")

TIPO = "ar_samsung_smartthings"

# The host is a constant of the driver and never a field of the registration: a hub that
# dialled whatever a registration named would be a proxy of the internet.
BASE = "https://api.smartthings.com/v1/{caminho}"

CAMINHO_DISPOSITIVOS = "devices"
CAMINHO_ESTADO = "devices/{id}/status"
CAMINHO_COMANDOS = "devices/{id}/commands"

# The unit itself, on every model seen; a Samsung air conditioner also publishes components
# for accessories, and none of them is what this registration commands.
COMPONENTE = "main"

CAP_ENERGIA = "switch"
CAP_MODO = "airConditionerMode"
CAP_VENTO = "airConditionerFanMode"
CAP_SETPOINT = "thermostatCoolingSetpoint"

ATR_ENERGIA = "switch"
ATR_MODO = "airConditionerMode"
ATR_MODOS = "supportedAcModes"
ATR_VENTO = "fanMode"
ATR_VENTOS = "supportedAcFanModes"
ATR_SETPOINT = "coolingSetpoint"

COMANDO_LIGAR = "on"
COMANDO_DESLIGAR = "off"
COMANDO_MODO = "setAirConditionerMode"
COMANDO_VENTO = "setFanMode"
COMANDO_SETPOINT = "setCoolingSetpoint"

CHAVE_ITENS = "items"
CHAVE_COMPONENTES = "components"
CHAVE_VALOR = "value"
CHAVE_UNIDADE = "unit"
CHAVE_RESULTADOS = "results"
CHAVE_SITUACAO = "status"
ACEITO = ("ACCEPTED", "COMPLETED", "SUCCESS")
FAHRENHEIT = "F"

# The words of a model are read from its own status, but a word only means something when
# this hub knows which one of the vocabulary it is; these are the spellings the API answers,
# normalised the same way. What is absent is absent on purpose: coolClean and dryClean are
# modes of their own and neither is the cool or the dry of the vocabulary.
PALAVRAS_DE_MODO = {
    "auto": ("auto", "aicomfort", "ai"),
    "frio": ("cool",),
    "quente": ("heat",),
    "vento": ("wind", "fanonly", "fan"),
    "seco": ("dry", "dehumidify"),
}
PALAVRAS_DE_VENTO = {
    "auto": ("auto",),
    "baixo": ("low",),
    "medio": ("medium", "mid"),
    "alto": ("high", "turbo"),
}

# The poll of the gestor runs every ten seconds and this driver spends ONE exchange on it,
# because the status of a unit already carries the lists of what it accepts. The timeout is
# generous because a round trip to a cloud is not a round trip on the LAN.
TEMPO_LIMITE_S = 12.0
CORPO_MAXIMO = 256 * 1024
FALHAS_ATE_OFFLINE = 2

# A token is written by a person into a form, so it is checked here before it travels; a
# value with a space or a newline in it is a paste that took the line around it.
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}")
_ID_DE_DISPOSITIVO = re.compile(r"[A-Za-z0-9._~-]{4,128}")
_NAO_PALAVRA = re.compile(r"[^a-z0-9]+")

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
            "Samsung air conditioner through the SmartThings cloud. The Wi-Fi split of Samsung "
            "has no local API, so this is the only way to reach it, and it needs the hub to "
            "have internet."
        ),
        "preparo": (
            "100% cloud: the hub talks to SmartThings over the internet and never to the unit "
            "on your network, which is why there is no address field. The air conditioner has "
            "to be registered in the SmartThings app of the phone, on the same account as the "
            "token. ATTENTION to the token: one created before 30 December 2024 does not "
            "expire, and one created after that date lasts 24 HOURS, which is a decision of "
            "Samsung and not of this hub. When it dies the panel says the registration needs "
            "pairing again."
        ),
        "campo_token": "Personal access token of the SmartThings account",
        "campo_dispositivo": (
            "Id of the device on SmartThings. Leave it empty: pairing takes it from the "
            "account when there is one air conditioner in it."
        ),
        "auth_ajuda": (
            "1. Open https://account.smartthings.com/tokens and sign in with the Samsung "
            "account that owns the air conditioner, which is the account of the SmartThings "
            "app on the phone.\n"
            "2. Create a token with the permissions to LIST and to CONTROL devices, and copy "
            "it: the site shows it ONCE.\n"
            "3. Paste it here and save.\n"
            "4. Press Find the devices of the account and pick yours from the list."
        ),
        "cap_temperatura": (
            "The setpoint travels in degrees Celsius; an account set to Fahrenheit is "
            "converted in both directions and the panel never sees the other scale."
        ),
        "cap_modo": (
            "The modes of this model are read from its own status, so a mode the unit does "
            "not have is refused before anything is sent."
        ),
    },
    "pt": {
        "descricao": (
            "Ar condicionado Samsung pela nuvem SmartThings. O split Wi-Fi da Samsung não tem "
            "API local, então este é o único caminho até ele, e ele exige internet no hub."
        ),
        "preparo": (
            "100% na nuvem: o hub fala com a SmartThings pela internet e nunca com o aparelho "
            "na sua rede, e por isso não há campo de endereço. O ar condicionado precisa estar "
            "cadastrado no aplicativo SmartThings do celular, na mesma conta do token. ATENÇÃO "
            "ao token: um criado antes de 30 de dezembro de 2024 não expira, e um criado "
            "depois dessa data dura 24 HORAS, o que é decisão da Samsung e não deste hub. "
            "Quando ele morre, o painel diz que o cadastro precisa parear de novo."
        ),
        "campo_token": "Token pessoal de acesso da conta SmartThings",
        "campo_dispositivo": (
            "Id do aparelho na SmartThings. Deixe vazio: o parear o pega da conta quando há um "
            "ar condicionado nela."
        ),
        "auth_ajuda": (
            "1. Abra https://account.smartthings.com/tokens e entre com a conta Samsung dona "
            "do ar condicionado, que é a conta do aplicativo SmartThings do celular.\n"
            "2. Crie um token com as permissões de LISTAR e de CONTROLAR aparelhos, e copie: "
            "o site o mostra UMA vez.\n"
            "3. Cole aqui e salve.\n"
            "4. Aperte Buscar aparelhos da conta e escolha o seu na lista."
        ),
        "cap_temperatura": (
            "O setpoint viaja em graus Celsius; uma conta em Fahrenheit é convertida nos dois "
            "sentidos e o painel nunca vê a outra escala."
        ),
        "cap_modo": (
            "Os modos deste modelo são lidos do estado dele, então um modo que a unidade não "
            "tem é recusado antes de qualquer coisa ser enviada."
        ),
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the cloud."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class SamsungSmartthings(Driver):
    """One air conditioner of a SmartThings account, read and commanded through the cloud."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={
            "pt": "Ar condicionado Samsung (SmartThings)",
            "en": "Samsung air conditioner (SmartThings)",
        },
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
            Campo(nome=CAMPO_TOKEN, tipo=TipoCampo.SEGREDO, obrigatorio=True),
            # The id of a device is a string that exists only inside the account, so demanding
            # it before the first call is demanding what nobody has yet. The token is enough:
            # parear lists the account and, when there is one air conditioner in it, takes its
            # id and writes it into the registration.
            Campo(nome=CAMPO_DISPOSITIVO, tipo=TipoCampo.TEXTO, obrigatorio=False),
        ),
        textos=TEXTOS,
        marca="Samsung",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._falhas = 0
        # What THIS unit accepts, refreshed by the poll because its status carries the lists;
        # a word the unit never named is refused here and never sent.
        self._modos: dict[str, str] = {}
        self._ventos: dict[str, str] = {}
        # The scale the account is set to, so a setpoint written back leaves in the scale the
        # unit reads and the contract keeps its degrees Celsius.
        self._fahrenheit = False
        self._adotado = ""
        self._fio = fio.Fio(log, cadastro.identidade)

    async def parar(self) -> None:
        sessao, self._sessao = self._sessao, None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    async def autenticar(self) -> str:
        """Checks the token and that this device is in the account, and writes what the
        account has into the log when it is not.
        """
        try:
            achados = await self._ares_da_conta()
        except _Falha as falha:
            log.warning(
                "%s: the SmartThings account refused the token: %s", self._id(), falha.codigo
            )
            return FALHOU
        if self._dispositivo() in {ar.id for ar in achados}:
            return PAREADO
        # An account with ONE air conditioner has no ambiguity to resolve, and making the
        # integrator copy an id out of a log to paste it back is a step that exists only
        # because nobody wrote this branch.
        if not self._dispositivo() and len(achados) == 1:
            self._adotado = achados[0].id
            log.info("%s: the account has one air conditioner, and it is now this one", self._id())
            return PAREADO
        for ar in achados:
            log.warning("air conditioner in the SmartThings account: %s (%s)", ar.id, ar.nome)
        if not achados:
            log.warning("the SmartThings account of %s has no air conditioner", self._id())
        return FALHOU

    async def aparelhos_da_conta(self) -> tuple[Aparelho, ...]:
        """The air conditioners this token reaches, for the operator to pick one."""
        achados = await self._ares_da_conta()
        return tuple(Aparelho(id=ar.id, nome=ar.nome) for ar in achados)

    def credenciais_do_aparelho(self) -> dict[str, str]:
        """The id the pairing found in the account, for the caller to persist."""
        return {CAMPO_DISPOSITIVO: self._adotado} if self._adotado else {}

    async def atualizar(self) -> None:
        """One poll: the status of the unit, which carries the state and its vocabulary."""
        try:
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
        if acao in (ACAO_LIGAR, ACAO_DESLIGAR):
            ligar = acao == ACAO_LIGAR
            await self._comandar(CAP_ENERGIA, COMANDO_LIGAR if ligar else COMANDO_DESLIGAR)
            self._defina(ligado=ligar)
            return None
        if acao == ACAO_TEMPERATURA:
            return await self._trocar_temperatura(valor)
        if acao == ACAO_MODO:
            return await self._trocar(valor, "_modos", CAP_MODO, COMANDO_MODO, ACAO_MODO)
        if acao == ACAO_VENTO:
            return await self._trocar(valor, "_ventos", CAP_VENTO, COMANDO_VENTO, ACAO_VENTO)
        return await super().executar(acao, valor)

    async def _trocar_temperatura(self, valor: object) -> str | None:
        if type(valor) is not int or not TEMPERATURA_MINIMA <= valor <= TEMPERATURA_MAXIMA:
            return INVALID_VALUE
        await self._comandar(
            CAP_SETPOINT, COMANDO_SETPOINT, [_para_a_unidade(valor, self._fahrenheit)]
        )
        self._defina(temperatura=valor)
        return None

    async def _trocar(
        self, valor: object, atributo: str, capacidade: str, comando: str, campo: str
    ) -> str | None:
        """One word of the vocabulary, in the spelling THIS unit answered with."""
        if not getattr(self, atributo):
            # A command that lands before the first poll has no vocabulary to check against,
            # and refusing it would refuse a word the unit does take; the status that carries
            # the vocabulary is read here instead, which is the same request the poll makes.
            self._aplicar(await self._pedir("GET", CAMINHO_ESTADO.format(id=self._dispositivo())))
        aceitas: dict[str, str] = getattr(self, atributo)
        palavra = aceitas.get(valor) if isinstance(valor, str) else None
        if palavra is None:
            return INVALID_VALUE
        await self._comandar(capacidade, comando, [palavra])
        self._defina(**{campo: valor})
        return None

    async def _comandar(
        self, capacidade: str, comando: str, argumentos: list | None = None
    ) -> None:
        ordem = {
            "commands": [
                {
                    "component": COMPONENTE,
                    "capability": capacidade,
                    "command": comando,
                    "arguments": argumentos or [],
                }
            ]
        }
        resposta = await self._pedir(
            "POST", CAMINHO_COMANDOS.format(id=self._dispositivo()), ordem=ordem
        )
        _conferir_resultados(resposta, capacidade, comando)

    def _aplicar(self, estado: dict) -> None:
        """The state and the vocabulary of the unit, from the one document the poll read."""
        self._modos = _vocabulario(estado, CAP_MODO, ATR_MODOS, PALAVRAS_DE_MODO)
        self._ventos = _vocabulario(estado, CAP_VENTO, ATR_VENTOS, PALAVRAS_DE_VENTO)
        energia = _texto(_valor(estado, CAP_ENERGIA, ATR_ENERGIA))
        setpoint, unidade = _valor_e_unidade(estado, CAP_SETPOINT, ATR_SETPOINT)
        self._fahrenheit = unidade.upper() == FAHRENHEIT
        self._defina(
            online=True,
            ligado=None if not energia else energia.lower() == COMANDO_LIGAR,
            temperatura=_para_o_contrato(setpoint, self._fahrenheit),
            modo=_palavra_de(_valor(estado, CAP_MODO, ATR_MODO), PALAVRAS_DE_MODO),
            vento=_palavra_de(_valor(estado, CAP_VENTO, ATR_VENTO), PALAVRAS_DE_VENTO),
            detalhe="",
        )

    async def _ares_da_conta(self) -> tuple["_Ar", ...]:
        """The air conditioners of the account, asked with the capability as the filter.

        The filter is the CAPABILITY this driver commands and never a category name, because
        a category is a label of the model and the capability is what says the unit takes the
        commands this driver sends. The answer is filtered again here, so a cloud that ignored
        the parameter does not put a doorbell in the list.
        """
        listagem = await self._pedir(
            "GET", CAMINHO_DISPOSITIVOS, parametros={"capability": CAP_MODO}
        )
        achados = []
        for bruto in listagem.get(CHAVE_ITENS, []):
            if not isinstance(bruto, dict) or not _tem_capacidade(bruto, CAP_MODO):
                continue
            identificador = _texto(bruto.get("deviceId"))
            if not identificador:
                continue
            nome = _texto(bruto.get("label")) or _texto(bruto.get("name")) or identificador
            achados.append(_Ar(identificador, nome))
        return tuple(achados)

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self._id(), self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._defina(online=False, detalhe=codigo)

    def _id(self) -> str:
        return self.cadastro.identidade

    def _dispositivo(self) -> str:
        """The id in force: the one the pairing adopted, or the one the registration carries.

        The adopted one comes first because the registration only learns it after the caller
        writes it, and the poll that follows the pairing must not wait for that trip.
        """
        return self._adotado or self.cadastro.campos.get(CAMPO_DISPOSITIVO, "").strip()

    def _token(self) -> str:
        return self.cadastro.segredos.get(CAMPO_TOKEN, "").strip()

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    async def _pedir(
        self,
        metodo: str,
        caminho: str,
        *,
        ordem: dict | None = None,
        parametros: dict[str, str] | None = None,
    ) -> dict:
        """One exchange with the cloud, answered as the document it sent."""
        token = self._token()
        if not _TOKEN.fullmatch(token):
            # A registration saved with a bad field would otherwise spend a request of the
            # account to be told what this line already knows.
            raise _Falha(AUTH_PENDENTE)
        if caminho != CAMINHO_DISPOSITIVOS and not _ID_DE_DISPOSITIVO.fullmatch(
            self._dispositivo()
        ):
            raise _Falha(AUTH_PENDENTE)
        url = BASE.format(caminho=caminho)
        # The transcript names the path and the order and never the headers, because the token
        # of the account rides in a header and keeps it out of the log.
        rotina = metodo == "GET" and caminho.endswith("/status")
        self._fio.enviado(
            f"{metodo} {caminho}" + (f" {fio.redigir(ordem)}" if ordem else ""), rotina=rotina
        )
        sessao = await self._abrir()
        try:
            async with sessao.request(
                metodo,
                url,
                json=ordem,
                params=parametros,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                # A redirect would send the token of the customer to whatever host answered,
                # which is the one thing that must never travel sideways.
                allow_redirects=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            self._fio.falhou(f"{metodo} {caminho}", erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        return _corpo_de(estado, bruto, caminho)


class _Ar:
    """One air conditioner of the account, as the listing names it."""

    __slots__ = ("id", "nome")

    def __init__(self, identificador: str, nome: str) -> None:
        self.id = identificador
        self.nome = nome


def _corpo_de(estado: int, bruto: bytes, caminho: str) -> dict:
    """The document the cloud sent, or the stable code the status deserves."""
    if estado in (401, 403):
        # A token that expired or was revoked is not a device that failed, and the panel says
        # so with the one code that means "pair it again". Every token created after 30
        # December 2024 gets here 24 hours after it was made.
        raise _Falha(AUTH_PENDENTE)
    if estado == 404:
        # The id of the registration is not in this account any more, and pairing again is
        # the fix, so the panel gets the code that says exactly that.
        log.warning("SmartThings does not know the device of %s any more", caminho)
        raise _Falha(AUTH_PENDENTE)
    if estado == 429:
        # The cloud is throttling this token, which is not the air conditioner failing; the
        # next poll finds it again and the log names the reason this one did not.
        log.warning("the SmartThings cloud is throttling this token (HTTP 429)")
        raise _Falha(EQ_OFFLINE)
    if estado in (400, 422):
        raise _Falha(INVALID_VALUE)
    if estado >= 400:
        log.warning("the SmartThings cloud answered HTTP %d to %s", estado, caminho)
        raise _Falha(ERRO_APARELHO)
    try:
        documento = json.loads(bruto.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError) as erro:
        raise _Falha(ERRO_APARELHO) from erro
    if not isinstance(documento, dict):
        raise _Falha(ERRO_APARELHO)
    return documento


def _conferir_resultados(resposta: dict, capacidade: str, comando: str) -> None:
    """A command answered 200 and refused inside the body is the unit saying no."""
    resultados = resposta.get(CHAVE_RESULTADOS)
    if not isinstance(resultados, list) or not resultados:
        # A cloud that answered 200 and nothing else did the thing; only a result that IS
        # there and says otherwise is a refusal.
        return
    for resultado in resultados:
        situacao = _texto(resultado.get(CHAVE_SITUACAO)) if isinstance(resultado, dict) else ""
        if situacao.upper() not in ACEITO:
            log.warning(
                "the unit refused %s %s with %s", capacidade, comando, situacao or "no status"
            )
            raise _Falha(ERRO_APARELHO)


def _principal(estado: dict) -> dict:
    componentes = estado.get(CHAVE_COMPONENTES)
    principal = componentes.get(COMPONENTE) if isinstance(componentes, dict) else None
    return principal if isinstance(principal, dict) else {}


def _atributo(estado: dict, capacidade: str, atributo: str) -> dict:
    bloco = _principal(estado).get(capacidade)
    if not isinstance(bloco, dict):
        return {}
    dado = bloco.get(atributo)
    return dado if isinstance(dado, dict) else {}


def _valor(estado: dict, capacidade: str, atributo: str) -> object:
    return _atributo(estado, capacidade, atributo).get(CHAVE_VALOR)


def _valor_e_unidade(estado: dict, capacidade: str, atributo: str) -> tuple[object, str]:
    dado = _atributo(estado, capacidade, atributo)
    return dado.get(CHAVE_VALOR), _texto(dado.get(CHAVE_UNIDADE))


def _vocabulario(
    estado: dict, capacidade: str, atributo: str, palavras: dict[str, tuple[str, ...]]
) -> dict[str, str]:
    """The words this unit accepts, mapped to the words it uses for them.

    Two models of the same brand do not agree on spelling, and matching without case and
    without punctuation is what makes fanOnly, fan_only and FANONLY the same word.
    """
    aceitas = _valor(estado, capacidade, atributo)
    aceitas = aceitas if isinstance(aceitas, list) else []
    saida: dict[str, str] = {}
    for nossa, grafias in palavras.items():
        for aceita in aceitas:
            texto = _texto(aceita)
            if texto and _normal(texto) in grafias:
                saida[nossa] = texto
                break
    return saida


def _tem_capacidade(dispositivo: dict, capacidade: str) -> bool:
    """Whether the main component of a device declares one capability."""
    for componente in dispositivo.get(CHAVE_COMPONENTES, []):
        if not isinstance(componente, dict) or _texto(componente.get("id")) != COMPONENTE:
            continue
        for declarada in componente.get("capabilities", []):
            if isinstance(declarada, dict) and _texto(declarada.get("id")) == capacidade:
                return True
    return False


def _palavra_de(bruto: object, palavras: dict[str, tuple[str, ...]]) -> str | None:
    """The word for what the unit answered, or None for one nobody named."""
    lido = _normal(_texto(bruto))
    if not lido:
        return None
    for nossa, grafias in palavras.items():
        if lido in grafias:
            return nossa
    return None


def _para_o_contrato(bruto: object, fahrenheit: bool) -> int | None:
    """The setpoint of the unit as the whole degrees Celsius the contract carries."""
    numero = _numero(bruto)
    if numero is None:
        return None
    return round((numero - 32) * 5 / 9) if fahrenheit else round(numero)


def _para_a_unidade(valor: int, fahrenheit: bool) -> int:
    """The degrees Celsius of the contract in the scale the account is set to."""
    return round(valor * 9 / 5 + 32) if fahrenheit else valor


def _numero(bruto: object) -> float | None:
    if isinstance(bruto, bool):
        return None
    if isinstance(bruto, (int, float)):
        return float(bruto)
    return None


def _normal(bruto: str) -> str:
    return _NAO_PALAVRA.sub("", bruto.strip().lower())


def _texto(bruto: object) -> str:
    return bruto.strip() if isinstance(bruto, str) else ""
