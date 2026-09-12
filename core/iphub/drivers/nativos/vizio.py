# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda

"""Vizio SmartCast televisions and sound bars, paired by a code on the screen.

What the protocol is, so nobody has to read it again:

- HTTPS on 7345 for a television and on 9000 for a sound bar, which is why the port is a
  field of the registration and not a constant. The certificate is one the device signs for
  itself and no authority backs, so this driver does not verify it: the wire is confidential
  against a passive listener on the segment and worth NOTHING against someone who can sit in
  the middle of it, and the pairing token rides exactly this wire. That is written here
  instead of hidden.
- Pairing is two messages and a code on the screen: /pairing/start makes the device show four
  digits and answers a request token, and /pairing/pair with those digits answers an AUTH
  token. The token is what every later message carries, in a header named AUTH, and it is
  handed to the caller to persist so nobody walks to the television twice.
- The settings live in a menu tree, and every value read from it carries a HASHVAL that the
  write of that value has to quote. A stale hash is refused by the device, so this driver
  reads the item it is about to write, right before writing it.
- The power state is the ONE thing readable with no token at all, and it is what says the
  device is on; the level and the mute come from the audio menu, and both are already on the
  scale of the contract.
- A key of the remote is a pair of numbers, a code set and a code. Only the pairs this driver
  is sure of are declared: the transport of a sound bar and the number keys are not among
  them, and a key that is not declared is a key the panel never draws.
"""

import json
import logging

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import Cadastro, ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.manifesto import Auth, Campo, Descoberta, Manifesto, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.vizio")

TIPO = "tv_vizio_smartcast"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"
AUTH_PENDENTE = "auth_pendente"
AGUARDANDO = "aguardando"
FALHOU = "falhou"
PAREADO = "pareado"

PORTA_PADRAO = 7345
TEMPO_LIMITE_S = 6.0
CORPO_MAXIMO = 256 * 1024
FALHAS_ATE_OFFLINE = 2

CAMPO_PORTA = "porta"
CAMPO_CODIGO = "codigo"
CAMPO_TOKEN = "token"

NOME_DO_CLIENTE = "QA IP Hub"
ID_DO_CLIENTE = "qa-ip-hub"
DESAFIO = 1

CAMINHO_ENERGIA = "/state/device/power_mode"
CAMINHO_INFO = "/state/device/deviceinfo"
CAMINHO_AUDIO = "/menu_native/dynamic/tv_settings/audio"
CAMINHO_ENTRADA = "/menu_native/dynamic/tv_settings/devices/current_input"
CAMINHO_ENTRADAS = "/menu_native/dynamic/tv_settings/devices/name_input"
CAMINHO_TECLA = "/key_command/"

ITEM_VOLUME = "volume"
ITEM_MUDO = "mute"
LIGADO = "On"
DESLIGADO = "Off"

# The code sets of the remote, and inside each one the code of the key. Only the pairs this
# driver is sure of live here; the rest of the tree is not guessed.
CONJUNTO_ENERGIA = 11
CODIGO_DESLIGAR = 0
CODIGO_LIGAR = 1
TECLAS = {
    "menos": (5, 0),
    "mais": (5, 1),
    "canal_menos": (8, 0),
    "canal_mais": (8, 1),
    "baixo": (3, 0),
    "esquerda": (3, 1),
    "ok": (3, 2),
    "direita": (3, 7),
    "cima": (3, 8),
    "voltar": (4, 0),
    "info": (4, 6),
    "menu": (4, 8),
    "inicio": (4, 15),
}
ACOES_DE_TRANSPORTE = {"pausar": (2, 2), "tocar": (2, 3)}

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TECLA = "tecla"

TEXTOS = {
    "en": {
        "descricao": (
            "Vizio SmartCast television or sound bar: power, volume, mute, input and the keys "
            "of the remote, over a wire the device pairs with a code on its screen."
        ),
        "preparo": (
            "On the device: Network, and leave it on the network of the hub; then System, "
            "Power Mode, Quick Start, which is what lets it be turned on again after it is "
            "off. A sound bar listens on 9000 and a television on 7345."
        ),
        "auth_ajuda": (
            "1. Press pair. The device shows four digits on its screen.\n"
            "2. Type them in the code field of this registration and save.\n"
            "3. Press pair again. The token is kept by the hub, so this is done once."
        ),
        "campo_porta": "7345 on a television, 9000 on a sound bar.",
        "campo_codigo": "The four digits the device shows while pairing.",
        "campo_token": "Filled by the pairing. Nobody types this.",
        "cap_tecla": "The keys of the remote, including the two of the volume.",
    },
    "pt": {
        "descricao": (
            "Televisão ou soundbar Vizio SmartCast: energia, volume, mudo, entrada e as teclas "
            "do controle, por um fio que o aparelho pareia com um código na tela."
        ),
        "preparo": (
            "No aparelho: Rede, e deixe-o na rede do hub; depois Sistema, Modo de Energia, "
            "Início Rápido, que é o que permite ligá-lo de novo depois de desligado. Uma "
            "soundbar escuta na 9000 e uma televisão na 7345."
        ),
        "auth_ajuda": (
            "1. Aperte parear. O aparelho mostra quatro dígitos na tela.\n"
            "2. Digite-os no campo código deste cadastro e salve.\n"
            "3. Aperte parear de novo. O token fica com o hub, então isso é feito uma vez."
        ),
        "campo_porta": "7345 numa televisão, 9000 numa soundbar.",
        "campo_codigo": "Os quatro dígitos que o aparelho mostra ao parear.",
        "campo_token": "Preenchido pelo pareamento. Ninguém digita isto.",
        "cap_tecla": "As teclas do controle, incluindo as duas do volume.",
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the device."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Vizio(Driver):
    """One Vizio SmartCast, commanded with the token its own screen handed over."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Vizio SmartCast", "en": "Vizio SmartCast"},
        categoria="tv",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_TECLA,
            *ACOES_DE_TRANSPORTE,
        ),
        teclas=tuple(TECLAS),
        auth=Auth.CODIGO,
        config_campos=(
            Campo(nome=CAMPO_PORTA, tipo=TipoCampo.INTEIRO, obrigatorio=False),
            Campo(nome=CAMPO_CODIGO, tipo=TipoCampo.TEXTO, obrigatorio=False),
            # The token is handed over by the device while pairing and given to the caller to
            # persist; it is a field so it lands in the secrets, never typed by the operator.
            Campo(nome=CAMPO_TOKEN, tipo=TipoCampo.SEGREDO, obrigatorio=False),
        ),
        # The device answers the announcement of a screen like a dozen other things do, so it
        # is found by the range sweep, which asks every address who it is.
        descoberta=Descoberta(),
        textos=TEXTOS,
        marca="Vizio",
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._token = cadastro.segredos.get(CAMPO_TOKEN, "").strip()
        self._pedido_de_pareamento = 0
        self._entradas: dict[str, str] = {}
        self._falhas = 0
        self._fio = fio.Fio(log, cadastro.identidade)

    async def parar(self) -> None:
        sessao, self._sessao = self._sessao, None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    def credenciais_do_aparelho(self) -> dict[str, str]:
        """The token the pairing bound to this device, for the caller to persist."""
        return {CAMPO_TOKEN: self._token} if self._token else {}

    async def autenticar(self) -> str:
        """Pareado, aguardando while the code is on the screen, or falhou."""
        codigo = self.cadastro.campos.get(CAMPO_CODIGO, "").strip()
        try:
            if not self._pedido_de_pareamento or not codigo:
                await self._comecar_a_parear()
                return AGUARDANDO
            await self._terminar_de_parear(codigo)
        except _Falha as falha:
            log.warning("%s: pairing failed with %s", self.cadastro.identidade, falha.codigo)
            self._pedido_de_pareamento = 0
            return FALHOU
        log.info("%s: paired", self.cadastro.identidade)
        return PAREADO

    async def _comecar_a_parear(self) -> None:
        """The first message, which is what puts the four digits on the screen."""
        dado = await self._pedir(
            "PUT",
            "/pairing/start",
            {"DEVICE_ID": ID_DO_CLIENTE, "DEVICE_NAME": NOME_DO_CLIENTE},
            sem_token=True,
        )
        pedido = _item(dado).get("PAIRING_REQ_TOKEN")
        if not isinstance(pedido, int):
            raise _Falha(ERRO_APARELHO)
        self._pedido_de_pareamento = pedido

    async def _terminar_de_parear(self, codigo: str) -> None:
        """The second message, which trades the four digits for the token."""
        dado = await self._pedir(
            "PUT",
            "/pairing/pair",
            {
                "DEVICE_ID": ID_DO_CLIENTE,
                "CHALLENGE_TYPE": DESAFIO,
                "RESPONSE_VALUE": codigo,
                "PAIRING_REQ_TOKEN": self._pedido_de_pareamento,
            },
            sem_token=True,
        )
        token = _item(dado).get("AUTH_TOKEN")
        if not isinstance(token, str) or not token:
            raise _Falha(ERRO_APARELHO)
        self._token = token
        self._pedido_de_pareamento = 0

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
            await self._tecla(CONJUNTO_ENERGIA, CODIGO_LIGAR)
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            await self._tecla(CONJUNTO_ENERGIA, CODIGO_DESLIGAR)
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            if not isinstance(valor, int) or isinstance(valor, bool) or not 0 <= valor <= 100:
                return INVALID_VALUE
            await self._escrever_no_menu(CAMINHO_AUDIO, ITEM_VOLUME, valor)
            self._defina(volume=valor)
            return None
        if acao == ACAO_MUDO:
            if not isinstance(valor, bool):
                return INVALID_VALUE
            await self._escrever_no_menu(CAMINHO_AUDIO, ITEM_MUDO, LIGADO if valor else DESLIGADO)
            self._defina(mudo=valor)
            return None
        if acao == ACAO_FONTE:
            return await self._trocar_entrada(valor)
        if acao == ACAO_TECLA:
            par = TECLAS.get(valor) if isinstance(valor, str) else None
            if par is None:
                return INVALID_VALUE
            await self._tecla(*par)
            return None
        if acao in ACOES_DE_TRANSPORTE:
            await self._tecla(*ACOES_DE_TRANSPORTE[acao])
            return None
        return NAO_SUPORTADO

    async def _trocar_entrada(self, valor: object) -> str | None:
        if not isinstance(valor, str) or not valor:
            return INVALID_VALUE
        await self._ler_entradas()
        escolhido = _nome_da_entrada(self._entradas, valor)
        if escolhido is None:
            return INVALID_VALUE
        await self._escrever_no_menu(CAMINHO_ENTRADA, "", escolhido)
        self._defina(fonte=self._entradas.get(escolhido, escolhido))
        return None

    async def _ler_estado(self) -> None:
        """One poll: the power, which needs no token, and then the audio and the input."""
        energia = _valor_do_item(await self._pedir("GET", CAMINHO_ENERGIA, None, sem_token=True))
        ligado = energia == 1 if isinstance(energia, int) else None
        if ligado is False:
            # A device that is off answers nothing else, so nothing else is asked of it.
            self._defina(online=True, ligado=False, detalhe="")
            return
        audio = _itens_por_nome(await self._pedir("GET", CAMINHO_AUDIO, None))
        nivel = audio.get(ITEM_VOLUME, {}).get("VALUE")
        mudo = audio.get(ITEM_MUDO, {}).get("VALUE")
        await self._ler_entradas()
        atual = _valor_do_item(await self._pedir("GET", CAMINHO_ENTRADA, None))
        self._defina(
            online=True,
            ligado=True,
            volume=nivel if isinstance(nivel, int) else None,
            mudo=mudo == LIGADO if isinstance(mudo, str) else None,
            fonte=self._entradas.get(atual, atual) if isinstance(atual, str) else None,
            fontes=tuple(self._entradas.values()),
            detalhe="",
        )

    async def _ler_entradas(self) -> None:
        """The inputs as the owner named them on the screen, asked once and kept."""
        if self._entradas:
            return
        itens = _itens(await self._pedir("GET", CAMINHO_ENTRADAS, None))
        achadas: dict[str, str] = {}
        for item in itens:
            nome = item.get("NAME")
            valor = item.get("VALUE")
            if not isinstance(nome, str) or not nome:
                continue
            achadas[nome] = valor if isinstance(valor, str) and valor else nome
        self._entradas = achadas

    async def _escrever_no_menu(self, caminho: str, nome: str, valor: object) -> None:
        """One write, with the hash read a moment earlier: a stale one is refused."""
        alvo = f"{caminho}/{nome}" if nome else caminho
        dado = await self._pedir("GET", alvo, None)
        hash_do_item = _hash_do_item(dado, nome)
        if hash_do_item is None:
            raise _Falha(ERRO_APARELHO)
        await self._pedir(
            "PUT", alvo, {"REQUEST": "MODIFY", "VALUE": valor, "HASHVAL": hash_do_item}
        )

    async def _tecla(self, conjunto: int, codigo: int) -> None:
        mensagem = {
            "KEYLIST": [{"CODESET": conjunto, "CODE": codigo, "ACTION": "KEYPRESS"}],
        }
        await self._pedir("PUT", CAMINHO_TECLA, mensagem)

    async def _pedir(
        self, metodo: str, caminho: str, mensagem: dict | None, *, sem_token: bool = False
    ) -> dict:
        """One exchange. The token rides a header and never lands in the transcript."""
        if not sem_token and not self._token:
            raise _Falha(AUTH_PENDENTE)
        url = f"https://{self._endereco()}:{self._porta()}{caminho}"
        cabecalhos = {"Content-Type": "application/json"}
        if not sem_token:
            cabecalhos["AUTH"] = self._token
        rotina = metodo == "GET"
        self._fio.enviado(f"{metodo} {caminho} {mensagem or ''}".strip(), rotina=rotina)
        sessao = await self._abrir()
        dados = None if mensagem is None else json.dumps(mensagem).encode("utf-8")
        try:
            async with sessao.request(
                metodo,
                url,
                data=dados,
                headers=cabecalhos,
                allow_redirects=False,
                # The certificate is the one the device signed for itself, and there is no
                # authority on a local network to check it against.
                ssl=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            self._fio.falhou(f"{metodo} {caminho}", erro)
            raise _Falha(EQ_OFFLINE) from erro
        self._fio.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        if estado in (401, 403):
            raise _Falha(AUTH_PENDENTE)
        if estado != 200:
            raise _Falha(ERRO_APARELHO)
        try:
            dado = json.loads(bruto)
        except ValueError as erro:
            raise _Falha(ERRO_APARELHO) from erro
        if not isinstance(dado, dict):
            raise _Falha(ERRO_APARELHO)
        self._julgar(caminho, dado)
        return dado

    def _julgar(self, caminho: str, dado: dict) -> None:
        """The device answers 200 to a refusal too; the verdict is inside the body."""
        estado = dado.get("STATUS")
        resultado = estado.get("RESULT", "") if isinstance(estado, dict) else ""
        if not isinstance(resultado, str) or resultado.casefold() == "success":
            return
        chave = resultado.casefold()
        codigo = AUTH_PENDENTE if "auth" in chave or "blocked" in chave else ERRO_APARELHO
        self._fio.recusado(f"{caminho} {resultado}", codigo)
        raise _Falha(codigo)

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._defina(online=False, ligado=False, detalhe=codigo)

    def _porta(self) -> int:
        bruto = self.cadastro.campos.get(CAMPO_PORTA, "").strip()
        return int(bruto) if bruto.isdigit() and 0 < int(bruto) < 65536 else PORTA_PADRAO

    def _endereco(self) -> str:
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The serial the device publishes with no token, on either of the two ports."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        for porta in (PORTA_PADRAO, 9000):
            url = f"https://{endereco}:{porta}{CAMINHO_INFO}"
            try:
                async with (
                    ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) as sessao,
                    sessao.get(url, allow_redirects=False, ssl=False) as resposta,
                ):
                    if resposta.status != 200:
                        continue
                    bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
            except (TimeoutError, ClientError, OSError, ValueError):
                continue
            try:
                dado = json.loads(bruto)
            except ValueError:
                continue
            serial = _procurar(dado, "SERIAL_NUMBER")
            if serial:
                return serial
        return None


def _itens(dado: dict) -> list[dict]:
    bruto = dado.get("ITEMS")
    if isinstance(bruto, dict):
        return [bruto]
    if not isinstance(bruto, list):
        return []
    return [item for item in bruto if isinstance(item, dict)]


def _item(dado: dict) -> dict:
    itens = _itens(dado)
    return itens[0] if itens else {}


def _itens_por_nome(dado: dict) -> dict[str, dict]:
    return {item["CNAME"]: item for item in _itens(dado) if isinstance(item.get("CNAME"), str)}


def _valor_do_item(dado: dict) -> object:
    return _item(dado).get("VALUE")


def _hash_do_item(dado: dict, nome: str) -> int | None:
    """The hash of the item just read, whether the menu answered one item or the whole list."""
    for item in _itens(dado):
        if nome and item.get("CNAME") != nome:
            continue
        hash_do_item = item.get("HASHVAL")
        if isinstance(hash_do_item, int):
            return hash_do_item
    return None


def _nome_da_entrada(entradas: dict[str, str], valor: str) -> str | None:
    """The name of an input by its own name or by the name the owner gave it."""
    if valor in entradas:
        return valor
    procurado = valor.strip().casefold()
    for nome, rotulo in entradas.items():
        if rotulo.strip().casefold() == procurado or nome.strip().casefold() == procurado:
            return nome
    return None


def _procurar(dado: object, chave: str) -> str:
    """The first value of that key anywhere in the answer, which nests differently by year."""
    if isinstance(dado, dict):
        valor = dado.get(chave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
        for dentro in dado.values():
            achado = _procurar(dentro, chave)
            if achado:
                return achado
    if isinstance(dado, list):
        for dentro in dado:
            achado = _procurar(dentro, chave)
            if achado:
                return achado
    return ""
