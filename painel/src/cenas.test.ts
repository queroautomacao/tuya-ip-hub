// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  CODIGOS_CENAS,
  ajustaveis,
  comCenas,
  corpoDeCenas,
  itemDoDp,
  lerCena,
  lerItemDoMapa,
  lerLeituraDeCenas,
  lerSnapshot,
  nomeValido,
  prepararEspera,
  prepararValor,
  textoDoValor,
  valorPadrao,
  type Cena,
  type ItemDoMapa,
} from "./cenas.ts";

const VOLUME: ItemDoMapa = {
  dpid: 101,
  zona: 1,
  funcao: "volume",
  tipo: "value",
  sentido: "rw",
  valores: [],
};
const PLAY: ItemDoMapa = { ...VOLUME, dpid: 102, funcao: "play", tipo: "bool" };
const PRESET: ItemDoMapa = {
  ...VOLUME,
  dpid: 103,
  funcao: "preset",
  tipo: "enum",
  sentido: "envio",
  valores: ["cmd1", "cmd2"],
};
const ONLINE: ItemDoMapa = { ...VOLUME, dpid: 104, funcao: "online", tipo: "bool", sentido: "reporte" };
const TOCANDO: ItemDoMapa = {
  ...VOLUME,
  dpid: 105,
  funcao: "tocando",
  tipo: "string",
  sentido: "reporte",
};
const ENTRADA: ItemDoMapa = { ...VOLUME, dpid: 141, funcao: "entrada", tipo: "enum", valores: [] };
const CENA: ItemDoMapa = {
  dpid: 131,
  zona: 0,
  funcao: "cena",
  tipo: "enum",
  sentido: "envio",
  valores: ["cena1", "cena2"],
};
const GRUPO: ItemDoMapa = {
  dpid: 132,
  zona: 0,
  funcao: "grupo",
  tipo: "enum",
  sentido: "rw",
  valores: ["solo", "grupo1"],
};

const MAPA = [VOLUME, PLAY, PRESET, ONLINE, TOCANDO, ENTRADA, CENA, GRUPO];

function cenaDe(parcial: Partial<Cena> = {}): Cena {
  return {
    numero: 1,
    nome: "Filme",
    em_curso: false,
    passos: [{ dpid: 101, valor: 30, espera_ms: 0 }],
    ...parcial,
  };
}

test("lerCena takes a scene and refuses one outside the contract (aceita uma cena e recusa uma fora do contrato)", () => {
  const bruto = {
    numero: 2,
    nome: "Festa",
    em_curso: true,
    passos: [{ dpid: 101, valor: 30, espera_ms: 250 }],
  };
  const lida = lerCena(bruto);
  assert.ok(lida !== null);
  assert.equal(lida.em_curso, true);
  assert.equal(lida.passos[0]?.espera_ms, 250);
  assert.equal(lerCena({ ...bruto, numero: "2" }), null);
  assert.equal(lerCena({ ...bruto, passos: [{ dpid: 101, espera_ms: 0 }] }), null);
  assert.equal(lerCena({ ...bruto, passos: [{ dpid: "101", valor: 1, espera_ms: 0 }] }), null);
  assert.equal(lerCena({ ...bruto, nome: null }), null);
});

test("lerLeituraDeCenas carries the ceilings the daemon fixes (leva os tetos que o daemon fixa)", () => {
  const bruto = { cenas: [], maximo: 8, passos_maximos: 32, espera_maxima_ms: 30000 };
  assert.equal(lerLeituraDeCenas(bruto)?.maximo, 8);
  assert.equal(lerLeituraDeCenas({ ...bruto, maximo: "8" }), null);
  assert.equal(lerLeituraDeCenas({ ...bruto, passos_maximos: undefined }), null);
});

test("lerItemDoMapa refuses a type or a direction outside section 8 (recusa tipo ou sentido fora da seção 8)", () => {
  assert.equal(lerItemDoMapa(VOLUME)?.dpid, 101);
  assert.equal(lerItemDoMapa({ ...VOLUME, tipo: "numero" }), null);
  assert.equal(lerItemDoMapa({ ...VOLUME, sentido: "escrita" }), null);
  assert.equal(lerItemDoMapa({ ...VOLUME, valores: [1] }), null);
  assert.equal(lerSnapshot({ dps: { "101": 20 }, mapa: MAPA })?.mapa.length, MAPA.length);
  assert.equal(lerSnapshot({ dps: [], mapa: MAPA }), null);
  assert.equal(lerSnapshot({ dps: {}, mapa: [{ dpid: 1 }] }), null);
});

// Why: section 8, a report is only ever born of real state, so a step never writes one; and a
// scene that started a scene would be a loop written in data.
// Por que: seção 8, um report só nasce de estado real, então um passo nunca escreve um; e uma
// cena que disparasse uma cena seria um laço escrito em dado.
test("ajustaveis never offers a report or the scene itself (nunca oferece um report nem a própria cena)", () => {
  const oferecidos = ajustaveis(MAPA).map((item) => item.dpid);
  assert.deepEqual(oferecidos, [101, 102, 103, 141, 132]);
  assert.ok(!oferecidos.includes(104), "online");
  assert.ok(!oferecidos.includes(105), "tocando");
  assert.ok(!oferecidos.includes(131), "cena");
  assert.equal(itemDoDp(MAPA, 141)?.funcao, "entrada");
  assert.equal(itemDoDp(MAPA, 999), undefined);
});

test("prepararValor judges the value by the type the data point declares (julga o valor pelo tipo que o data point declara)", () => {
  assert.deepEqual(prepararValor(VOLUME, "40"), { ok: true, valor: 40 });
  for (const bruto of ["101", "-1", "abc", ""]) {
    assert.deepEqual(prepararValor(VOLUME, bruto), { ok: false, codigo: "cena_valor_invalido" });
  }
  assert.deepEqual(prepararValor(PLAY, "true"), { ok: true, valor: true });
  assert.deepEqual(prepararValor(PLAY, "false"), { ok: true, valor: false });
  assert.deepEqual(prepararValor(PLAY, "sim"), { ok: false, codigo: "cena_valor_invalido" });
  assert.deepEqual(prepararValor(PRESET, "cmd2"), { ok: true, valor: "cmd2" });
  assert.deepEqual(prepararValor(PRESET, "cmd9"), { ok: false, codigo: "cena_valor_invalido" });
  // Why: the inputs of a speaker come from the hardware (section 14, plm_support), so a
  // speaker that was offline when the scene was saved offers no list and the shape is all
  // that can be judged here.
  // Por que: as entradas de uma caixa vêm do hardware (seção 14, plm_support), então uma caixa
  // offline na hora de salvar não oferece lista e a forma é tudo que dá para julgar aqui.
  assert.deepEqual(prepararValor(ENTRADA, "wifi"), { ok: true, valor: "wifi" });
  assert.deepEqual(prepararValor(ENTRADA, ""), { ok: false, codigo: "cena_valor_invalido" });
  assert.deepEqual(prepararValor(ENTRADA, "x".repeat(65)), {
    ok: false,
    codigo: "cena_valor_invalido",
  });
  // Why: a string data point is report only in the whole of section 8, so a step never sets one.
  // Por que: um data point string é só de report em toda a seção 8, então um passo nunca ajusta um.
  assert.deepEqual(prepararValor(TOCANDO, "Musica"), {
    ok: false,
    codigo: "cena_dp_somente_leitura",
  });
});

test("prepararEspera keeps the wait inside the band of the daemon (mantém a espera dentro da faixa do daemon)", () => {
  assert.deepEqual(prepararEspera("", 30000), { ok: true, valor: 0 });
  assert.deepEqual(prepararEspera("250", 30000), { ok: true, valor: 250 });
  for (const bruto of ["30001", "-1", "abc", "1.5"]) {
    assert.deepEqual(prepararEspera(bruto, 30000), { ok: false, codigo: "cena_espera_invalida" });
  }
});

test("valorPadrao and textoDoValor agree on what a control shows (concordam sobre o que um controle mostra)", () => {
  assert.equal(valorPadrao(VOLUME), 0);
  assert.equal(valorPadrao(PLAY), true);
  assert.equal(valorPadrao(PRESET), "cmd1");
  assert.equal(valorPadrao(ENTRADA), "");
  assert.equal(textoDoValor(true), "true");
  assert.equal(textoDoValor(false), "false");
  assert.equal(textoDoValor(30), "30");
  assert.equal(textoDoValor(null), "");
});

test("nomeValido measures the name the way the daemon does (mede o nome como o daemon mede)", () => {
  assert.equal(nomeValido("Filme"), true);
  assert.equal(nomeValido("N".repeat(40)), true);
  assert.equal(nomeValido("N".repeat(41)), false);
  // Why: the daemon counts code points, the way python len does, and String.length counts
  // UTF-16 units, so an astral character would count twice here and once there.
  // Por que: o daemon conta pontos de código, como o len do python, e o String.length conta
  // unidades UTF-16, então um caractere astral contaria duas vezes aqui e uma lá.
  assert.equal(nomeValido("\u{1d11e}".repeat(40)), true);
  assert.equal(nomeValido("\u{1d11e}".repeat(41)), false);
  // Why: a control character travels to the bridge inside the JSON of DP 134, so it is
  // refused where it is typed.
  // Por que: um caractere de controle viaja para a ponte dentro do JSON do DP 134, entao
  // ele e recusado onde e digitado.
  assert.equal(nomeValido("Filme\u0000"), false);
  assert.equal(nomeValido("Filme\u007f"), false);
  assert.equal(nomeValido("Filme\u001b"), false);
});

// Why: the POSITION of a scene is its number, so an erased scene keeps its slot; a list that
// came back shorter would move scene 3 into slot 2 in every automation already built.
// Por que: a POSIÇÃO de uma cena é o número dela, então uma cena apagada mantém a vaga; uma
// lista que voltasse mais curta moveria a cena 3 para a vaga 2 em toda automação já montada.
test("corpoDeCenas and comCenas keep the position of a scene (mantêm a posição de uma cena)", () => {
  const cenas = [cenaDe({ numero: 1, nome: "", passos: [] }), cenaDe({ numero: 2, nome: "Festa" })];
  assert.deepEqual(corpoDeCenas(cenas), [
    { nome: "", passos: [] },
    { nome: "Festa", passos: [{ dpid: 101, valor: 30, espera_ms: 0 }] },
  ]);
  const oito = comCenas([cenaDe()], 8);
  assert.equal(oito.length, 8);
  assert.deepEqual(
    oito.map((cena) => cena.numero),
    [1, 2, 3, 4, 5, 6, 7, 8],
  );
  assert.equal(oito[1]?.nome, "");
  assert.deepEqual(oito[7]?.passos, []);
});

test("every code of a scene has a phrase in both dictionaries (todo código de cena tem frase nos dois dicionários)", () => {
  for (const idioma of ["pt", "en"]) {
    const caminho = new URL(`./i18n/${idioma}.json`, import.meta.url);
    const textos = JSON.parse(readFileSync(caminho, "utf-8")) as Record<string, string>;
    for (const codigo of CODIGOS_CENAS) {
      const frase = textos[`erro_${codigo}`];
      assert.equal(typeof frase, "string", `${idioma}.erro_${codigo}`);
      assert.ok(frase.trim().length > 0, `${idioma}.erro_${codigo}`);
    }
  }
});

// Why: a step is one line that reads in the order it happens, and on a narrow screen it wraps
// instead of pushing the card sideways.
// Por que: um passo é uma linha que se lê na ordem em que acontece, e numa tela estreita ela
// quebra em vez de empurrar o cartão para o lado.
test("a step wraps instead of scrolling sideways (um passo quebra em vez de rolar de lado)", () => {
  const css = readFileSync(new URL("./estilos-cenas.css", import.meta.url), "utf-8");
  const inicio = css.indexOf(".passo {");
  assert.ok(inicio > 0, "no rule for a step");
  const bloco = css.slice(inicio, css.indexOf("}", inicio));
  assert.ok(bloco.includes("flex-wrap: wrap"), "flex-wrap");
});
