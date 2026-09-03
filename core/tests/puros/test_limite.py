# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: five failures per IP block for fifteen minutes, sixty attempts a minute.

The identity counted is the real IP of the request, so ip_do_pedido is tested here too.

Seção 9: cinco falhas por IP bloqueiam por quinze minutos, sessenta tentativas por minuto.

A identidade contada é o IP real da requisição, então ip_do_pedido é testado aqui também.
"""

import time

import pytest
from aiohttp.test_utils import make_mocked_request

from iphub.limite import BLOQUEIO_S, FALHAS_ATE_BLOQUEIO, JANELA_GLOBAL_S, TETO_GLOBAL, Limite
from iphub.portao import ip_do_pedido

ATACANTE = "192.0.2.10"
VIZINHO = "192.0.2.11"


class Relogio:
    def __init__(self, agora: float = 1_000_000.0) -> None:
        self.agora = agora

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos


@pytest.fixture
def relogio() -> Relogio:
    return Relogio()


@pytest.fixture
def limite(relogio: Relogio) -> Limite:
    return Limite(agora=relogio)


def falhar(limite: Limite, ip: str, vezes: int) -> None:
    for _ in range(vezes):
        limite.registrar_tentativa()
        limite.registrar_falha(ip)


def test_ip_desconhecido_passa(limite):
    assert limite.permitido(ATACANTE)
    assert limite.bloqueado_ate(ATACANTE) is None


def test_quatro_falhas_ainda_permitem_a_quinta_tentativa(limite):
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO - 1)
    assert limite.permitido(ATACANTE)
    assert limite.bloqueado_ate(ATACANTE) is None


def test_a_quinta_falha_bloqueia(limite, relogio):
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO)
    assert not limite.permitido(ATACANTE)
    assert limite.bloqueado_ate(ATACANTE) == relogio.agora + BLOQUEIO_S


def test_o_bloqueio_dura_a_janela_inteira(limite, relogio):
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO)
    relogio.avancar(BLOQUEIO_S - 1)
    assert not limite.permitido(ATACANTE)


def test_o_bloqueio_levanta_depois_da_janela(limite, relogio):
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO)
    relogio.avancar(BLOQUEIO_S + 1)
    assert limite.permitido(ATACANTE)
    assert limite.bloqueado_ate(ATACANTE) is None


def test_consultar_o_limite_nao_apaga_o_bloqueio(limite):
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO)
    for _ in range(10):
        assert not limite.permitido(ATACANTE)
    assert limite.bloqueado_ate(ATACANTE) is not None


def test_insistir_durante_o_bloqueio_nao_libera(limite, relogio):
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO)
    relogio.avancar(60)
    falhar(limite, ATACANTE, 3)
    assert not limite.permitido(ATACANTE)


def test_sucesso_limpa_o_contador(limite):
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO - 1)
    limite.registrar_sucesso(ATACANTE)
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO - 1)
    assert limite.permitido(ATACANTE)


def test_bloquear_um_ip_nao_bloqueia_outro(limite):
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO)
    assert not limite.permitido(ATACANTE)
    assert limite.permitido(VIZINHO)


def test_o_teto_global_recusa_a_tentativa_seguinte(limite):
    for _ in range(TETO_GLOBAL - 1):
        limite.registrar_tentativa()
    assert limite.permitido(VIZINHO)
    limite.registrar_tentativa()
    assert not limite.permitido(VIZINHO)


def test_ataque_distribuido_bate_no_teto_global(limite):
    # Why: one failure per IP never reaches the per IP block, so only the global window
    # stops an attacker who rotates addresses.
    # Por que: uma falha por IP nunca chega ao bloqueio por IP, então só a janela global
    # segura um atacante que troca de endereço.
    for n in range(TETO_GLOBAL):
        ip = f"192.0.2.{n}"
        assert limite.permitido(ip)
        limite.registrar_tentativa()
        limite.registrar_falha(ip)
    assert not limite.permitido("198.51.100.7")


def test_o_teto_global_segue_de_pe_dentro_do_minuto(limite, relogio):
    for _ in range(TETO_GLOBAL):
        limite.registrar_tentativa()
    relogio.avancar(JANELA_GLOBAL_S / 2)
    assert not limite.permitido(VIZINHO)


def test_o_teto_global_levanta_depois_do_minuto(limite, relogio):
    for _ in range(TETO_GLOBAL):
        limite.registrar_tentativa()
    relogio.avancar(JANELA_GLOBAL_S + 1)
    assert limite.permitido(VIZINHO)


def test_a_podagem_nao_deixa_as_estruturas_crescerem(limite, relogio):
    for n in range(200):
        falhar(limite, f"198.51.100.{n % 250}", 1)
    relogio.avancar(BLOQUEIO_S + 1)
    limite.registrar_tentativa()
    assert limite._por_ip == {}
    assert len(limite._global) == 1


def test_a_podagem_nao_esquece_um_bloqueio_em_curso(limite, relogio):
    falhar(limite, ATACANTE, FALHAS_ATE_BLOQUEIO)
    relogio.avancar(BLOQUEIO_S - 1)
    limite.registrar_tentativa()
    assert ATACANTE in limite._por_ip
    assert not limite.permitido(ATACANTE)


def test_o_relogio_padrao_e_o_monotonico():
    # Why: the counters live in memory only, so a backwards step of the wall clock (NTP on a
    # board with no battery) would hold the global window shut for the size of the step.
    # Por que: os contadores vivem só em memória, então um passo para trás do relógio de parede
    # (NTP numa placa sem bateria) manteria a janela global fechada pelo tamanho do passo.
    assert abs(Limite()._agora() - time.monotonic()) < 1.0


def test_a_tentativa_que_nao_e_registrada_nao_gasta_a_janela(limite):
    # Why: a request refused before the credential is checked costs no PBKDF2, so it may not
    # spend a slot; otherwise cheap malformed requests lock the owner out of login.
    # Por que: uma requisição recusada antes de conferir a credencial não custa PBKDF2, então
    # não pode gastar vaga; senão requisições malformadas baratas trancam o dono fora do login.
    for _ in range(TETO_GLOBAL * 2):
        assert limite.permitido(VIZINHO)
    for _ in range(TETO_GLOBAL):
        limite.registrar_tentativa()
    assert not limite.permitido(VIZINHO)


def test_consultar_o_bloqueio_tambem_poda(limite, relogio):
    # Why: an attacker that rotates addresses stops adding keys only when every entry point
    # prunes, not only the ones that write.
    # Por que: um atacante que troca de endereço só para de somar chaves quando todo ponto de
    # entrada poda, não só os que escrevem.
    for numero in range(50):
        falhar(limite, f"198.51.100.{numero}", 1)
    relogio.avancar(BLOQUEIO_S + 1)
    assert limite.bloqueado_ate(ATACANTE) is None
    assert limite._por_ip == {}


def test_o_sucesso_tambem_poda(limite, relogio):
    for numero in range(50):
        falhar(limite, f"198.51.100.{numero}", 1)
    relogio.avancar(BLOQUEIO_S + 1)
    limite.registrar_sucesso(ATACANTE)
    assert limite._por_ip == {}


PROXY = frozenset({"127.0.0.1"})


class Transporte:
    """Only the peer of the socket, which is all the gate reads from the transport.

    Só o par do socket, que é tudo o que o portão lê do transporte.
    """

    def __init__(self, par: str) -> None:
        self._par = par

    def get_extra_info(self, chave: str, padrao: object = None) -> object:
        return (self._par, 45678) if chave == "peername" else padrao


def pedido(par: str, *encaminhados: str):
    """A request from par carrying one X-Forwarded-For line per value given.

    Uma requisição vinda de par com uma linha de X-Forwarded-For por valor dado.
    """
    return make_mocked_request(
        "POST",
        "/api/entrar",
        [("X-Forwarded-For", valor) for valor in encaminhados],
        transport=Transporte(par),
    )


def test_sem_proxy_declarado_o_ip_e_o_do_par():
    assert ip_do_pedido(pedido("198.51.100.9", "203.0.113.5"), frozenset()) == "198.51.100.9"


def test_o_proxy_que_anexa_entrega_o_ultimo_salto():
    # Why: the common reverse proxy appends what it saw, so position zero is text the client
    # wrote and only the last entry is the address that the trusted hop talked to.
    # Por que: o proxy reverso comum anexa o que viu, então a posição zero é texto que o
    # cliente escreveu e só a última entrada é o endereço com quem o salto confiável falou.
    assert ip_do_pedido(pedido("127.0.0.1", "203.0.113.5, 198.51.100.9"), PROXY) == "198.51.100.9"


def test_o_proxy_que_substitui_entrega_a_unica_entrada():
    assert ip_do_pedido(pedido("127.0.0.1", "198.51.100.9"), PROXY) == "198.51.100.9"


def test_a_segunda_linha_do_cabecalho_nao_e_perdida():
    assert ip_do_pedido(pedido("127.0.0.1", "203.0.113.5", "198.51.100.9"), PROXY) == "198.51.100.9"


def test_os_saltos_de_proxies_declarados_sao_pulados():
    declarados = frozenset({"127.0.0.1", "198.51.100.2"})
    encaminhado = "203.0.113.5, 198.51.100.9, 198.51.100.2"
    assert ip_do_pedido(pedido("127.0.0.1", encaminhado), declarados) == "198.51.100.9"


def test_o_valor_que_nao_e_ip_cai_no_par():
    assert ip_do_pedido(pedido("127.0.0.1", "nao-e-ip"), PROXY) == "127.0.0.1"


def test_o_cabecalho_ausente_cai_no_par():
    assert ip_do_pedido(pedido("127.0.0.1"), PROXY) == "127.0.0.1"


def test_o_ipv6_encaminhado_vira_forma_canonica():
    # Why: two spellings of one address would be two keys in the block map, that is two blocks
    # for the attacker to spend instead of one.
    # Por que: duas grafias de um mesmo endereço seriam duas chaves no mapa de bloqueio, ou
    # seja, dois bloqueios para o atacante gastar em vez de um.
    assert ip_do_pedido(pedido("127.0.0.1", "2001:DB8:0:0:0:0:0:1"), PROXY) == "2001:db8::1"
