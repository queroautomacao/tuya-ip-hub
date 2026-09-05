# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 7 under attack: every guard of the format gets the file that defeated it before.

Each case of ATAQUES is a file shaped like the one that caused a real defect on the previous
project, and the expected set is exact: the guard reports what it must and nothing else, so
one mistake never answers with a page of problems.

Seção 7 sob ataque: toda guarda do formato ganha o arquivo que a derrotou antes.

Cada caso de ATAQUES é um arquivo com a forma do que causou um defeito real no projeto
anterior, e o conjunto esperado é exato: a guarda relata o que deve e nada mais, para um erro
nunca responder com uma página de problemas.
"""

import json
from dataclasses import FrozenInstanceError, fields

import pytest

from iphub.drivers.declarativo.formato import (
    CAMPO_NOME_MAXIMO,
    CODIGOS,
    LEITURAS,
    Cabecalho,
    Comando,
    Consulta,
    DeclaracaoInvalida,
    Definicao,
    Escala,
    Http,
    Leitura,
    Passo,
    Tcp,
    Udp,
    validar,
)
from iphub.drivers.manifesto import Auth, Estado, Manifesto, TipoCampo
from iphub.drivers.manifesto import validar as validar_manifesto

# The classic nested quantifier: the fire test of section 7 is what refuses it, and here the
# fire test is a double, so no test of this layer spawns a process.
# O quantificador aninhado clássico: a prova de fogo da seção 7 é quem o recusa, e aqui a
# prova de fogo é um dublê, então nenhum teste desta camada cria processo.
CATASTROFICA = r"(\s+)+!"


class Fogo:
    """The fire test, driven by hand: it records what it was asked and answers what the test wants.

    A prova de fogo, dirigida na mão: guarda o que perguntaram e responde o que o teste quer.
    """

    def __init__(self, *, perigosas: tuple[str, ...] = (CATASTROFICA,), estoura: bool = False):
        self.perguntou: list[str] = []
        self._perigosas = perigosas
        self._estoura = estoura

    def perigosa(self, padrao: str) -> bool:
        self.perguntou.append(padrao)
        if self._estoura:
            raise RuntimeError("the worker died")
        return padrao in self._perigosas


def _tcp(**mudancas) -> dict:
    """A video matrix over TCP, the shape of section 7.

    The example printed in section 7 is illustrative (its rotulo is literally "..."), so the
    file here is the same one completed: every declared capability carries its command.

    Uma matriz de video por TCP, a forma da seção 7.

    O exemplo impresso na seção 7 é ilustrativo (o rotulo dele é literalmente "..."), então o
    arquivo aqui é o mesmo completado: toda capacidade declarada carrega seu comando.
    """
    arquivo = {
        "manifesto": {
            "tipo": "matriz_exemplo",
            "rotulo": {"pt": "Matriz de video", "en": "Video matrix"},
            "categoria": "matriz",
            "capacidades": ["ligar", "desligar", "fonte", "comando_extra"],
        },
        "transporte": {
            "tcp": {"porta": 23, "terminador": "\r", "timeout_s": 3, "intervalo_min_ms": 200}
        },
        "comandos": {
            "ligar": {"envia": "PWR ON"},
            "desligar": {"envia": "PWR OFF"},
            "fonte": {"envia": "SRC {valor}", "valores": {"HDMI1": "1", "HDMI2": "2"}},
            "comando_extra": {"envia": "{valor}"},
        },
        "estado": {
            "pede": "STATUS?",
            "le": {
                "ligado": {"regex": "PWR (ON|OFF)", "verdadeiro": "ON"},
                "fonte": {"regex": r"SRC (\d)"},
            },
        },
        "descoberta": {"ssdp_fabricantes": ["exemplo"]},
    }
    return {**arquivo, **mudancas}


def _http(**mudancas) -> dict:
    """An amplifier over HTTP: a body with braces, a header from the registration, a scale.

    Um amplificador por HTTP: corpo com chaves, cabeçalho vindo do cadastro, uma escala.
    """
    arquivo = {
        "manifesto": {
            "tipo": "amplificador_http",
            "rotulo": {"pt": "Amplificador HTTP", "en": "HTTP amplifier"},
            "categoria": "audio",
            "capacidades": ["ligar", "desligar", "volume", "mudo"],
            "config_campos": [{"nome": "token", "tipo": "segredo", "obrigatorio": True}],
            "textos": {
                "pt": {"descricao": "Amplificador com API HTTP", "campo_token": "Token da API"},
                "en": {"descricao": "Amplifier with an HTTP API", "campo_token": "API token"},
            },
        },
        "transporte": {
            "http": {
                "base": "http://{ip}",
                "metodo": "GET",
                "timeout_s": 4,
                "cabecalhos": {"Authorization": "token"},
            }
        },
        "comandos": {
            "ligar": {"envia": "/api/power", "metodo": "POST", "corpo": '{"on": true}'},
            "desligar": {"envia": "/api/power", "metodo": "POST", "corpo": '{"on": false}'},
            "volume": {"envia": "/api/volume?v={valor_escala}"},
            "mudo": {"envia": "/api/mute?v={valor}", "valores": {"true": "1", "false": "0"}},
        },
        "estado": {
            "pede": [{"envia": "/api/status"}, {"envia": "/api/volume"}],
            "le": {
                "ligado": {"json": "power.state", "verdadeiro": "on"},
                "volume": {"json": "volume.value"},
                "mudo": {"json": "mute", "verdadeiro": "1"},
            },
        },
        "escala_volume": {"min": 0, "max": 79},
    }
    return {**arquivo, **mudancas}


def _udp(**mudancas) -> dict:
    """A screen controller over UDP, which is where the hexadecimal literal earns its place.

    Um controlador de tela por UDP, que é onde o literal hexadecimal se justifica.
    """
    arquivo = {
        "manifesto": {
            "tipo": "tela_udp",
            "rotulo": {"pt": "Tela de projecao", "en": "Projection screen"},
            "categoria": "outro",
            "capacidades": ["ligar", "desligar"],
        },
        "transporte": {"udp": {"porta": 5000, "timeout_s": 2}},
        "comandos": {
            "ligar": {"envia": "FF EE EE 01", "hex": True},
            "desligar": {"envia": "FFEEEE02", "hex": True},
        },
    }
    return {**arquivo, **mudancas}


def _com(base, bloco: str, **mudancas) -> dict:
    """One block of a file replaced key by key, so an attack changes only what it attacks.

    Um bloco do arquivo trocado chave a chave, para um ataque mudar só o que ele ataca.
    """
    arquivo = base()
    arquivo[bloco] = {**arquivo[bloco], **mudancas}
    return arquivo


def _problemas(dados, *, fogo: Fogo | None = None) -> set[tuple[str, str]]:
    with pytest.raises(DeclaracaoInvalida) as erro:
        validar(dados, regex=fogo or Fogo())
    return set(erro.value.problemas)


# Each entry: what the file looks like, and the EXACT problems it must answer with.
# Cada entrada: como o arquivo é, e os problemas EXATOS com que ele precisa responder.
ATAQUES = (
    ("arquivo que nao e objeto", "PWR ON", {("arquivo", "decl_nao_objeto")}),
    ("arquivo que e uma lista", [_tcp()], {("arquivo", "decl_nao_objeto")}),
    (
        "chave que ninguem le",
        _tcp(schema=1),
        {("arquivo.schema", "decl_chave_desconhecida")},
    ),
    (
        "motor escrito no arquivo",
        _com(_tcp, "manifesto", motor="nativo"),
        {("manifesto.motor", "decl_chave_desconhecida")},
    ),
    (
        "manifesto que nao e objeto",
        _tcp(manifesto=["matriz_exemplo"]),
        {("manifesto", "decl_manifesto_invalido")},
    ),
    (
        "tipo que sobe uma pasta",
        _com(_tcp, "manifesto", tipo="../../etc/senhas"),
        {("manifesto.tipo", "decl_tipo_invalido")},
    ),
    (
        "tipo com ponto, que ja e nome de arquivo",
        _com(_tcp, "manifesto", tipo="matriz.json"),
        {("manifesto.tipo", "decl_tipo_invalido")},
    ),
    (
        "tipo numerico, que nunca casa com a config",
        _com(_tcp, "manifesto", tipo=7),
        {("manifesto.tipo", "decl_tipo_invalido")},
    ),
    (
        "rotulo numerico onde o painel imprime texto",
        _com(_tcp, "manifesto", rotulo={"pt": 7, "en": "Video matrix"}),
        {("manifesto.rotulo.pt", "decl_rotulo_invalido")},
    ),
    (
        "rotulo num idioma so",
        _com(_tcp, "manifesto", rotulo={"pt": "Matriz de video"}),
        {("manifesto.rotulo", "decl_rotulo_invalido")},
    ),
    (
        "idioma do rotulo com caractere de controle, o KeyError que derrubava o boot",
        _com(_tcp, "manifesto", rotulo={"p\x01t": "Matriz de video", "en": "Video matrix"}),
        {("manifesto.rotulo", "decl_rotulo_invalido")},
    ),
    (
        "idioma dos textos com caractere de controle, o mesmo KeyError",
        _com(
            _tcp,
            "manifesto",
            textos={"p\x01t": {"descricao": "Matriz"}, "en": {"descricao": "Matrix"}},
        ),
        {("manifesto.textos", "decl_textos_invalidos")},
    ),
    (
        "rotulo que o utf-8 nao escreve",
        _com(_tcp, "manifesto", rotulo={"pt": "Matriz \ud800", "en": "Video matrix"}),
        {("manifesto.rotulo.pt", "decl_texto_nao_gravavel")},
    ),
    (
        "texto do painel que o utf-8 nao escreve",
        _com(
            _tcp,
            "manifesto",
            textos={"pt": {"descricao": "Matriz \ud800"}, "en": {"descricao": "Matrix"}},
        ),
        {("manifesto.textos.pt.descricao", "decl_texto_nao_gravavel")},
    ),
    (
        "nome de campo longo demais para o texto que a secao 6 exige dele",
        _com(_tcp, "manifesto", config_campos=[{"nome": "n" * 35}]),
        {("manifesto.config_campos[0].nome", "decl_config_campo_invalido")},
    ),
    (
        "categoria fora do vocabulario",
        _com(_tcp, "manifesto", categoria="geladeira"),
        {("manifesto.categoria", "decl_categoria_invalida")},
    ),
    (
        "capacidade como string, que itera como letras",
        _com(_tcp, "manifesto", capacidades="ligar"),
        {("manifesto.capacidades", "decl_capacidade_desconhecida")},
    ),
    (
        "capacidade que nao existe",
        _com(_tcp, "manifesto", capacidades=["ligar", "voar"]),
        {("manifesto.capacidades", "decl_capacidade_desconhecida")},
    ),
    (
        "teclas como string, que itera como letras",
        _com(_tcp, "manifesto", teclas="canal_mais"),
        {("manifesto.teclas", "decl_vocabulario_invalido")},
    ),
    (
        "palavra fora do vocabulario da secao 6, com a capacidade declarada",
        _com(
            _tcp,
            "manifesto",
            capacidades=["ligar", "desligar", "fonte", "comando_extra", "tecla"],
            teclas=["canal_mais", "voar"],
        ),
        {
            ("manifesto.teclas", "decl_vocabulario_invalido"),
            ("comandos.tecla", "decl_comando_vazio"),
        },
    ),
    (
        "palavras declaradas sem a capacidade que as fala",
        _com(_tcp, "manifesto", ventos=["auto"]),
        {("manifesto.ventos", "decl_vocabulario_invalido")},
    ),
    (
        "agrupar fora de multiroom, regra da secao 6",
        _com(_tcp, "manifesto", capacidades=["agrupar"], **{}),
        {
            ("manifesto.capacidades", "decl_capacidade_desconhecida"),
            ("comandos.ligar", "decl_capacidade_desconhecida"),
            ("comandos.desligar", "decl_capacidade_desconhecida"),
            ("comandos.fonte", "decl_capacidade_desconhecida"),
            ("comandos.comando_extra", "decl_capacidade_desconhecida"),
            ("comandos.agrupar", "decl_comando_vazio"),
        },
    ),
    (
        "auth fora do vocabulario",
        _com(_tcp, "manifesto", auth="senha"),
        {("manifesto.auth", "decl_auth_invalida")},
    ),
    (
        "auth declarada sem o texto de ajuda que a secao 6 exige",
        _com(_tcp, "manifesto", auth="codigo"),
        {("manifesto.textos", "decl_textos_invalidos")},
    ),
    (
        "padrao de campo numerico, que esvazia o formulario",
        _com(
            _http,
            "manifesto",
            config_campos=[{"nome": "porta", "padrao": 4352}],
            textos={
                "pt": {"descricao": "A", "campo_porta": "Porta"},
                "en": {"descricao": "B", "campo_porta": "Port"},
            },
        ),
        {
            ("manifesto.config_campos[0].padrao", "decl_config_campo_invalido"),
            ("transporte.http.cabecalhos.Authorization", "decl_cabecalho_invalido"),
        },
    ),
    (
        "campo chamado ip, que congelaria o endereco",
        _com(
            _http,
            "manifesto",
            config_campos=[{"nome": "ip"}],
            textos={
                "pt": {"descricao": "A", "campo_ip": "IP"},
                "en": {"descricao": "B", "campo_ip": "IP"},
            },
        ),
        {
            ("manifesto.config_campos", "decl_config_campo_invalido"),
            ("transporte.http.cabecalhos.Authorization", "decl_cabecalho_invalido"),
        },
    ),
    (
        "textos num idioma so",
        _com(_tcp, "manifesto", textos={"pt": {"descricao": "Matriz"}}),
        {("manifesto.textos", "decl_textos_invalidos")},
    ),
    (
        "descoberta com assinatura que nao e lista",
        _tcp(descoberta={"ssdp_fabricantes": "exemplo"}),
        {("descoberta.ssdp_fabricantes", "decl_descoberta_invalida")},
    ),
    (
        "dois transportes num arquivo so",
        _tcp(transporte={"tcp": {"porta": 23}, "udp": {"porta": 5000}}),
        {("transporte", "decl_transporte_invalido")},
    ),
    (
        "transporte que ninguem fala",
        _tcp(transporte={"serial": {"porta": 23}}),
        {
            ("transporte", "decl_transporte_invalido"),
            ("transporte.serial", "decl_chave_desconhecida"),
        },
    ),
    (
        "porta zero, que so sabe falhar",
        _tcp(transporte={"tcp": {"porta": 0}}),
        {("transporte.tcp.porta", "decl_porta_invalida")},
    ),
    (
        "porta como texto",
        _tcp(transporte={"tcp": {"porta": "23"}}),
        {("transporte.tcp.porta", "decl_porta_invalida")},
    ),
    (
        "porta acima do que existe",
        _tcp(transporte={"tcp": {"porta": 65536}}),
        {("transporte.tcp.porta", "decl_porta_invalida")},
    ),
    (
        "prazo curto demais para uma placa ARM",
        _tcp(transporte={"tcp": {"porta": 23, "timeout_s": 0.2}}),
        {("transporte.tcp.timeout_s", "decl_timeout_invalido")},
    ),
    (
        "prazo que segura o poll por meio minuto",
        _tcp(transporte={"tcp": {"porta": 23, "timeout_s": 31}}),
        {("transporte.tcp.timeout_s", "decl_timeout_invalido")},
    ),
    (
        "intervalo minimo negativo",
        _tcp(transporte={"tcp": {"porta": 23, "intervalo_min_ms": -1}}),
        {("transporte.tcp.intervalo_min_ms", "decl_intervalo_invalido")},
    ),
    (
        "terminador longo o bastante para ser um comando",
        _tcp(transporte={"tcp": {"porta": 23, "terminador": "\r\n\r\n\r"}}),
        {("transporte.tcp.terminador", "decl_terminador_invalido")},
    ),
    (
        "base apontada para a internet, e nao para o aparelho",
        _com(_http, "transporte", http={"base": "http://exemplo.invalido"}),
        {("transporte.http.base", "decl_base_invalida")},
    ),
    (
        "base com o marcador e um caminho de recheio",
        _com(_http, "transporte", http={"base": "http://{ip}/../outro"}),
        {("transporte.http.base", "decl_base_invalida")},
    ),
    (
        "base com porta zero",
        _com(_http, "transporte", http={"base": "http://{ip}:0"}),
        {("transporte.http.base", "decl_base_invalida")},
    ),
    (
        "metodo que este daemon nao fala",
        _com(_http, "transporte", http={"base": "http://{ip}", "metodo": "PATCH"}),
        {("transporte.http.metodo", "decl_metodo_invalido")},
    ),
    (
        "cabecalho carregando o segredo em vez do nome do campo",
        _com(
            _http,
            "transporte",
            http={"base": "http://{ip}", "cabecalhos": {"Authorization": "Bearer 123"}},
        ),
        {("transporte.http.cabecalhos.Authorization", "decl_cabecalho_invalido")},
    ),
    (
        "comando que manda nada",
        _com(_tcp, "comandos", ligar={"envia": ""}),
        {("comandos.ligar.envia", "decl_comando_vazio")},
    ),
    (
        "comando que e uma string solta",
        _com(_tcp, "comandos", ligar="PWR ON"),
        {("comandos.ligar", "decl_comando_invalido")},
    ),
    (
        "capacidade declarada sem comando",
        _com(
            _tcp, "manifesto", capacidades=["ligar", "desligar", "fonte", "comando_extra", "mudo"]
        ),
        {("comandos.mudo", "decl_comando_vazio")},
    ),
    (
        "comando de acao que o manifesto nao declara",
        _com(_tcp, "comandos", volume={"envia": "VOL {valor}"}),
        {("comandos.volume", "decl_capacidade_desconhecida")},
    ),
    (
        "envia e sequencia no mesmo comando",
        _com(_tcp, "comandos", ligar={"envia": "PWR ON", "sequencia": [{"envia": "PWR ON"}]}),
        {("comandos.ligar", "decl_comando_invalido")},
    ),
    (
        "valores como lista de fontes, e nao como mapa",
        _com(_tcp, "comandos", fonte={"envia": "SRC {valor}", "valores": ["HDMI1", "HDMI2"]}),
        {("comandos.fonte.valores", "decl_valores_invalido")},
    ),
    (
        "repeticao que nao repete nada",
        _com(_tcp, "comandos", ligar={"envia": "PWR ON", "repete": 0}),
        {("comandos.ligar.repete", "decl_repete_invalido")},
    ),
    (
        "repeticao que martela o aparelho",
        _com(_tcp, "comandos", ligar={"envia": "PWR ON", "repete": 500}),
        {("comandos.ligar.repete", "decl_repete_invalido")},
    ),
    (
        "hexadecimal com meio byte",
        _com(_udp, "comandos", ligar={"envia": "FF EE E", "hex": True}),
        {("comandos.ligar.envia", "decl_hex_invalido")},
    ),
    (
        "hexadecimal com um espaco dentro do par de bytes",
        _com(_udp, "comandos", ligar={"envia": "0 A0B", "hex": True}),
        {("comandos.ligar.envia", "decl_hex_invalido")},
    ),
    (
        "comando que o utf-8 nao escreve",
        _com(_tcp, "comandos", ligar={"envia": "PWR \ud800"}),
        {("comandos.ligar.envia", "decl_texto_nao_gravavel")},
    ),
    (
        "terminador que o utf-8 nao escreve",
        _tcp(transporte={"tcp": {"porta": 23, "terminador": "\ud800"}}),
        {("transporte.tcp.terminador", "decl_terminador_invalido")},
    ),
    (
        "regex que o utf-8 nao escreve",
        _com(_tcp, "estado", le={"fonte": {"regex": "SRC (\ud800)"}}),
        {("estado.le.fonte.regex", "decl_texto_nao_gravavel")},
    ),
    (
        "chave desconhecida que o utf-8 nao escreve",
        _tcp(**{"schema\ud800": 1}),
        {("arquivo.schema", "decl_chave_desconhecida")},
    ),
    (
        "hexadecimal que carrega um marcador",
        _com(_udp, "comandos", ligar={"envia": "FF {valor}", "hex": True}),
        {("comandos.ligar.envia", "decl_hex_invalido")},
    ),
    (
        "hexadecimal onde o transporte e HTTP",
        _com(_http, "comandos", ligar={"envia": "/api/power", "hex": True}),
        {("comandos.ligar.hex", "decl_chave_desconhecida")},
    ),
    (
        "caminho HTTP sem barra, que cai na pagina inicial",
        _com(_http, "comandos", ligar={"envia": "api/power"}),
        {("comandos.ligar.envia", "decl_comando_invalido")},
    ),
    (
        "estado que nao e objeto",
        _tcp(estado=["STATUS?"]),
        {("estado", "decl_estado_invalido")},
    ),
    (
        "estado que le sem perguntar",
        _tcp(estado={"le": {"ligado": {"regex": "PWR (ON|OFF)", "verdadeiro": "ON"}}}),
        {("estado.pede", "decl_estado_invalido")},
    ),
    (
        "le que nao e objeto, o arquivo que derrubava o appliance no boot",
        _com(_tcp, "estado", le=["ligado"]),
        {("estado.le", "decl_leitura_invalida")},
    ),
    (
        "leitura que e uma string",
        _com(_tcp, "estado", le={"ligado": "PWR (ON|OFF)"}),
        {("estado.le.ligado", "decl_leitura_invalida")},
    ),
    (
        "leitura ligada e vazia",
        _com(_tcp, "estado", le={"ligado": {"verdadeiro": "ON"}}),
        {("estado.le.ligado", "decl_leitura_vazia")},
    ),
    (
        "leitura por regex e por json ao mesmo tempo",
        _com(_tcp, "estado", le={"fonte": {"regex": r"SRC (\d)", "json": "src"}}),
        {("estado.le.fonte", "decl_leitura_invalida")},
    ),
    (
        "booleano lido sem dizer o que e verdadeiro",
        _com(_tcp, "estado", le={"ligado": {"regex": "PWR (ON|OFF)"}}),
        {("estado.le.ligado.verdadeiro", "decl_leitura_invalida")},
    ),
    (
        "verdadeiro num campo que nao e booleano",
        _com(_tcp, "estado", le={"fonte": {"regex": r"SRC (\d)", "verdadeiro": "1"}}),
        {("estado.le.fonte.verdadeiro", "decl_leitura_invalida")},
    ),
    (
        "leitura de um campo que o Estado nao tem",
        _com(_tcp, "estado", le={"online": {"regex": "PWR (ON)"}}),
        {("estado.le.online", "decl_campo_desconhecido")},
    ),
    (
        "caminho json que nao e caminho",
        _com(_http, "estado", le={"volume": {"json": "volume/value"}}),
        {("estado.le.volume.json", "decl_leitura_invalida")},
    ),
    (
        "regex que nem compila",
        _com(_tcp, "estado", le={"fonte": {"regex": "SRC ("}}),
        {("estado.le.fonte.regex", "decl_regex_invalida")},
    ),
    (
        "regex sem grupo de captura, que matava o poll",
        _com(_tcp, "estado", le={"fonte": {"regex": "SRC OK"}}),
        {("estado.le.fonte.regex", "decl_regex_sem_grupo")},
    ),
    (
        "regex catastrofica, recusada na prova de fogo",
        _com(_tcp, "estado", le={"fonte": {"regex": CATASTROFICA}}),
        {("estado.le.fonte.regex", "decl_regex_perigosa")},
    ),
    (
        "escala de volume que rende sempre o mesmo numero",
        _http(escala_volume={"min": 0, "max": 0}),
        {("escala_volume", "decl_escala_invalida")},
    ),
    (
        "escala de volume ao contrario",
        _http(escala_volume={"min": 79, "max": 0}),
        {("escala_volume", "decl_escala_invalida")},
    ),
    (
        "escala de volume que nao e objeto",
        _http(escala_volume=79),
        {("escala_volume", "decl_escala_invalida")},
    ),
)


@pytest.mark.parametrize(("rotulo", "dados", "esperado"), ATAQUES, ids=[a[0] for a in ATAQUES])
def test_o_arquivo_quebrado_e_recusado_pelo_campo_e_nada_mais(rotulo, dados, esperado):
    assert _problemas(dados) == esperado


def test_todo_codigo_do_vocabulario_tem_um_ataque():
    """A code nobody attacks is a code the panel translates for a case that never happens.

    Um código que ninguém ataca é um código que o painel traduz para um caso que nunca ocorre.
    """
    atacados = {codigo for _, _, esperado in ATAQUES for _, codigo in esperado}
    assert atacados == set(CODIGOS)


def test_todo_codigo_e_estavel_e_unico():
    assert len(set(CODIGOS)) == len(CODIGOS)
    assert all(codigo.startswith("decl_") for codigo in CODIGOS)


@pytest.mark.parametrize("arquivo", [_tcp, _http, _udp], ids=["tcp", "http", "udp"])
def test_os_tres_transportes_viram_dado_tipado(arquivo):
    definicao = validar(arquivo(), regex=Fogo())
    assert isinstance(definicao, Definicao)
    assert isinstance(definicao.transporte, Tcp | Http | Udp)
    assert all(isinstance(comando, Comando) for comando in definicao.comandos.values())


@pytest.mark.parametrize("arquivo", [_tcp, _http, _udp], ids=["tcp", "http", "udp"])
def test_o_manifesto_gerado_passa_na_secao_6(arquivo):
    """Rule 1 of section 2: a declarative driver is a Driver like any other, so its manifest
    is judged by the same validator the native ones face.

    Regra 1 da seção 2: um driver declarativo é um Driver como outro qualquer, então o
    manifesto dele é julgado pelo mesmo validador que os nativos enfrentam.
    """
    manifesto = validar(arquivo(), regex=Fogo()).manifesto
    assert isinstance(manifesto, Manifesto)
    assert validar_manifesto(manifesto) is None


@pytest.mark.parametrize("arquivo", [_tcp, _http, _udp], ids=["tcp", "http", "udp"])
def test_o_motor_nunca_vem_do_arquivo(arquivo):
    """A declaration cannot claim to be code that shipped in the image.

    Uma declaração não pode se dizer código que embarcou na imagem.
    """
    assert validar(arquivo(), regex=Fogo()).manifesto.motor == "declarativo"


def test_o_comando_e_a_leitura_viram_passo_e_leitura():
    definicao = validar(_tcp(), regex=Fogo())
    assert definicao.comandos["ligar"] == Comando(passos=(Passo(envia="PWR ON"),))
    assert definicao.comandos["fonte"].valores == {"HDMI1": "1", "HDMI2": "2"}
    assert definicao.fontes == ("HDMI1", "HDMI2")
    assert definicao.estado == Consulta(
        pede=(Passo(envia="STATUS?"),),
        le=(
            Leitura(campo="ligado", regex="PWR (ON|OFF)", verdadeiro="ON"),
            Leitura(campo="fonte", regex=r"SRC (\d)"),
        ),
    )


def test_estado_em_mais_de_uma_requisicao():
    """Section 7 requires state from more than one request, so pede is always a sequence.

    A seção 7 exige estado de mais de uma requisição, então o pede é sempre uma sequência.
    """
    definicao = validar(_http(), regex=Fogo())
    assert definicao.estado is not None
    assert [passo.envia for passo in definicao.estado.pede] == ["/api/status", "/api/volume"]
    assert all(passo.metodo == "GET" for passo in definicao.estado.pede)


def test_repeticao_e_sequencia_com_intervalo_sao_declaradas():
    """Relative volume and an infrared bridge are repetition, not a loop written in the file.

    Volume relativo e ponte de infravermelho são repetição, não laço escrito no arquivo.
    """
    arquivo = _com(
        _tcp,
        "comandos",
        comando_extra={
            "sequencia": [{"envia": "MENU"}, {"envia": "{valor}"}],
            "repete": 3,
            "intervalo_ms": 120,
        },
    )
    comando = validar(arquivo, regex=Fogo()).comandos["comando_extra"]
    assert comando.passos == (Passo(envia="MENU"), Passo(envia="{valor}"))
    assert (comando.repete, comando.intervalo_ms) == (3, 120)


def test_literal_hexadecimal_atravessa_inteiro():
    definicao = validar(_udp(), regex=Fogo())
    assert definicao.comandos["ligar"].passos == (Passo(envia="FF EE EE 01", hex=True),)
    assert isinstance(definicao.transporte, Udp)
    assert definicao.transporte.terminador == ""


def test_todo_literal_hexadecimal_aceito_decodifica():
    """The validation proves what the transport will do, because a literal that only LOOKS
    hexadecimal was accepted and then failed on every command, as erro_aparelho, far from it.

    A validação prova o que o transporte vai fazer, porque um literal que só PARECE
    hexadecimal era aceito e depois falhava em todo comando, como erro_aparelho, longe dele.
    """
    definicao = validar(_udp(), regex=Fogo())
    passos = [passo for comando in definicao.comandos.values() for passo in comando.passos]
    assert passos
    for passo in passos:
        assert passo.hex
        assert bytes.fromhex(passo.envia)


def test_nome_de_campo_no_limite_ainda_ganha_o_texto_da_secao_6():
    """The ceiling is exactly where the campo_<nome> key of section 6 stops fitting, so a name
    at the limit is a field like any other and the manifest it builds passes section 6.

    O teto é exatamente onde a chave campo_<nome> da seção 6 para de caber, então um nome no
    limite é campo como outro qualquer e o manifesto que ele monta passa na seção 6.
    """
    nome = "n" * CAMPO_NOME_MAXIMO
    arquivo = _com(
        _tcp,
        "manifesto",
        config_campos=[{"nome": nome}],
        textos={
            "pt": {"descricao": "Matriz de video", f"campo_{nome}": "Campo"},
            "en": {"descricao": "Video matrix", f"campo_{nome}": "Field"},
        },
    )
    manifesto = validar(arquivo, regex=Fogo()).manifesto
    assert manifesto.config_campos[0].nome == nome
    assert manifesto.textos["pt"][f"campo_{nome}"] == "Campo"
    assert validar_manifesto(manifesto) is None


SURROGADOS = (
    ("rotulo", _com(_tcp, "manifesto", rotulo={"pt": "Matriz \ud800", "en": "Video matrix"})),
    (
        "texto",
        _com(
            _tcp,
            "manifesto",
            textos={"pt": {"descricao": "Matriz \ud800"}, "en": {"descricao": "Matrix"}},
        ),
    ),
    ("comando", _com(_tcp, "comandos", ligar={"envia": "PWR \ud800"})),
    ("chave", _tcp(**{"schema\ud800": 1})),
)


@pytest.mark.parametrize(("onde", "dados"), SURROGADOS, ids=[caso[0] for caso in SURROGADOS])
def test_o_que_o_utf_8_nao_escreve_e_arquivo_ruim_e_nao_erro_interno(onde, dados):
    """The route saves the file as the integrator typed it, so a text utf-8 cannot write made
    the save answer 500 after the validate route had accepted that very file. Both answer a
    refusal per field now, and the refusal itself is a thing that can be written.

    A rota grava o arquivo como o integrador digitou, então um texto que o utf-8 não escreve
    fazia a gravação responder 500 depois de a rota de validar aceitar aquele mesmo arquivo.
    Agora as duas respondem recusa por campo, e a própria recusa é algo que se consegue gravar.
    """
    with pytest.raises(UnicodeEncodeError):
        json.dumps(dados, ensure_ascii=False).encode("utf-8")
    problemas = _problemas(dados)
    assert problemas
    json.dumps(sorted(problemas), ensure_ascii=False).encode("utf-8")


def test_saudacao_inicial_e_declarada():
    """The PJLink case: a greeting line before the first answer is data, never a condition.

    O caso do PJLink: uma linha de saudação antes da primeira resposta é dado, nunca condicional.
    """
    arquivo = _tcp(transporte={"tcp": {"porta": 4352, "saudacao": True}})
    transporte = validar(arquivo, regex=Fogo()).transporte
    assert transporte == Tcp(porta=4352, saudacao=True)


def test_corpo_http_com_chaves_nao_e_confundido_com_marcador():
    """A JSON body is the common case of a relay, and it is full of braces.

    Um corpo JSON é o caso comum de um relé, e ele é cheio de chaves.
    """
    comando = validar(_http(), regex=Fogo()).comandos["ligar"]
    assert comando.passos == (Passo(envia="/api/power", metodo="POST", corpo='{"on": true}'),)


def test_cabecalho_so_nomeia_campo_do_cadastro():
    """The value of a header comes from the registration, so a shared file carries no secret.

    O valor de um cabeçalho vem do cadastro, então um arquivo compartilhado não leva segredo.
    """
    transporte = validar(_http(), regex=Fogo()).transporte
    assert isinstance(transporte, Http)
    assert transporte.cabecalhos == (Cabecalho(nome="Authorization", campo="token"),)


def test_cabecalho_que_aponta_para_campo_inexistente_e_recusado():
    arquivo = _com(
        _http, "transporte", http={"base": "http://{ip}", "cabecalhos": {"X-Chave": "senha"}}
    )
    assert _problemas(arquivo) == {
        ("transporte.http.cabecalhos.X-Chave", "decl_cabecalho_invalido")
    }


def test_escala_de_volume_negativa_e_valida():
    """A receiver in dB runs from -80 to 0, and a rule of "positive only" would refuse it.

    Um receiver em dB vai de -80 a 0, e uma regra de "só positivo" o recusaria.
    """
    definicao = validar(_http(escala_volume={"min": -80, "max": 0}), regex=Fogo())
    assert definicao.escala == Escala(minimo=-80, maximo=0)


def test_caractere_de_controle_sai_do_que_vai_ao_fio():
    """A source label copied from a manual with a carriage return became TWO commands.

    Um rótulo de fonte copiado do manual com um retorno de carro virava DOIS comandos.
    """
    arquivo = _com(
        _tcp,
        "comandos",
        fonte={"envia": "SRC {valor}\r\nPWR OFF", "valores": {"HDMI\r1": "1\rPWR OFF"}},
    )
    comando = validar(arquivo, regex=Fogo()).comandos["fonte"]
    assert comando.passos == (Passo(envia="SRC {valor}PWR OFF"),)
    assert comando.valores == {"HDMI1": "1PWR OFF"}


def test_caractere_de_controle_sai_do_que_o_painel_mostra():
    arquivo = _com(_tcp, "manifesto", rotulo={"pt": "Matriz\r\nfalsa", "en": "Fake\r\nmatrix"})
    manifesto = validar(arquivo, regex=Fogo()).manifesto
    assert manifesto.rotulo == {"pt": "Matrizfalsa", "en": "Fakematrix"}


def test_o_terminador_mantem_o_byte_de_controle_que_ele_e():
    transporte = validar(_tcp(), regex=Fogo()).transporte
    assert isinstance(transporte, Tcp)
    assert transporte.terminador == "\r"


def test_toda_regex_do_arquivo_passa_pela_prova_de_fogo_antes_de_salvar():
    """Section 7: the pattern is refused at save time, never discovered inside a poll.

    Seção 7: o padrão é recusado na hora de salvar, nunca descoberto dentro de um poll.
    """
    fogo = Fogo()
    validar(_tcp(), regex=fogo)
    assert fogo.perguntou == ["PWR (ON|OFF)", r"SRC (\d)"]


def test_prova_de_fogo_que_nao_responde_recusa_o_padrao():
    """A fire test that cannot answer is not a pattern proven safe, and validation never raises.

    Uma prova de fogo que não responde não é padrão provado seguro, e a validação nunca estoura.
    """
    assert _problemas(_tcp(), fogo=Fogo(estoura=True)) == {
        ("estado.le.ligado.regex", "decl_regex_perigosa"),
        ("estado.le.fonte.regex", "decl_regex_perigosa"),
    }


def test_regex_que_nem_compila_nao_chega_na_prova_de_fogo():
    fogo = Fogo()
    _problemas(_com(_tcp, "estado", le={"fonte": {"regex": "SRC ("}}), fogo=fogo)
    assert fogo.perguntou == []


def test_leitura_so_alcanca_campo_que_o_estado_publica():
    """A reading is a field of Estado, so a new key on the panel is a new field there.

    Uma leitura é um campo do Estado, então chave nova no painel é campo novo lá.
    """
    assert set(LEITURAS) < {campo.name for campo in fields(Estado)}
    assert "online" not in LEITURAS
    assert "detalhe" not in LEITURAS


def test_textos_herdam_o_rotulo_quando_o_arquivo_nao_traz_bloco():
    """Section 6 wants a descricao in both languages and section 7 shows a file without one.

    A seção 6 quer uma descricao nos dois idiomas e a seção 7 mostra um arquivo sem uma.
    """
    manifesto = validar(_tcp(), regex=Fogo()).manifesto
    assert manifesto.textos == {
        "pt": {"descricao": "Matriz de video"},
        "en": {"descricao": "Video matrix"},
    }


def test_texto_escrito_no_arquivo_vence_o_rotulo():
    arquivo = _com(
        _tcp,
        "manifesto",
        textos={"pt": {"descricao": "Matriz de 8 entradas"}, "en": {"descricao": "8 input matrix"}},
    )
    manifesto = validar(arquivo, regex=Fogo()).manifesto
    assert manifesto.textos["pt"]["descricao"] == "Matriz de 8 entradas"


def test_config_campos_viram_campo_tipado():
    manifesto = validar(_http(), regex=Fogo()).manifesto
    campo = manifesto.config_campos[0]
    assert (campo.nome, campo.tipo, campo.obrigatorio) == ("token", TipoCampo.SEGREDO, True)
    assert manifesto.auth is Auth.NENHUMA


def test_driver_sem_comando_nenhum_e_valido():
    """A device that only answers is a legitimate driver: it reads and commands nothing.

    Um aparelho que só responde é driver legítimo: ele lê e não comanda nada.
    """
    arquivo = _tcp(
        manifesto={
            "tipo": "sensor_tcp",
            "rotulo": {"pt": "Sensor", "en": "Sensor"},
            "categoria": "outro",
            "capacidades": [],
        },
        comandos={},
    )
    definicao = validar(arquivo, regex=Fogo())
    assert definicao.comandos == {}
    assert definicao.fontes == ()


def test_todo_problema_sai_de_uma_vez():
    """The panel shows every refusal at once, so the file is fixed in one pass.

    O painel mostra toda recusa de uma vez, para o arquivo ser consertado numa passada.
    """
    arquivo = {
        "manifesto": {
            "tipo": "Matriz Exemplo",
            "rotulo": {"pt": "Matriz"},
            "categoria": "geladeira",
        },
        "transporte": {"tcp": {"porta": 0, "timeout_s": 90}},
        "comandos": {"ligar": {"envia": ""}},
        "estado": {"pede": "STATUS?", "le": {"ligado": {"regex": "PWR ON"}}},
        "escala_volume": {"min": 10, "max": 10},
    }
    assert _problemas(arquivo) == {
        ("manifesto.tipo", "decl_tipo_invalido"),
        ("manifesto.rotulo", "decl_rotulo_invalido"),
        ("manifesto.categoria", "decl_categoria_invalida"),
        ("transporte.tcp.porta", "decl_porta_invalida"),
        ("transporte.tcp.timeout_s", "decl_timeout_invalido"),
        ("comandos.ligar.envia", "decl_comando_vazio"),
        ("estado.le.ligado.verdadeiro", "decl_leitura_invalida"),
        ("escala_volume", "decl_escala_invalida"),
    }


@pytest.mark.parametrize(
    "torto",
    [
        None,
        7,
        [],
        "",
        {"manifesto": None, "transporte": None, "comandos": None, "estado": None},
        {"manifesto": {"tipo": {"a": 1}, "rotulo": [], "capacidades": {}}},
        {"transporte": {"tcp": {"porta": [23], "terminador": 7, "timeout_s": "3"}}},
        {"comandos": {"ligar": {"sequencia": [None, 7, {"envia": None}]}}},
        {"estado": {"pede": [], "le": {"ligado": {"regex": 7}}}},
        {"estado": {"le": {7: {"regex": "(a)"}}}},
        {"manifesto": {"textos": {"pt": {7: "A"}, "en": {"descricao": 7}}}},
        {"descoberta": {"ssdp_st": [7]}},
        {"escala_volume": {"min": True, "max": 100}},
        {"transporte": {"http": {"base": "http://{ip}", "cabecalhos": {7: "token"}}}},
        {"manifesto": {"tipo": "a" * 5000}, "transporte": {"tcp": {"porta": 23}}},
    ],
    ids=range(15),
)
def test_a_validacao_nunca_estoura_outra_excecao(torto):
    """The loader calls this on a file written by hand, and a raise here took an appliance down.

    O carregador chama isto sobre um arquivo escrito à mão, e um estouro aqui derrubava um
    appliance.
    """
    with pytest.raises(DeclaracaoInvalida) as erro:
        validar(torto, regex=Fogo())
    assert erro.value.problemas
    assert all(codigo in CODIGOS for _, codigo in erro.value.problemas)
    assert isinstance(erro.value, ValueError)


def test_o_problema_carrega_campo_e_codigo_e_a_mensagem_nao_e_frase():
    with pytest.raises(DeclaracaoInvalida) as erro:
        validar({}, regex=Fogo())
    assert erro.value.problemas == (
        ("manifesto", "decl_manifesto_invalido"),
        ("transporte", "decl_transporte_invalido"),
    )
    assert (
        str(erro.value)
        == "manifesto: decl_manifesto_invalido; transporte: decl_transporte_invalido"
    )


@pytest.mark.parametrize(
    "valor",
    [
        Passo(envia="PWR ON"),
        Comando(passos=()),
        Leitura(campo="ligado"),
        Consulta(pede=()),
        Escala(minimo=0, maximo=79),
        Cabecalho(nome="X", campo="token"),
        Tcp(porta=23),
        Http(base="http://{ip}"),
        Udp(porta=5000),
    ],
    ids=lambda valor: type(valor).__name__,
)
def test_o_formato_e_congelado(valor):
    with pytest.raises(FrozenInstanceError):
        valor.campo_inexistente = "x"


def test_a_definicao_e_congelada():
    definicao = validar(_tcp(), regex=Fogo())
    with pytest.raises(FrozenInstanceError):
        definicao.transporte = Udp(porta=1)
