# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The second factor against the vectors of the standard and against replay."""

import base64

from iphub import totp

# RFC 6238 publishes the ASCII secret "12345678901234567890" and the codes of a few
# instants; the file speaks base32, so the same secret is written the way a phone reads it.
SEGREDO_DO_RFC = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")


def test_os_codigos_do_rfc_6238_batem():
    """If this fails, no authenticator on earth agrees with this file."""
    # (instant, code) from the SHA1 table of the standard, cut to six digits.
    for instante, esperado in ((59, "287082"), (1111111109, "081804"), (1234567890, "005924")):
        assert totp.codigo_de(SEGREDO_DO_RFC, totp.passo_de(instante)) == esperado


def test_um_segredo_novo_e_base32_de_vinte_bytes():
    segredo = totp.gerar_segredo()
    assert segredo != totp.gerar_segredo()
    assert len(base64.b32decode(segredo + "=" * ((-len(segredo)) % 8))) == totp.SEGREDO_BYTES


def test_o_codigo_da_hora_e_aceito_e_o_passo_volta():
    segredo = totp.gerar_segredo()
    agora = 1_700_000_000.0
    codigo = totp.codigo_de(segredo, totp.passo_de(agora))
    assert totp.conferir(segredo, codigo, agora, ultimo_passo=0) == totp.passo_de(agora)


def test_um_relogio_meio_minuto_fora_ainda_entra_e_dois_minutos_fora_nao():
    segredo = totp.gerar_segredo()
    agora = 1_700_000_000.0
    for desvio in (-totp.PASSO_S, 0, totp.PASSO_S):
        codigo = totp.codigo_de(segredo, totp.passo_de(agora + desvio))
        assert totp.conferir(segredo, codigo, agora, ultimo_passo=0) is not None
    longe = totp.codigo_de(segredo, totp.passo_de(agora + 4 * totp.PASSO_S))
    assert totp.conferir(segredo, longe, agora, ultimo_passo=0) is None


def test_um_codigo_gasto_nao_entra_de_novo():
    """A code read over somebody's shoulder works for thirty seconds, unless it is spent."""
    segredo = totp.gerar_segredo()
    agora = 1_700_000_000.0
    codigo = totp.codigo_de(segredo, totp.passo_de(agora))
    passo = totp.conferir(segredo, codigo, agora, ultimo_passo=0)
    assert passo is not None
    assert totp.conferir(segredo, codigo, agora, ultimo_passo=passo) is None


def test_o_que_nao_e_um_codigo_e_recusado_sem_explodir():
    segredo = totp.gerar_segredo()
    agora = 1_700_000_000.0
    for informado in (None, 123456, "", "12345", "1234567", "abcdef", "12 34 56"):
        assert totp.conferir(segredo, informado, agora, ultimo_passo=0) is None
    # A stored secret that is not base32 answers no instead of raising into the route.
    assert totp.conferir("nao é base32!", "123456", agora, ultimo_passo=0) is None
    assert totp.codigo_de("nao é base32!", 1) == ""


def test_espacos_e_minusculas_do_teclado_do_celular_sao_aceitos():
    """A person types what the phone shows, and the phone shows three digits and a space."""
    segredo = totp.gerar_segredo()
    agora = 1_700_000_000.0
    codigo = totp.codigo_de(segredo, totp.passo_de(agora))
    espacado = f"{codigo[:3]} {codigo[3:]}"
    assert totp.conferir(segredo, espacado, agora, ultimo_passo=0) is not None


def test_a_uri_leva_o_segredo_o_emissor_e_a_conta():
    uri = totp.uri("ABCDEFGH", "Casa da Praia")
    assert uri.startswith("otpauth://totp/")
    assert "secret=ABCDEFGH" in uri
    assert "issuer=Tuya%20IP%20Hub" in uri
    assert "Casa%20da%20Praia" in uri
    assert "digits=6" in uri and "period=30" in uri
