# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 7: the declarative file as typed data, and the validation that refuses a bad one.

This module opens no socket and imports no transport, so the API validates a file before
saving it and the loader refuses one at boot without pulling the engine in. The format is
DATA and never program: no condition, no loop, no expression, no arithmetic. Only three
substitutions exist inside the text of a command, and the engine applies them in one pass:
{valor} (the chosen value, already mapped), {valor_escala} (the volume 0 to 100 converted
to the scale of the device) and {ip} (the address of the registration).

The accepted keys are the whole format, and a key outside them is refused instead of
ignored, because a key that was typed and that nothing reads is a driver silently doing less:

  file:       manifesto, transporte, comandos, estado, escala_volume, descoberta
  manifesto:  tipo, rotulo, categoria, capacidades, auth, config_campos, textos
  transporte: exactly one of tcp, http, udp
              tcp:  porta, terminador, timeout_s, intervalo_min_ms, saudacao
              http: base, metodo, timeout_s, cabecalhos
              udp:  porta, terminador, timeout_s, intervalo_min_ms
  comandos:   one entry per declared capacidade, each one a step (envia, plus hex for tcp
              and udp, plus metodo and corpo for http) or a sequencia of steps, and
              valores, repete and intervalo_ms
  estado:     pede (one or more steps) and le
  estado.le:  one entry per Estado field, each with regex or json, plus verdadeiro

Seção 7: o arquivo declarativo como dado tipado, e a validação que recusa um arquivo ruim.

Este módulo não abre socket e não importa transporte, então a API valida um arquivo antes
de salvar e o carregador recusa um no boot sem puxar o motor junto. O formato é DADO e
nunca programa: sem condicional, sem laço, sem expressão, sem aritmética. Só existem três
substituições dentro do texto de um comando, e o motor as aplica numa passada: {valor} (o
valor escolhido, já traduzido), {valor_escala} (o volume 0 a 100 convertido para a escala
do aparelho) e {ip} (o endereço do cadastro).

As chaves aceitas são o formato inteiro, e uma chave fora delas é recusada em vez de
ignorada, porque uma chave digitada que ninguém lê é um driver fazendo menos em silêncio.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from iphub.drivers.manifesto import (
    CAPACIDADES,
    CATEGORIAS,
    IDIOMAS,
    PREFIXO_TEXTO_CAMPO,
    Auth,
    Campo,
    Descoberta,
    Manifesto,
    ManifestoInvalido,
    TipoCampo,
)
from iphub.drivers.manifesto import validar as validar_manifesto

MOTOR = "declarativo"

TRANSPORTES = ("tcp", "http", "udp")
METODOS = ("GET", "POST", "PUT")

# Why: online comes from the transport and detalhe carries a code of base.CODIGOS, so
# neither is a reading a file may claim; fontes comes from the valores of the fonte command,
# which is the same fact written once.
# Por que: o online vem do transporte e o detalhe carrega um código de base.CODIGOS, então
# nenhum dos dois é leitura que um arquivo possa reivindicar; as fontes vêm dos valores do
# comando de fonte, que é o mesmo fato escrito uma vez.
# Why: section 6 publishes the transport and the title as different facts, and section 2
# refuses two engines that diverge, so a declaration reads reproduzindo the same way a native
# driver publishes it.
# Por que: a seção 6 publica o transporte e o título como fatos diferentes, e a seção 2 recusa
# dois motores que divergem, então uma declaração lê o reproduzindo do mesmo jeito que um
# driver nativo o publica.
# Why: section 7, a file reads temperatura, modo and vento the way it reads fonte, so an air
# conditioner and a receiver with sound modes are declared and never programmed.
# Por que: seção 7, um arquivo lê temperatura, modo e vento do jeito que lê fonte, então um
# ar condicionado e um receiver com modos de som são declarados e nunca programados.
LEITURAS = (
    "ligado",
    "volume",
    "mudo",
    "fonte",
    "reproduzindo",
    "tocando",
    "temperatura",
    "modo",
    "vento",
)
BOOLEANAS = ("ligado", "mudo", "reproduzindo")
INTEIRAS = ("volume", "temperatura")

# The lists of words a manifest of section 6 declares, each for one spoken capability.
# As listas de palavras que um manifesto da seção 6 declara, cada uma para uma capacidade
# falada.
VOCABULARIOS = ("teclas", "modos", "ventos")

CHAVES_ARQUIVO = ("manifesto", "transporte", "comandos", "estado", "escala_volume", "descoberta")
CHAVES_MANIFESTO = (
    "tipo",
    "rotulo",
    "categoria",
    "capacidades",
    "auth",
    "config_campos",
    "textos",
    "teclas",
    "modos",
    "ventos",
)
CHAVES_CAMPO = ("nome", "tipo", "obrigatorio", "padrao")
CHAVES_TCP = ("porta", "terminador", "timeout_s", "intervalo_min_ms", "saudacao")
CHAVES_HTTP = ("base", "metodo", "timeout_s", "cabecalhos")
CHAVES_UDP = ("porta", "terminador", "timeout_s", "intervalo_min_ms")
CHAVES_COMANDO = ("sequencia", "valores", "repete", "intervalo_ms")
CHAVES_PASSO_LINHA = ("envia", "hex")
CHAVES_PASSO_HTTP = ("envia", "metodo", "corpo")
CHAVES_ESTADO = ("pede", "le")
CHAVES_LEITURA = ("regex", "json", "verdadeiro")
CHAVES_ESCALA = ("min", "max")
CHAVES_DESCOBERTA = ("ssdp_st", "ssdp_fabricantes", "mdns_servicos")

CAMPO_ARQUIVO = "arquivo"

DECL_NAO_OBJETO = "decl_nao_objeto"
DECL_CHAVE_DESCONHECIDA = "decl_chave_desconhecida"
DECL_MANIFESTO_INVALIDO = "decl_manifesto_invalido"
DECL_TIPO_INVALIDO = "decl_tipo_invalido"
DECL_ROTULO_INVALIDO = "decl_rotulo_invalido"
DECL_CATEGORIA_INVALIDA = "decl_categoria_invalida"
DECL_CAPACIDADE_DESCONHECIDA = "decl_capacidade_desconhecida"
DECL_VOCABULARIO_INVALIDO = "decl_vocabulario_invalido"
DECL_AUTH_INVALIDA = "decl_auth_invalida"
DECL_CONFIG_CAMPO_INVALIDO = "decl_config_campo_invalido"
DECL_TEXTOS_INVALIDOS = "decl_textos_invalidos"
DECL_DESCOBERTA_INVALIDA = "decl_descoberta_invalida"
DECL_TRANSPORTE_INVALIDO = "decl_transporte_invalido"
DECL_PORTA_INVALIDA = "decl_porta_invalida"
DECL_TIMEOUT_INVALIDO = "decl_timeout_invalido"
DECL_INTERVALO_INVALIDO = "decl_intervalo_invalido"
DECL_TERMINADOR_INVALIDO = "decl_terminador_invalido"
DECL_BASE_INVALIDA = "decl_base_invalida"
DECL_METODO_INVALIDO = "decl_metodo_invalido"
DECL_CABECALHO_INVALIDO = "decl_cabecalho_invalido"
DECL_COMANDO_INVALIDO = "decl_comando_invalido"
DECL_COMANDO_VAZIO = "decl_comando_vazio"
DECL_VALORES_INVALIDO = "decl_valores_invalido"
DECL_REPETE_INVALIDO = "decl_repete_invalido"
DECL_HEX_INVALIDO = "decl_hex_invalido"
DECL_ESTADO_INVALIDO = "decl_estado_invalido"
DECL_LEITURA_INVALIDA = "decl_leitura_invalida"
DECL_LEITURA_VAZIA = "decl_leitura_vazia"
DECL_CAMPO_DESCONHECIDO = "decl_campo_desconhecido"
DECL_REGEX_INVALIDA = "decl_regex_invalida"
DECL_REGEX_SEM_GRUPO = "decl_regex_sem_grupo"
DECL_REGEX_PERIGOSA = "decl_regex_perigosa"
DECL_ESCALA_INVALIDA = "decl_escala_invalida"
# Why: a text a lone surrogate makes unwritable is not the same fault as the field being
# absent or empty, and answering the caller code told the integrator the command was empty
# when it was there; the panel translates a phrase, so the phrase has to be about the fault.
# Por que: um texto que um surrogado solto torna ingravável não é a mesma falta que o campo
# ausente ou vazio, e responder o código do chamador dizia ao integrador que o comando
# estava vazio quando ele estava lá; o painel traduz uma frase, então a frase tem de ser
# sobre a falta.
DECL_TEXTO_NAO_GRAVAVEL = "decl_texto_nao_gravavel"

# The stable vocabulary the panel translates, section 11: the API never answers a phrase.
# O vocabulário estável que o painel traduz, seção 11: a API nunca responde frase.
CODIGOS = (
    DECL_NAO_OBJETO,
    DECL_CHAVE_DESCONHECIDA,
    DECL_MANIFESTO_INVALIDO,
    DECL_TIPO_INVALIDO,
    DECL_ROTULO_INVALIDO,
    DECL_CATEGORIA_INVALIDA,
    DECL_CAPACIDADE_DESCONHECIDA,
    DECL_VOCABULARIO_INVALIDO,
    DECL_AUTH_INVALIDA,
    DECL_CONFIG_CAMPO_INVALIDO,
    DECL_TEXTOS_INVALIDOS,
    DECL_DESCOBERTA_INVALIDA,
    DECL_TRANSPORTE_INVALIDO,
    DECL_PORTA_INVALIDA,
    DECL_TIMEOUT_INVALIDO,
    DECL_INTERVALO_INVALIDO,
    DECL_TERMINADOR_INVALIDO,
    DECL_BASE_INVALIDA,
    DECL_METODO_INVALIDO,
    DECL_CABECALHO_INVALIDO,
    DECL_COMANDO_INVALIDO,
    DECL_COMANDO_VAZIO,
    DECL_VALORES_INVALIDO,
    DECL_REPETE_INVALIDO,
    DECL_HEX_INVALIDO,
    DECL_ESTADO_INVALIDO,
    DECL_LEITURA_INVALIDA,
    DECL_LEITURA_VAZIA,
    DECL_CAMPO_DESCONHECIDO,
    DECL_REGEX_INVALIDA,
    DECL_REGEX_SEM_GRUPO,
    DECL_REGEX_PERIGOSA,
    DECL_ESCALA_INVALIDA,
    DECL_TEXTO_NAO_GRAVAVEL,
)

# Why: every ceiling below exists because the value crosses into something real. The tipo
# becomes a file name, a text reaches the panel, a step becomes bytes on a wire, and a
# repetition becomes that many round trips against a device on the customer LAN.
# Por que: todo teto abaixo existe porque o valor atravessa para algo real. O tipo vira nome
# de arquivo, um texto chega ao painel, um passo vira bytes num fio, e uma repetição vira
# essa quantidade de idas e vindas contra um aparelho na LAN do cliente.
TIPO_MAXIMO = 32
TEXTO_MAXIMO = 512
NOME_MAXIMO = 40
# Why: section 6 asks for a text named campo_<nome> in both languages, and a nome long enough
# to push that key past NOME_MAXIMO asked for a text no file could write, so the field could
# never be validated and the refusal named manifesto.textos instead of the field.
# Por que: a seção 6 pede um texto chamado campo_<nome> nos dois idiomas, e um nome longo o
# bastante para empurrar essa chave além do NOME_MAXIMO pedia um texto que arquivo nenhum
# escrevia, então o campo nunca era validado e a recusa nomeava manifesto.textos, não o campo.
CAMPO_NOME_MAXIMO = NOME_MAXIMO - len(PREFIXO_TEXTO_CAMPO)
PASSOS_MAXIMOS = 8
VALORES_MAXIMOS = 64
REPETE_MAXIMO = 20
INTERVALO_MAXIMO_MS = 10_000
TIMEOUT_MINIMO_S = 0.5
TIMEOUT_MAXIMO_S = 30.0
TIMEOUT_PADRAO_S = 3.0
PORTA_MINIMA = 1
PORTA_MAXIMA = 65535
TERMINADOR_MAXIMO = 4
ESCALA_LIMITE = 10_000
ASSINATURAS_MAXIMAS = 16

TERMINADOR_PADRAO = "\r"

_TIPO = re.compile(rf"[a-z0-9_]{{1,{TIPO_MAXIMO}}}")
# Why: the base is written with the placeholder and nothing else, so a driver received ready
# made cannot send the internal address of the customer to a host of its own on every poll.
# Por que: a base é escrita com o marcador e nada mais, então um driver recebido pronto não
# pode mandar o endereço interno do cliente para um host próprio a cada poll.
_BASE = re.compile(r"https?://\{ip\}(?::(\d{1,5}))?/?")
_CABECALHO = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$%&'*+.^_`|~-]{0,63}")
_CAMINHO_JSON = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
# Why: a lone surrogate is the one thing a str holds that utf-8 cannot write, and the file is
# saved as the integrator typed it, so a text carrying one is a bad file and not an internal
# error; accepting it made the save answer 500 after the validate route had said yes.
# Por que: um surrogado solto é a única coisa que um str guarda e o utf-8 não escreve, e o
# arquivo é gravado como o integrador digitou, então um texto que leva um é arquivo ruim e não
# erro interno; aceitá-lo fazia a gravação responder 500 depois de a rota de validar dizer sim.
_SURROGADO = re.compile(r"[\ud800-\udfff]")

# The prefix of a section 6 problem, mapped to the code the panel already translates.
# O prefixo de um problema da seção 6, mapeado ao código que o painel já traduz.
CODIGO_DA_SECAO_6 = {
    "tipo": DECL_TIPO_INVALIDO,
    "categoria": DECL_CATEGORIA_INVALIDA,
    "motor": DECL_MANIFESTO_INVALIDO,
    "auth": DECL_AUTH_INVALIDA,
    "descoberta": DECL_DESCOBERTA_INVALIDA,
    "ssdp_st": DECL_DESCOBERTA_INVALIDA,
    "ssdp_fabricantes": DECL_DESCOBERTA_INVALIDA,
    "mdns_servicos": DECL_DESCOBERTA_INVALIDA,
    "capacidades": DECL_CAPACIDADE_DESCONHECIDA,
    "teclas": DECL_VOCABULARIO_INVALIDO,
    "modos": DECL_VOCABULARIO_INVALIDO,
    "ventos": DECL_VOCABULARIO_INVALIDO,
    "rotulo": DECL_ROTULO_INVALIDO,
    "config_campos": DECL_CONFIG_CAMPO_INVALIDO,
    "textos": DECL_TEXTOS_INVALIDOS,
}


class ProvaDeFogo(Protocol):
    """The fire test of section 7: True refuses the pattern, at save time and never in a poll.

    A prova de fogo da seção 7: True recusa o padrão, na hora de salvar e nunca num poll.
    """

    def perigosa(self, padrao: str) -> bool: ...


@dataclass(frozen=True)
class Cabecalho:
    """A header whose VALUE is the registration field named campo, so no file carries a secret.

    Um cabeçalho cujo VALOR é o campo de cadastro chamado campo, para nenhum arquivo levar
    segredo.
    """

    nome: str
    campo: str


@dataclass(frozen=True)
class Tcp:
    """saudacao tolerates a greeting line before the first answer, the PJLink shape.

    saudacao tolera uma linha de saudação antes da primeira resposta, o formato do PJLink.
    """

    porta: int
    terminador: str = TERMINADOR_PADRAO
    timeout_s: float = TIMEOUT_PADRAO_S
    intervalo_min_ms: int = 0
    saudacao: bool = False


@dataclass(frozen=True)
class Http:
    base: str
    metodo: str = "GET"
    timeout_s: float = TIMEOUT_PADRAO_S
    cabecalhos: tuple[Cabecalho, ...] = ()


@dataclass(frozen=True)
class Udp:
    porta: int
    terminador: str = ""
    timeout_s: float = TIMEOUT_PADRAO_S
    intervalo_min_ms: int = 0


type Transporte = Tcp | Http | Udp


@dataclass(frozen=True)
class Passo:
    """One thing on the wire: a line for tcp and udp, one request for http.

    envia is the line, or the path when the transport is http. hex says the line is a
    hexadecimal literal, which carries no substitution because it is bytes, not text.

    Uma coisa no fio: uma linha para tcp e udp, uma requisição para http.

    envia é a linha, ou o caminho quando o transporte é http. hex diz que a linha é um
    literal hexadecimal, que não leva substituição porque é byte, não texto.
    """

    envia: str
    hex: bool = False
    metodo: str = ""
    corpo: str = ""


@dataclass(frozen=True)
class Comando:
    """What one capability sends: the steps, the map of values and the declared repetition.

    O que uma capacidade envia: os passos, o mapa de valores e a repetição declarada.
    """

    passos: tuple[Passo, ...]
    valores: dict[str, str] = field(default_factory=dict)
    repete: int = 1
    intervalo_ms: int = 0


@dataclass(frozen=True)
class Leitura:
    """One field of Estado read from an answer, by regex with a capture group or by JSON path.

    Um campo do Estado lido de uma resposta, por regex com grupo de captura ou caminho JSON.
    """

    campo: str
    regex: str = ""
    caminho: str = ""
    verdadeiro: str = ""


@dataclass(frozen=True)
class Consulta:
    """The estado block: what to ask and how to read it back.

    Section 7 asks for state in more than one request, so pede is a sequence. A reading is
    tried against each answer in order and the first one that yields a value wins, which is
    a rule of the engine and not a condition written in the file.

    O bloco estado: o que perguntar e como ler de volta.

    A seção 7 pede estado em mais de uma requisição, então pede é uma sequência. Uma leitura
    é tentada em cada resposta na ordem e a primeira que render um valor vence, que é regra
    do motor e não condicional escrita no arquivo.
    """

    pede: tuple[Passo, ...]
    le: tuple[Leitura, ...] = ()


@dataclass(frozen=True)
class Escala:
    """The real range of the device; section 6 fixes the contract at 0 to 100.

    A faixa real do aparelho; a seção 6 fixa o contrato em 0 a 100.
    """

    minimo: int
    maximo: int


@dataclass(frozen=True)
class Definicao:
    """A validated declaration: a manifest of section 6 and the data the engine speaks.

    Uma declaração validada: um manifesto da seção 6 e o dado que o motor fala.
    """

    manifesto: Manifesto
    transporte: Transporte
    comandos: dict[str, Comando] = field(default_factory=dict)
    estado: Consulta | None = None
    escala: Escala | None = None

    @property
    def fontes(self) -> tuple[str, ...]:
        """The sources the panel offers, in the order the file wrote them.

        As fontes que o painel oferece, na ordem em que o arquivo as escreveu.
        """
        comando = self.comandos.get("fonte")
        return tuple(comando.valores) if comando is not None else ()


class DeclaracaoInvalida(ValueError):
    """Carries every problem as (campo, codigo), so the panel fixes the file in one pass.

    Carrega todo problema como (campo, codigo), para o painel consertar o arquivo numa passada.
    """

    def __init__(self, problemas: tuple[tuple[str, str], ...]) -> None:
        self.problemas = problemas
        super().__init__("; ".join(f"{campo}: {codigo}" for campo, codigo in problemas))


def validar(dados: object, *, regex: ProvaDeFogo) -> Definicao:
    """The declaration as typed data, or DeclaracaoInvalida listing EVERY problem at once.

    Nothing but DeclaracaoInvalida leaves here, for any input at all: the loader calls this
    on a file an integrator wrote by hand, and a validation that raised anything else took a
    whole appliance down in a restart loop.

    A declaração como dado tipado, ou DeclaracaoInvalida listando TODO problema de uma vez.

    Nada além de DeclaracaoInvalida sai daqui, para entrada nenhuma: o carregador chama isto
    sobre um arquivo que um integrador escreveu à mão, e uma validação que estourava outra
    coisa derrubava um appliance inteiro em laço de reinício.
    """
    leitor = _Leitor(regex)
    definicao = leitor.definicao(dados)
    if definicao is None or leitor.problemas:
        raise DeclaracaoInvalida(tuple(leitor.problemas) or ((CAMPO_ARQUIVO, DECL_NAO_OBJETO),))
    return definicao


class _Leitor:
    """Reads the declaration and records every problem instead of stopping at the first.

    Lê a declaração e registra todo problema em vez de parar no primeiro.
    """

    def __init__(self, regex: ProvaDeFogo) -> None:
        self._regex = regex
        self.problemas: list[tuple[str, str]] = []

    def definicao(self, dados: object) -> Definicao | None:
        if not isinstance(dados, dict):
            self._erro(CAMPO_ARQUIVO, DECL_NAO_OBJETO)
            return None
        self._chaves(CAMPO_ARQUIVO, dados, CHAVES_ARQUIVO)
        descoberta = self._descoberta(dados.get("descoberta"))
        manifesto = self._manifesto(dados.get("manifesto"), descoberta)
        transporte = self._transporte(dados.get("transporte"), _campos_de(manifesto))
        # Why: with a broken manifest the declared capabilities are unknown, and cross
        # checking against a guess would answer one mistake with a problem per command.
        # Por que: com o manifesto quebrado as capacidades declaradas são desconhecidas, e
        # conferir contra um palpite responderia um erro com um problema por comando.
        capacidades = manifesto.capacidades if manifesto is not None else None
        comandos = self._comandos(dados.get("comandos"), capacidades, transporte)
        estado = self._estado(dados.get("estado"), transporte)
        escala = self._escala(dados.get("escala_volume"))
        if manifesto is None or transporte is None or comandos is None:
            return None
        return Definicao(
            manifesto=manifesto,
            transporte=transporte,
            comandos=comandos,
            estado=estado,
            escala=escala,
        )

    # ---------- manifest, section 6 ----------

    def _manifesto(self, bruto: object, descoberta: Descoberta | None) -> Manifesto | None:
        if not isinstance(bruto, dict):
            self._erro("manifesto", DECL_MANIFESTO_INVALIDO)
            return None
        self._chaves("manifesto", bruto, CHAVES_MANIFESTO)
        antes = len(self.problemas)
        tipo = self._tipo(bruto.get("tipo"))
        rotulo = self._rotulo(bruto.get("rotulo"))
        categoria = self._categoria(bruto.get("categoria"))
        capacidades = self._capacidades(bruto.get("capacidades"))
        auth = self._auth(bruto.get("auth"))
        config_campos = self._config_campos(bruto.get("config_campos"))
        textos = self._textos(bruto.get("textos"), rotulo)
        vocabularios = [self._vocabulario(bruto.get(campo), campo) for campo in VOCABULARIOS]
        partes = (tipo, rotulo, categoria, capacidades, auth, config_campos, textos, *vocabularios)
        if any(parte is None for parte in partes) or descoberta is None:
            return None
        teclas, modos, ventos = vocabularios
        # Why: motor is never read from the file, so a declaration cannot claim to be code
        # that shipped in the image; rule 3 of section 2 says nothing loads code at runtime.
        # Por que: o motor nunca é lido do arquivo, então uma declaração não pode se dizer
        # código que embarcou na imagem; a regra 3 da seção 2 diz que nada carrega código em
        # runtime.
        manifesto = Manifesto(
            tipo=tipo,
            rotulo=rotulo,
            categoria=categoria,
            capacidades=capacidades,
            auth=auth,
            descoberta=descoberta,
            config_campos=config_campos,
            textos=textos,
            motor=MOTOR,
            teclas=teclas,
            modos=modos,
            ventos=ventos,
        )
        if len(self.problemas) == antes:
            self._secao6(manifesto)
        return manifesto

    def _secao6(self, manifesto: Manifesto) -> None:
        """Section 6 judges the manifest, so no rule of the contract is written twice here.

        A seção 6 julga o manifesto, para nenhuma regra do contrato ser escrita duas vezes aqui.
        """
        try:
            validar_manifesto(manifesto)
        except ManifestoInvalido as erro:
            for problema in erro.problemas:
                prefixo = problema.split(":")[0]
                onde = "descoberta" if prefixo in CHAVES_DESCOBERTA else "manifesto"
                codigo = CODIGO_DA_SECAO_6.get(prefixo, DECL_MANIFESTO_INVALIDO)
                self._erro(f"{onde}.{prefixo}", codigo)

    def _tipo(self, bruto: object) -> str | None:
        # Why: the tipo is the name of the file under the drivers directory, so an alphabet
        # carrying a separator or a dot would let a save escape that directory.
        # Por que: o tipo é o nome do arquivo na pasta de drivers, então um alfabeto levando
        # separador ou ponto deixaria um salvamento escapar daquela pasta.
        if not isinstance(bruto, str) or not _TIPO.fullmatch(bruto):
            self._erro("manifesto.tipo", DECL_TIPO_INVALIDO)
            return None
        return bruto

    def _rotulo(self, bruto: object) -> dict[str, str] | None:
        # Why: the keys are compared raw because a key sanitised for display is not the key the
        # file carries, and reading the file by the sanitised name raised KeyError out of the
        # validation, which the loader calls at boot: one hand written file, no daemon.
        # Por que: as chaves são comparadas cruas porque uma chave higienizada para exibição não
        # é a chave que o arquivo tem, e ler o arquivo pelo nome higienizado estourava KeyError
        # para fora da validação, que o carregador chama no boot: um arquivo à mão, sem daemon.
        if not isinstance(bruto, dict) or set(bruto) != set(IDIOMAS):
            self._erro("manifesto.rotulo", DECL_ROTULO_INVALIDO)
            return None
        rotulo = {}
        for idioma in IDIOMAS:
            onde = f"manifesto.rotulo.{idioma}"
            texto = self._texto(bruto[idioma], onde, DECL_ROTULO_INVALIDO)
            if texto is None:
                return None
            rotulo[idioma] = texto
        return rotulo

    def _categoria(self, bruto: object) -> str | None:
        if bruto not in CATEGORIAS:
            self._erro("manifesto.categoria", DECL_CATEGORIA_INVALIDA)
            return None
        return str(bruto)

    def _capacidades(self, bruto: object) -> tuple[str, ...] | None:
        # Why: a capability written as the string "ligar" iterates as its letters, and the
        # panel would show five controls that no device has.
        # Por que: uma capacidade escrita como a string "ligar" itera como as letras dela, e
        # o painel mostraria cinco controles que nenhum aparelho tem.
        if bruto is None:
            return ()
        if not isinstance(bruto, list) or any(item not in CAPACIDADES for item in bruto):
            self._erro("manifesto.capacidades", DECL_CAPACIDADE_DESCONHECIDA)
            return None
        return tuple(bruto)

    def _vocabulario(self, bruto: object, campo: str) -> tuple[str, ...] | None:
        """A list of words, whose membership in the vocabulary section 6 judges.

        Uma lista de palavras, cuja pertinência ao vocabulário a seção 6 julga.
        """
        if bruto is None:
            return ()
        if not isinstance(bruto, list) or any(not isinstance(item, str) for item in bruto):
            self._erro(f"manifesto.{campo}", DECL_VOCABULARIO_INVALIDO)
            return None
        return tuple(bruto)

    def _auth(self, bruto: object) -> Auth | None:
        if bruto is None:
            return Auth.NENHUMA
        try:
            return Auth(bruto)
        except ValueError:
            self._erro("manifesto.auth", DECL_AUTH_INVALIDA)
            return None

    def _config_campos(self, bruto: object) -> tuple[Campo, ...] | None:
        if bruto is None:
            return ()
        if not isinstance(bruto, list):
            self._erro("manifesto.config_campos", DECL_CONFIG_CAMPO_INVALIDO)
            return None
        campos = [
            self._campo(item, f"manifesto.config_campos[{indice}]")
            for indice, item in enumerate(bruto)
        ]
        if any(campo is None for campo in campos):
            return None
        return tuple(campos)

    def _campo(self, bruto: object, onde: str) -> Campo | None:
        if not isinstance(bruto, dict):
            self._erro(onde, DECL_CONFIG_CAMPO_INVALIDO)
            return None
        self._chaves(onde, bruto, CHAVES_CAMPO)
        nome = self._texto(bruto.get("nome"), f"{onde}.nome", DECL_CONFIG_CAMPO_INVALIDO)
        tipo = bruto.get("tipo", TipoCampo.TEXTO.value)
        obrigatorio = bruto.get("obrigatorio", False)
        padrao = self._texto(
            bruto.get("padrao", ""), f"{onde}.padrao", DECL_CONFIG_CAMPO_INVALIDO, exigido=False
        )
        if tipo not in tuple(TipoCampo):
            self._erro(f"{onde}.tipo", DECL_CONFIG_CAMPO_INVALIDO)
            return None
        if type(obrigatorio) is not bool:
            self._erro(f"{onde}.obrigatorio", DECL_CONFIG_CAMPO_INVALIDO)
            return None
        if nome is not None and len(nome) > CAMPO_NOME_MAXIMO:
            self._erro(f"{onde}.nome", DECL_CONFIG_CAMPO_INVALIDO)
            return None
        if nome is None or padrao is None:
            return None
        return Campo(nome=nome, tipo=TipoCampo(tipo), obrigatorio=obrigatorio, padrao=padrao)

    def _textos(self, bruto: object, rotulo: dict[str, str] | None) -> dict[str, dict] | None:
        onde = "manifesto.textos"
        textos: dict[str, dict[str, str]] = {idioma: {} for idioma in IDIOMAS}
        if bruto is not None and not self._encher_textos(bruto, textos, onde):
            return None
        # Why: section 6 demands a descricao in both languages and section 7 shows a file with
        # no textos block at all, so the label answers for the description when it is absent;
        # a field or an auth still has to be described, and section 6 is what says so.
        # Por que: a seção 6 exige uma descricao nos dois idiomas e a seção 7 mostra um arquivo
        # sem bloco textos nenhum, então o rótulo responde pela descrição quando ela falta; um
        # campo ou uma auth ainda precisam ser descritos, e quem diz isso é a seção 6.
        if rotulo is not None:
            for idioma in IDIOMAS:
                textos[idioma].setdefault("descricao", rotulo[idioma])
        return textos

    def _encher_textos(self, bruto: object, textos: dict[str, dict], onde: str) -> bool:
        # The keys are compared raw for the reason written over _rotulo.
        # As chaves são comparadas cruas pelo motivo escrito sobre o _rotulo.
        if not isinstance(bruto, dict) or set(bruto) != set(IDIOMAS):
            self._erro(onde, DECL_TEXTOS_INVALIDOS)
            return False
        inteiro = True
        for idioma in IDIOMAS:
            grupo = bruto[idioma]
            if not isinstance(grupo, dict):
                self._erro(f"{onde}.{idioma}", DECL_TEXTOS_INVALIDOS)
                inteiro = False
                continue
            for chave, valor in grupo.items():
                lugar = f"{onde}.{idioma}.{_nome(chave)}"
                texto = self._texto(valor, lugar, DECL_TEXTOS_INVALIDOS)
                if texto is None:
                    inteiro = False
                    continue
                textos[idioma][_nome(chave)] = texto
        return inteiro

    def _descoberta(self, bruto: object) -> Descoberta | None:
        if bruto is None:
            return Descoberta()
        if not isinstance(bruto, dict):
            self._erro("descoberta", DECL_DESCOBERTA_INVALIDA)
            return None
        self._chaves("descoberta", bruto, CHAVES_DESCOBERTA)
        assinaturas = {}
        inteiro = True
        for nome in CHAVES_DESCOBERTA:
            valores = bruto.get(nome, [])
            if not isinstance(valores, list) or len(valores) > ASSINATURAS_MAXIMAS:
                self._erro(f"descoberta.{nome}", DECL_DESCOBERTA_INVALIDA)
                inteiro = False
                continue
            limpos = [
                self._texto(valor, f"descoberta.{nome}[{indice}]", DECL_DESCOBERTA_INVALIDA)
                for indice, valor in enumerate(valores)
            ]
            if any(texto is None for texto in limpos):
                inteiro = False
                continue
            assinaturas[nome] = tuple(limpos)
        return Descoberta(**assinaturas) if inteiro else None

    # ---------- transport ----------

    def _transporte(self, bruto: object, campos: frozenset[str]) -> Transporte | None:
        if not isinstance(bruto, dict):
            self._erro("transporte", DECL_TRANSPORTE_INVALIDO)
            return None
        self._chaves("transporte", bruto, TRANSPORTES)
        presentes = [nome for nome in TRANSPORTES if nome in bruto]
        # Why: two transports in one file would make the engine choose in runtime, and the
        # format has no condition in it; one file describes one way of talking.
        # Por que: dois transportes num arquivo fariam o motor escolher em runtime, e o
        # formato não tem condicional; um arquivo descreve um jeito de falar.
        if len(presentes) != 1:
            self._erro("transporte", DECL_TRANSPORTE_INVALIDO)
            return None
        nome = presentes[0]
        dados = bruto[nome]
        if not isinstance(dados, dict):
            self._erro(f"transporte.{nome}", DECL_TRANSPORTE_INVALIDO)
            return None
        if nome == "http":
            return self._http(dados, campos)
        return self._linha(nome, dados)

    def _linha(self, nome: str, dados: dict) -> Tcp | Udp | None:
        onde = f"transporte.{nome}"
        self._chaves(onde, dados, CHAVES_TCP if nome == "tcp" else CHAVES_UDP)
        porta = self._inteiro(
            dados.get("porta"), f"{onde}.porta", DECL_PORTA_INVALIDA, PORTA_MINIMA, PORTA_MAXIMA
        )
        padrao = TERMINADOR_PADRAO if nome == "tcp" else ""
        terminador = self._terminador(dados.get("terminador", padrao), f"{onde}.terminador")
        timeout_s = self._timeout(dados.get("timeout_s", TIMEOUT_PADRAO_S), f"{onde}.timeout_s")
        intervalo = self._inteiro(
            dados.get("intervalo_min_ms", 0),
            f"{onde}.intervalo_min_ms",
            DECL_INTERVALO_INVALIDO,
            0,
            INTERVALO_MAXIMO_MS,
        )
        saudacao = dados.get("saudacao", False)
        if type(saudacao) is not bool:
            self._erro(f"{onde}.saudacao", DECL_TRANSPORTE_INVALIDO)
            return None
        if porta is None or terminador is None or timeout_s is None or intervalo is None:
            return None
        if nome == "udp":
            return Udp(
                porta=porta, terminador=terminador, timeout_s=timeout_s, intervalo_min_ms=intervalo
            )
        return Tcp(
            porta=porta,
            terminador=terminador,
            timeout_s=timeout_s,
            intervalo_min_ms=intervalo,
            saudacao=saudacao,
        )

    def _http(self, dados: dict, campos: frozenset[str]) -> Http | None:
        onde = "transporte.http"
        self._chaves(onde, dados, CHAVES_HTTP)
        base = self._base(dados.get("base"))
        metodo = self._metodo(dados.get("metodo", "GET"), f"{onde}.metodo")
        timeout_s = self._timeout(dados.get("timeout_s", TIMEOUT_PADRAO_S), f"{onde}.timeout_s")
        cabecalhos = self._cabecalhos(dados.get("cabecalhos"), campos)
        if base is None or metodo is None or timeout_s is None or cabecalhos is None:
            return None
        return Http(base=base, metodo=metodo, timeout_s=timeout_s, cabecalhos=cabecalhos)

    def _base(self, bruto: object) -> str | None:
        onde = "transporte.http.base"
        casou = _BASE.fullmatch(bruto) if isinstance(bruto, str) else None
        if casou is None:
            self._erro(onde, DECL_BASE_INVALIDA)
            return None
        porta = casou.group(1)
        if porta is not None and not PORTA_MINIMA <= int(porta) <= PORTA_MAXIMA:
            self._erro(onde, DECL_BASE_INVALIDA)
            return None
        return str(bruto)

    def _cabecalhos(self, bruto: object, campos: frozenset[str]) -> tuple[Cabecalho, ...] | None:
        onde = "transporte.http.cabecalhos"
        if bruto is None:
            return ()
        if not isinstance(bruto, dict):
            self._erro(onde, DECL_CABECALHO_INVALIDO)
            return None
        cabecalhos = []
        inteiro = True
        for nome, campo in bruto.items():
            # Why: the value names a registration field and is never the value itself, so a
            # JSON shared between installations can never carry a token or a password.
            # Por que: o valor nomeia um campo de cadastro e nunca é o valor em si, então um
            # JSON compartilhado entre instalações nunca pode levar token ou senha.
            certo = (
                isinstance(nome, str)
                and _CABECALHO.fullmatch(nome)
                and isinstance(campo, str)
                and campo in campos
            )
            if not certo:
                self._erro(f"{onde}.{_nome(nome)}", DECL_CABECALHO_INVALIDO)
                inteiro = False
                continue
            cabecalhos.append(Cabecalho(nome=str(nome), campo=str(campo)))
        return tuple(cabecalhos) if inteiro else None

    # ---------- commands ----------

    def _comandos(
        self, bruto: object, capacidades: tuple[str, ...] | None, transporte: Transporte | None
    ) -> dict[str, Comando] | None:
        if bruto is None:
            bruto = {}
        if not isinstance(bruto, dict):
            self._erro("comandos", DECL_COMANDO_INVALIDO)
            return None
        inteiro = True
        if capacidades is not None:
            for acao in bruto:
                if not isinstance(acao, str) or acao not in capacidades:
                    # Why: a command for an action the manifest does not declare is
                    # unreachable, because the gestor refuses the action before the driver
                    # ever sees it.
                    # Por que: um comando para uma ação que o manifesto não declara é
                    # inalcançável, porque o gestor recusa a ação antes de o driver sequer ver.
                    self._erro(f"comandos.{_nome(acao)}", DECL_CAPACIDADE_DESCONHECIDA)
                    inteiro = False
        comandos = {}
        esperadas = capacidades if capacidades is not None else _acoes_de(bruto)
        for acao in esperadas:
            if acao not in bruto:
                self._erro(f"comandos.{acao}", DECL_COMANDO_VAZIO)
                inteiro = False
                continue
            comando = self._comando(bruto[acao], f"comandos.{acao}", transporte)
            if comando is None:
                inteiro = False
                continue
            comandos[acao] = comando
        return comandos if inteiro else None

    def _comando(self, bruto: object, onde: str, transporte: Transporte | None) -> Comando | None:
        if not isinstance(bruto, dict):
            self._erro(onde, DECL_COMANDO_INVALIDO)
            return None
        self._chaves(onde, bruto, CHAVES_COMANDO + _chaves_de_passo(transporte))
        passos = self._passos(bruto, onde, transporte)
        valores = self._valores(bruto.get("valores"), f"{onde}.valores")
        repete = self._inteiro(
            bruto.get("repete", 1), f"{onde}.repete", DECL_REPETE_INVALIDO, 1, REPETE_MAXIMO
        )
        intervalo = self._inteiro(
            bruto.get("intervalo_ms", 0),
            f"{onde}.intervalo_ms",
            DECL_INTERVALO_INVALIDO,
            0,
            INTERVALO_MAXIMO_MS,
        )
        if passos is None or valores is None or repete is None or intervalo is None:
            return None
        return Comando(passos=passos, valores=valores, repete=repete, intervalo_ms=intervalo)

    def _passos(
        self, bruto: dict, onde: str, transporte: Transporte | None
    ) -> tuple[Passo, ...] | None:
        sequencia = bruto.get("sequencia")
        tem_envia = "envia" in bruto
        if sequencia is None:
            if not tem_envia:
                self._erro(onde, DECL_COMANDO_VAZIO)
                return None
            passo = self._passo(bruto, onde, transporte)
            return None if passo is None else (passo,)
        # Why: a file writing both would leave the engine choosing which one to send.
        # Por que: um arquivo escrevendo os dois deixaria o motor escolhendo qual mandar.
        if tem_envia:
            self._erro(onde, DECL_COMANDO_INVALIDO)
            return None
        return self._sequencia(sequencia, f"{onde}.sequencia", transporte)

    def _sequencia(
        self, bruto: object, onde: str, transporte: Transporte | None
    ) -> tuple[Passo, ...] | None:
        if not isinstance(bruto, list) or not bruto:
            self._erro(onde, DECL_COMANDO_VAZIO)
            return None
        if len(bruto) > PASSOS_MAXIMOS:
            self._erro(onde, DECL_COMANDO_INVALIDO)
            return None
        passos = []
        inteiro = True
        for indice, item in enumerate(bruto):
            lugar = f"{onde}[{indice}]"
            if not isinstance(item, dict):
                self._erro(lugar, DECL_COMANDO_INVALIDO)
                inteiro = False
                continue
            self._chaves(lugar, item, _chaves_de_passo(transporte))
            passo = self._passo(item, lugar, transporte)
            if passo is None:
                inteiro = False
                continue
            passos.append(passo)
        return tuple(passos) if inteiro else None

    def _passo(self, bruto: dict, onde: str, transporte: Transporte | None) -> Passo | None:
        envia = self._texto(bruto.get("envia"), f"{onde}.envia", DECL_COMANDO_VAZIO)
        if envia is None:
            return None
        if isinstance(transporte, Http):
            return self._passo_http(bruto, onde, envia, transporte)
        return self._passo_linha(bruto, onde, envia)

    def _passo_linha(self, bruto: dict, onde: str, envia: str) -> Passo | None:
        hexadecimal = bruto.get("hex", False)
        if type(hexadecimal) is not bool:
            self._erro(f"{onde}.hex", DECL_HEX_INVALIDO)
            return None
        if not hexadecimal:
            return Passo(envia=envia)
        # Why: a hexadecimal literal is bytes, so half a byte on the wire is a command the
        # device cannot answer, and the substitutions do not apply to it either. The proof is
        # the very call the transport makes, because a literal that only LOOKS hexadecimal
        # (a space inside a byte pair) was accepted here and then failed on every command,
        # as erro_aparelho, far from the field that caused it.
        # Por que: um literal hexadecimal é byte, então meio byte no fio é um comando que o
        # aparelho não sabe responder, e as substituições também não valem para ele. A prova é
        # a própria chamada que o transporte faz, porque um literal que só PARECE hexadecimal
        # (um espaço dentro de um par de bytes) era aceito aqui e depois falhava em todo
        # comando, como erro_aparelho, longe do campo que o causou.
        try:
            bytes.fromhex(envia)
        except ValueError:
            self._erro(f"{onde}.envia", DECL_HEX_INVALIDO)
            return None
        return Passo(envia=envia, hex=True)

    def _passo_http(self, bruto: dict, onde: str, envia: str, transporte: Http) -> Passo | None:
        # Why: a path that does not start with a slash lands on the root of the device, which
        # answers its home page, so the driver reads online forever without reading a thing.
        # Por que: um caminho que não começa com barra cai na raiz do aparelho, que responde a
        # página inicial, então o driver lê online para sempre sem ler coisa alguma.
        if not envia.startswith("/"):
            self._erro(f"{onde}.envia", DECL_COMANDO_INVALIDO)
            return None
        metodo = self._metodo(bruto.get("metodo", transporte.metodo), f"{onde}.metodo")
        corpo = self._texto(
            bruto.get("corpo", ""), f"{onde}.corpo", DECL_COMANDO_INVALIDO, exigido=False
        )
        if metodo is None or corpo is None:
            return None
        return Passo(envia=envia, metodo=metodo, corpo=corpo)

    def _valores(self, bruto: object, onde: str) -> dict[str, str] | None:
        if bruto is None:
            return {}
        # Why: a list of sources instead of a map passed validation and only exploded when the
        # driver was instantiated, and the equipment vanished from the panel with no error.
        # Por que: uma lista de fontes em vez de um mapa passava na validação e só explodia na
        # instanciação, e o equipamento sumia do painel sem erro nenhum.
        if not isinstance(bruto, dict) or not bruto or len(bruto) > VALORES_MAXIMOS:
            self._erro(onde, DECL_VALORES_INVALIDO)
            return None
        valores = {}
        inteiro = True
        for chave, valor in bruto.items():
            lugar = f"{onde}.{_nome(chave)}"
            rotulo = self._texto(chave, lugar, DECL_VALORES_INVALIDO)
            fio = self._texto(valor, lugar, DECL_VALORES_INVALIDO)
            if rotulo is None or fio is None:
                inteiro = False
                continue
            valores[rotulo] = fio
        return valores if inteiro else None

    # ---------- state ----------

    def _estado(self, bruto: object, transporte: Transporte | None) -> Consulta | None:
        if bruto is None:
            return None
        if not isinstance(bruto, dict):
            self._erro("estado", DECL_ESTADO_INVALIDO)
            return None
        self._chaves("estado", bruto, CHAVES_ESTADO)
        pede = self._pede(bruto.get("pede"), transporte)
        le = self._le(bruto.get("le"))
        if pede is None or le is None:
            return None
        return Consulta(pede=pede, le=le)

    def _pede(self, bruto: object, transporte: Transporte | None) -> tuple[Passo, ...] | None:
        onde = "estado.pede"
        if bruto is None:
            self._erro(onde, DECL_ESTADO_INVALIDO)
            return None
        if isinstance(bruto, str):
            bruto = [{"envia": bruto}]
        elif isinstance(bruto, dict):
            bruto = [bruto]
        return self._sequencia(bruto, onde, transporte)

    def _le(self, bruto: object) -> tuple[Leitura, ...] | None:
        onde = "estado.le"
        if bruto is None:
            return ()
        # Why: a le that is not an object raised inside the validation, and the loader calls
        # the validation at boot, so one hand written file took the whole appliance down.
        # Por que: um le que não é objeto estourava dentro da validação, e o carregador chama
        # a validação no boot, então um arquivo escrito à mão derrubava o appliance inteiro.
        if not isinstance(bruto, dict):
            self._erro(onde, DECL_LEITURA_INVALIDA)
            return None
        leituras = [
            self._leitura(campo, dados, f"{onde}.{_nome(campo)}") for campo, dados in bruto.items()
        ]
        if any(leitura is None for leitura in leituras):
            return None
        return tuple(leituras)

    def _leitura(self, campo: object, bruto: object, onde: str) -> Leitura | None:
        if campo not in LEITURAS:
            self._erro(onde, DECL_CAMPO_DESCONHECIDO)
            return None
        if not isinstance(bruto, dict):
            self._erro(onde, DECL_LEITURA_INVALIDA)
            return None
        self._chaves(onde, bruto, CHAVES_LEITURA)
        padrao = bruto.get("regex")
        caminho = bruto.get("json")
        if padrao is not None and caminho is not None:
            self._erro(onde, DECL_LEITURA_INVALIDA)
            return None
        if padrao is None and caminho is None:
            # Why: a reading switched on and empty was ignored in silence, and the integrator
            # ticked four fields and saw none of them on the card.
            # Por que: uma leitura ligada e vazia era ignorada em silêncio, e o integrador
            # marcava quatro campos e não via nenhum no cartão.
            self._erro(onde, DECL_LEITURA_VAZIA)
            return None
        verdadeiro = self._verdadeiro(str(campo), bruto.get("verdadeiro"), onde)
        if verdadeiro is None:
            return None
        if caminho is not None:
            if not isinstance(caminho, str) or not _CAMINHO_JSON.fullmatch(caminho):
                self._erro(f"{onde}.json", DECL_LEITURA_INVALIDA)
                return None
            return Leitura(campo=str(campo), caminho=caminho, verdadeiro=verdadeiro)
        if not self._regex_aceita(padrao, f"{onde}.regex"):
            return None
        return Leitura(campo=str(campo), regex=str(padrao), verdadeiro=verdadeiro)

    def _verdadeiro(self, campo: str, bruto: object, onde: str) -> str | None:
        # Why: without the word that means true, a boolean read off a device is a guess, and a
        # guess about power is a panel showing a projector off while the lamp is lit.
        # Por que: sem a palavra que significa verdadeiro, um booleano lido de um aparelho é
        # chute, e chute sobre energia é painel mostrando projetor apagado com a lâmpada acesa.
        if campo in BOOLEANAS:
            return self._texto(bruto, f"{onde}.verdadeiro", DECL_LEITURA_INVALIDA)
        if bruto is not None:
            self._erro(f"{onde}.verdadeiro", DECL_LEITURA_INVALIDA)
            return None
        return ""

    def _regex_aceita(self, padrao: object, onde: str) -> bool:
        if not isinstance(padrao, str) or _nao_gravavel(padrao):
            self._erro(onde, DECL_TEXTO_NAO_GRAVAVEL)
            return False
        try:
            compilada = re.compile(padrao)
        except (re.error, RecursionError, OverflowError):
            self._erro(onde, DECL_REGEX_INVALIDA)
            return False
        if compilada.groups < 1:
            # Why: with no group the read calls group(1), which raised, killed the poll and
            # left a device reading offline forever while it was powered on.
            # Por que: sem grupo a leitura chama group(1), que estourava, matava o poll e
            # deixava um aparelho lendo offline para sempre estando ligado.
            self._erro(onde, DECL_REGEX_SEM_GRUPO)
            return False
        try:
            perigosa = self._regex.perigosa(padrao)
        except Exception:
            # Why: the fire test runs the pattern in another process, and a fire test that
            # cannot answer is not a pattern proven safe; refusing here keeps a catastrophic
            # regex out of the poll, where it would freeze the daemon, the panel and the API.
            # Por que: a prova de fogo roda o padrão em outro processo, e uma prova que não
            # responde não é padrão provado seguro; recusar aqui mantém uma regex catastrófica
            # fora do poll, onde ela congelaria o daemon, o painel e a API.
            perigosa = True
        if perigosa:
            self._erro(onde, DECL_REGEX_PERIGOSA)
            return False
        return True

    def _escala(self, bruto: object) -> Escala | None:
        onde = "escala_volume"
        if bruto is None:
            return None
        if not isinstance(bruto, dict):
            self._erro(onde, DECL_ESCALA_INVALIDA)
            return None
        self._chaves(onde, bruto, CHAVES_ESCALA)
        minimo = bruto.get("min", 0)
        maximo = bruto.get("max", 0)
        # Why: a maximum at or below the minimum renders every volume as one number, and on
        # the bench that number was zero, so every volume command silenced the device.
        # Por que: um máximo igual ou abaixo do mínimo renderiza todo volume num número só, e
        # na bancada esse número era zero, então todo comando de volume calava o aparelho.
        certo = (
            type(minimo) is int
            and type(maximo) is int
            and minimo < maximo
            and abs(minimo) <= ESCALA_LIMITE
            and abs(maximo) <= ESCALA_LIMITE
        )
        if not certo:
            self._erro(onde, DECL_ESCALA_INVALIDA)
            return None
        return Escala(minimo=minimo, maximo=maximo)

    # ---------- shared readers ----------

    def _erro(self, campo: str, codigo: str) -> None:
        problema = (campo, codigo)
        if problema not in self.problemas:
            self.problemas.append(problema)

    def _chaves(self, onde: str, dados: dict, conhecidas: tuple[str, ...]) -> None:
        for chave in dados:
            if not isinstance(chave, str) or chave not in conhecidas:
                self._erro(f"{onde}.{_nome(chave)}", DECL_CHAVE_DESCONHECIDA)

    def _texto(self, bruto: object, onde: str, codigo: str, *, exigido: bool = True) -> str | None:
        if isinstance(bruto, str) and _nao_gravavel(bruto):
            self._erro(onde, DECL_TEXTO_NAO_GRAVAVEL)
            return None
        if not isinstance(bruto, str) or len(bruto) > TEXTO_MAXIMO:
            self._erro(onde, codigo)
            return None
        # Why: a source label copied from a manual with a carriage return in it became TWO
        # commands on the wire, so every text that can reach a device loses its control bytes.
        # Por que: um rótulo de fonte copiado do manual com um retorno de carro virava DOIS
        # comandos no fio, então todo texto que pode chegar a um aparelho perde os bytes de
        # controle.
        limpo = _CONTROLE.sub("", bruto)
        if exigido and not limpo.strip():
            self._erro(onde, codigo)
            return None
        return limpo

    def _inteiro(
        self, bruto: object, onde: str, codigo: str, minimo: int, maximo: int
    ) -> int | None:
        # Why: the true of JSON is an int for Python, and it is neither a port nor a count.
        # Por que: o true do JSON é int para o Python, e não é porta nem contagem.
        if type(bruto) is not int or not minimo <= bruto <= maximo:
            self._erro(onde, codigo)
            return None
        return bruto

    def _timeout(self, bruto: object, onde: str) -> float | None:
        if type(bruto) not in (int, float) or not TIMEOUT_MINIMO_S <= bruto <= TIMEOUT_MAXIMO_S:
            self._erro(onde, DECL_TIMEOUT_INVALIDO)
            return None
        return float(bruto)

    def _terminador(self, bruto: object, onde: str) -> str | None:
        # Why: the terminator is control bytes by definition, so it is the one text that keeps
        # them; what it must not be is long enough to be a command of its own.
        # Por que: o terminador é byte de controle por definição, então é o único texto que os
        # mantém; o que ele não pode é ser longo o bastante para ser um comando próprio.
        if not isinstance(bruto, str) or len(bruto) > TERMINADOR_MAXIMO or _nao_gravavel(bruto):
            self._erro(onde, DECL_TERMINADOR_INVALIDO)
            return None
        return bruto

    def _metodo(self, bruto: object, onde: str) -> str | None:
        if not isinstance(bruto, str) or bruto.upper() not in METODOS:
            self._erro(onde, DECL_METODO_INVALIDO)
            return None
        return bruto.upper()


def _acoes_de(bruto: dict) -> tuple[str, ...]:
    """The actions the file itself wrote, for when the manifest did not survive validation.

    As ações que o próprio arquivo escreveu, para quando o manifesto não sobreviveu à validação.
    """
    return tuple(acao for acao in bruto if isinstance(acao, str))


def _chaves_de_passo(transporte: Transporte | None) -> tuple[str, ...]:
    if transporte is None:
        return CHAVES_PASSO_LINHA + CHAVES_PASSO_HTTP
    return CHAVES_PASSO_HTTP if isinstance(transporte, Http) else CHAVES_PASSO_LINHA


def _campos_de(manifesto: Manifesto | None) -> frozenset[str]:
    if manifesto is None:
        return frozenset()
    return frozenset(campo.nome for campo in manifesto.config_campos)


def _nao_gravavel(texto: str) -> bool:
    """True when utf-8 cannot write the text, which is the only way a str fails to encode.

    True quando o utf-8 não escreve o texto, que é o único jeito de um str falhar ao codificar.
    """
    return _SURROGADO.search(texto) is not None


def _nome(chave: object) -> str:
    """A key as a field name: a hand written file may carry a key of any type or size.

    The surrogate goes out here instead of being refused, because this name travels inside the
    very refusal that names the key, and a refusal that cannot be written reaches nobody.

    Uma chave como nome de campo: um arquivo escrito à mão pode levar chave de qualquer tipo
    ou tamanho.

    O surrogado sai aqui em vez de ser recusado, porque este nome viaja dentro da própria
    recusa que nomeia a chave, e uma recusa que não pode ser escrita não chega a ninguém.
    """
    return _SURROGADO.sub("", _CONTROLE.sub("", str(chave)))[:NOME_MAXIMO]
