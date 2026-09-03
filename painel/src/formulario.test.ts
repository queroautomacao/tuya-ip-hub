// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { test } from "node:test";

import type { Achado, Campo, Equipamento, ItemCatalogo } from "./equipamentos.ts";
import {
  LIMITE_TEXTO,
  VAZIO,
  formularioDe,
  imprimivel,
  ipLiteral,
  ofertaDoAchado,
  padroes,
  validarCadastro,
  type Formulario,
} from "./formulario.ts";

function itemDe(parcial: Partial<ItemCatalogo> = {}): ItemCatalogo {
  return {
    tipo: "projetor_exemplo",
    categoria: "projetor",
    motor: "nativo",
    auth: "codigo",
    capacidades: ["ligar", "desligar"],
    rotulo: { pt: "Projetor de exemplo", en: "Example projector" },
    textos: { pt: { descricao: "Um projetor" }, en: { descricao: "A projector" } },
    config_campos: [],
    ...parcial,
  };
}

function campoDe(parcial: Partial<Campo> & { nome: string }): Campo {
  return { tipo: "texto", obrigatorio: false, padrao: "", ...parcial };
}

function formularioDeTeste(parcial: Partial<Formulario> = {}): Formulario {
  const base = { tipo: "projetor_exemplo", identidade: "uuid-1", ip: "192.0.2.10" };
  return { ...VAZIO, ...base, ...parcial };
}

function achadoDe(parcial: Partial<Achado> = {}): Achado {
  return {
    tipo: "projetor_exemplo",
    identidade: "uuid-1",
    ip: "192.0.2.10",
    porta: 4352,
    descricao: "servidor",
    ja_cadastrado: false,
    ...parcial,
  };
}

// Why: section 9 says the ip of any route that talks to a device is an IP literal, so
// the hub never becomes a proxy into the LAN; these are the attacks on that rule.
// Por que: a seção 9 diz que o ip de toda rota que fala com aparelho é um IP literal,
// para o hub nunca virar proxy da LAN; estes são os ataques a essa regra.
test("ipLiteral refuses a name, a URL, a port and a zone id (recusa nome, URL, porta e zone id)", () => {
  for (const bom of [
    "192.0.2.10",
    "0.0.0.0",
    "255.255.255.255",
    "::1",
    "::",
    "fe80::1",
    "2001:db8::8a2e:370:7334",
    "::ffff:192.0.2.10",
  ]) {
    assert.equal(ipLiteral(bom), true, bom);
  }
  for (const mau of [
    "",
    " ",
    "aparelho.local",
    "localhost",
    "example.com",
    "http://192.0.2.10",
    "https://192.0.2.10/status",
    "192.0.2.10:8080",
    "[::1]",
    "[::1]:80",
    "fe80::1%eth0",
    "192.0.2.10 ",
    " 192.0.2.10",
    "192.0.2.1 ",
    "192.0.2",
    "192.0.2.10.1",
    "010.1.1.1",
    "256.1.1.1",
    "1:2:3:4:5:6:7:8:9",
    "1::2::3",
    ":::",
    "192.0.2.10/24",
    "1".repeat(400),
    "0:".repeat(200),
  ]) {
    assert.equal(ipLiteral(mau), false, JSON.stringify(mau));
  }
});

test("validarCadastro refuses the form before it costs a request (recusa o formulário antes de custar requisição)", () => {
  const item = itemDe({
    config_campos: [
      campoDe({ nome: "porta", tipo: "inteiro", padrao: "4352" }),
      campoDe({ nome: "sala", obrigatorio: true }),
      campoDe({ nome: "senha", tipo: "segredo" }),
    ],
  });
  const catalogo = [item];
  const base = formularioDeTeste({
    nome: "Projetor",
    campos: { porta: "4352", sala: "auditorio" },
  });
  assert.deepEqual(validarCadastro({ ...base, tipo: "nao_existe" }, catalogo), {
    ok: false,
    codigo: "tipo_desconhecido",
    campo: "tipo",
  });
  assert.deepEqual(validarCadastro({ ...base, identidade: "  " }, catalogo), {
    ok: false,
    codigo: "campo_invalido",
    campo: "identidade",
  });
  for (const ip of ["", "aparelho.local", "192.0.2.10:4352", "http://192.0.2.10"]) {
    assert.deepEqual(validarCadastro({ ...base, ip }, catalogo), {
      ok: false,
      codigo: "ip_invalido",
      campo: "ip",
    });
  }
  assert.deepEqual(validarCadastro({ ...base, campos: { porta: "4352" } }, catalogo), {
    ok: false,
    codigo: "campo_invalido",
    campo: "sala",
  });
  assert.deepEqual(
    validarCadastro({ ...base, campos: { ...base.campos, porta: "4352 ou 4353" } }, catalogo),
    { ok: false, codigo: "campo_invalido", campo: "porta" },
  );
});

test("a valid form sends only the declared fields, trimmed (um formulário válido envia só os campos declarados, aparados)", () => {
  const item = itemDe({
    config_campos: [
      campoDe({ nome: "porta", tipo: "inteiro", padrao: "4352" }),
      campoDe({ nome: "senha", tipo: "segredo" }),
    ],
  });
  const validacao = validarCadastro(
    formularioDeTeste({
      identidade: "  uuid-1  ",
      nome: "  Projetor  ",
      ip: "  192.0.2.10  ",
      // Why: an empty secret is left out so the daemon keeps the credential it already
      // has, and a field the manifest does not declare never reaches the daemon.
      // Por que: um segredo vazio fica de fora para o daemon manter a credencial que já
      // tem, e um campo que o manifesto não declara nunca chega ao daemon.
      campos: { porta: " 4352 ", senha: "", inventado: "x" },
    }),
    [item],
  );
  assert.equal(validacao.ok, true);
  assert.deepEqual(validacao.ok && validacao.corpo, {
    tipo: item.tipo,
    identidade: "uuid-1",
    nome: "Projetor",
    ip: "192.0.2.10",
    campos: { porta: "4352" },
  });
});

// Why: the daemon stores a device credential verbatim, so trimming a password with a
// leading or a trailing space corrupts it, and the device then tells the operator the
// credential is wrong about bytes it never received.
// Por que: o daemon guarda a credencial do aparelho literal, então aparar uma senha com
// espaço na ponta a corrompe, e o aparelho depois diz ao operador que a credencial está
// errada sobre bytes que ele nunca recebeu.
test("a SEGREDO travels exactly as it was typed (um SEGREDO viaja exatamente como foi digitado)", () => {
  const item = itemDe({
    config_campos: [
      campoDe({ nome: "porta", tipo: "inteiro", padrao: "4352" }),
      campoDe({ nome: "senha", tipo: "segredo" }),
    ],
  });
  const validacao = validarCadastro(
    formularioDeTeste({ campos: { porta: " 4352 ", senha: "  se nha  " } }),
    [item],
  );
  assert.equal(validacao.ok && validacao.corpo.campos.senha, "  se nha  ");
  assert.equal(validacao.ok && validacao.corpo.campos.porta, "4352");
  // Why: a credential of nothing but spaces is still a credential the operator typed.
  // Por que: uma credencial só de espaços ainda é uma credencial que o operador digitou.
  const espacos = validarCadastro(formularioDeTeste({ campos: { senha: "   " } }), [item]);
  assert.equal(espacos.ok && espacos.corpo.campos.senha, "   ");
});

// Why: the daemon refuses a text over 200 code points or with a control character with
// campo_invalido, and the operator has to be told which field, not just that one is bad.
// Por que: o daemon recusa texto acima de 200 pontos de código ou com caractere de
// controle com campo_invalido, e o operador precisa saber qual campo, não só que há um.
test("the form enforces the limits of the daemon and names the field (o formulário impõe os limites do daemon e nomeia o campo)", () => {
  const item = itemDe({ config_campos: [campoDe({ nome: "sala" })] });
  const longo = "a".repeat(LIMITE_TEXTO + 1);
  assert.deepEqual(validarCadastro(formularioDeTeste({ nome: longo }), [item]), {
    ok: false,
    codigo: "campo_invalido",
    campo: "nome",
  });
  assert.deepEqual(validarCadastro(formularioDeTeste({ identidade: longo }), [item]), {
    ok: false,
    codigo: "campo_invalido",
    campo: "identidade",
  });
  assert.deepEqual(validarCadastro(formularioDeTeste({ nome: "sala\u00A0um" }), [item]), {
    ok: false,
    codigo: "campo_invalido",
    campo: "nome",
  });
  assert.deepEqual(validarCadastro(formularioDeTeste({ campos: { sala: longo } }), [item]), {
    ok: false,
    codigo: "campo_invalido",
    campo: "sala",
  });
  assert.deepEqual(
    validarCadastro(formularioDeTeste({ campos: { sala: "linha\r\nnova" } }), [item]),
    { ok: false, codigo: "campo_invalido", campo: "sala" },
  );
  // Why: the limit is in code points, the way python len counts, so an astral character
  // must not count twice and cost the operator a field the daemon would accept.
  // Por que: o limite é em pontos de código, como o len do python conta, então um
  // caractere astral não pode contar duas vezes e custar um campo que o daemon aceitaria.
  const astral = "\u{1F50A}".repeat(LIMITE_TEXTO);
  assert.equal(validarCadastro(formularioDeTeste({ nome: astral }), [item]).ok, true);
  assert.equal(imprimivel("sala 1"), true);
  assert.equal(imprimivel(""), true);
  assert.equal(imprimivel("sala\u00A0um"), false);
});

// Why: an update that omits a secret keeps the one the daemon stores, so the operator
// fixes an address without typing the device password again, and erasing one is an
// explicit ask that travels as the empty string.
// Por que: uma atualização que omite um segredo mantém o que o daemon guarda, então o
// operador corrige um endereço sem digitar a senha do aparelho de novo, e apagar uma é um
// pedido explícito que viaja como texto vazio.
test("an edit keeps the stored credential and erases it only when asked (a edição mantém a credencial guardada e só a apaga quando pedem)", () => {
  const item = itemDe({
    config_campos: [
      campoDe({ nome: "porta", tipo: "inteiro", padrao: "4352" }),
      campoDe({ nome: "senha", tipo: "segredo", obrigatorio: true }),
    ],
  });
  const equipamento: Equipamento = {
    identidade: "uuid-1",
    tipo: item.tipo,
    nome: "Projetor",
    ip: "192.0.2.10",
    campos: { porta: "4353" },
    segredos_definidos: ["senha"],
    estado: {
      online: true,
      ligado: null,
      volume: null,
      mudo: null,
      fonte: null,
      fontes: [],
      tocando: null,
      detalhe: "",
    },
  };
  const formulario = formularioDe(equipamento, item);
  assert.deepEqual(formulario.campos, { porta: "4353" });
  assert.deepEqual(formulario.apagar, []);
  const mantido = validarCadastro(
    { ...formulario, ip: "192.0.2.11" },
    [item],
    equipamento.segredos_definidos,
  );
  assert.equal(mantido.ok, true);
  assert.deepEqual(mantido.ok && mantido.corpo.campos, { porta: "4353" });
  // Why: with nothing stored, an obligatory secret left blank is still a refusal.
  // Por que: sem nada guardado, um segredo obrigatório em branco ainda é recusa.
  assert.deepEqual(validarCadastro(formulario, [item]), {
    ok: false,
    codigo: "campo_invalido",
    campo: "senha",
  });
  const opcional = itemDe({ config_campos: [campoDe({ nome: "senha", tipo: "segredo" })] });
  const apagado = validarCadastro(
    { ...formulario, campos: {}, apagar: ["senha"] },
    [opcional],
    ["senha"],
  );
  assert.deepEqual(apagado.ok && apagado.corpo.campos, { senha: "" });
});

// Why: the identity is the key of a registration and the sweep is the only place it could
// come from, so a device that answered without one gets a note and no button; a prefilled
// form the operator cannot finish is worse than being told to do it by hand.
// Por que: a identidade é a chave do cadastro e a varredura é o único lugar de onde ela
// viria, então um aparelho que respondeu sem identidade ganha um aviso e nenhum botão; um
// formulário preenchido que o operador não termina é pior que dizer para fazer à mão.
test("the sweep offers no prefill without an identity (a varredura não oferece preenchimento sem identidade)", () => {
  assert.equal(ofertaDoAchado(achadoDe()), "cadastrar");
  assert.equal(ofertaDoAchado(achadoDe({ ja_cadastrado: true })), "ja_cadastrado");
  assert.equal(ofertaDoAchado(achadoDe({ tipo: "" })), "sem_tipo");
  assert.equal(ofertaDoAchado(achadoDe({ identidade: "" })), "sem_identidade");
  assert.equal(ofertaDoAchado(achadoDe({ identidade: "", ja_cadastrado: true })), "ja_cadastrado");
});

test("padroes offers the manifest default and never a secret (padroes oferece o padrão do manifesto e nunca um segredo)", () => {
  const item = itemDe({
    config_campos: [
      campoDe({ nome: "porta", tipo: "inteiro", padrao: "4352" }),
      campoDe({ nome: "senha", tipo: "segredo", padrao: "" }),
    ],
  });
  assert.deepEqual(padroes(item), { porta: "4352" });
  assert.deepEqual(padroes(undefined), {});
});
