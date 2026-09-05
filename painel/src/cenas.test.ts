// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  CODIGOS_CENAS,
  acoesDe,
  comCenas,
  corpoDeCenas,
  especieDe,
  lerCena,
  lerLeituraDeCenas,
  nomeValido,
  opcoesDe,
  prepararEspera,
  prepararIntervalo,
  prepararValor,
  textoDoValor,
  ultimaEmUso,
  valorPadrao,
  type Cena,
} from "./cenas.ts";
import type { Equipamento, EstadoEquipamento, ItemCatalogo } from "./equipamentos.ts";

const ACOES = [
  "ligar",
  "desligar",
  "volume",
  "mudo",
  "fonte",
  "tocar",
  "pausar",
  "proxima",
  "anterior",
  "tecla",
  "atalho",
  "modo",
  "vento",
  "temperatura",
  "comando_extra",
  "grupo",
];

function estadoDe(parcial: Partial<EstadoEquipamento> = {}): EstadoEquipamento {
  return {
    online: true,
    ligado: null,
    volume: null,
    mudo: null,
    fonte: null,
    fontes: [],
    reproduzindo: null,
    tocando: null,
    temperatura: null,
    modo: null,
    vento: null,
    detalhe: "",
    ...parcial,
  };
}

function itemDe(parcial: Partial<ItemCatalogo> = {}): ItemCatalogo {
  return {
    tipo: "caixa",
    categoria: "multiroom",
    motor: "nativo",
    auth: "nenhuma",
    capacidades: ["volume", "mudo", "fonte", "tocar", "pausar", "agrupar"],
    teclas: [],
    modos: [],
    ventos: [],
    produto: "av",
    template: "au",
    rotulo: { pt: "Caixa", en: "Speaker" },
    textos: { pt: {}, en: {} },
    config_campos: [],
    ...parcial,
  };
}

function equipamentoDe(parcial: Partial<Equipamento> = {}): Equipamento {
  return {
    identidade: "uuid-1",
    tipo: "caixa",
    nome: "Sala",
    ip: "192.0.2.10",
    campos: {},
    segredos_definidos: [],
    listas: {},
    licenca: null,
    numero: null,
    estado: estadoDe({ fontes: ["wifi", "line-in"] }),
    ...parcial,
  };
}

const PASSO = { equipamento: "uuid-1", acao: "volume", valor: 30, espera_ms: null };

test("lerCena takes a scene and refuses one outside the contract (aceita uma cena e recusa uma fora do contrato)", () => {
  const cena = lerCena({
    numero: 1,
    nome: "Cinema",
    intervalo_ms: 1000,
    em_curso: false,
    passos: [PASSO, { equipamento: "uuid-2", acao: "ligar", espera_ms: 500 }],
  });
  assert.deepEqual(cena, {
    numero: 1,
    nome: "Cinema",
    intervalo_ms: 1000,
    em_curso: false,
    passos: [PASSO, { equipamento: "uuid-2", acao: "ligar", valor: null, espera_ms: 500 }],
  });
  assert.equal(lerCena({ numero: "1", nome: "x", intervalo_ms: 1000, passos: [] }), null);
  assert.equal(lerCena({ numero: 1, nome: "x", intervalo_ms: 1000, passos: [{ acao: "ligar" }] }), null);
  assert.equal(lerCena({ numero: 1, nome: "x", intervalo_ms: 1000, passos: [{ ...PASSO, espera_ms: "1" }] }), null);
  assert.equal(lerCena({ numero: 1, nome: "x", intervalo_ms: 1000, passos: [{ ...PASSO, acao: 7 }] }), null);
});

test("lerLeituraDeCenas carries the ceilings and the actions the daemon fixes (leva os tetos e as ações que o daemon fixa)", () => {
  const leitura = lerLeituraDeCenas({
    cenas: [],
    maximo: 32,
    acoes: ACOES,
    passos_maximos: 64,
    espera_maxima_ms: 30000,
    intervalo_padrao_ms: 1000,
  });
  assert.deepEqual(leitura, {
    cenas: [],
    maximo: 32,
    acoes: ACOES,
    passos_maximos: 64,
    espera_maxima_ms: 30000,
    intervalo_padrao_ms: 1000,
  });
  assert.equal(lerLeituraDeCenas({ cenas: [], maximo: 32, passos_maximos: 64, espera_maxima_ms: 1, intervalo_padrao_ms: 1 }), null);
  assert.equal(lerLeituraDeCenas({ cenas: [], maximo: "32", acoes: [], passos_maximos: 64, espera_maxima_ms: 1, intervalo_padrao_ms: 1 }), null);
});

test("acoesDe offers the capabilities of the manifest and the group only to who groups (oferece as capacidades do manifesto e o grupo só a quem agrupa)", () => {
  assert.deepEqual(acoesDe(ACOES, itemDe()), ["volume", "mudo", "fonte", "tocar", "pausar", "grupo"]);
  const projetor = itemDe({ categoria: "projetor", capacidades: ["ligar", "desligar", "tecla", "agrupar"] });
  assert.deepEqual(acoesDe(ACOES, projetor), ["ligar", "desligar", "tecla"]);
  assert.deepEqual(acoesDe(ACOES, undefined), []);
});

test("especieDe tells how the value of each action is typed (diz como o valor de cada ação é digitado)", () => {
  assert.equal(especieDe("ligar"), "nenhum");
  assert.equal(especieDe("proxima"), "nenhum");
  assert.equal(especieDe("volume"), "numero");
  assert.equal(especieDe("temperatura"), "numero");
  assert.equal(especieDe("mudo"), "logico");
  assert.equal(especieDe("grupo"), "grupo");
  for (const acao of ["tecla", "vento", "modo", "fonte", "atalho"]) assert.equal(especieDe(acao), "escolha");
  assert.equal(especieDe("comando_extra"), "texto");
});

test("opcoesDe reads the lists of the registration, the words of the manifest, or the inputs the driver read (lê as listas do cadastro, as palavras do manifesto, ou as entradas que o driver leu)", () => {
  const item = itemDe({ capacidades: ["fonte", "atalho", "modo", "tecla", "vento"], teclas: ["ok", "menu"], ventos: ["auto", "alto"] });
  const equipamento = equipamentoDe({
    listas: { entradas: [{ rotulo: "HDMI 1", valor: "hdmi1" }], atalhos: [{ rotulo: "Netflix", valor: "app:netflix" }], modos: [{ rotulo: "Cinema", valor: "movie" }] },
  });
  assert.deepEqual(opcoesDe("fonte", item, equipamento), [{ valor: "hdmi1", rotulo: "HDMI 1" }]);
  assert.deepEqual(opcoesDe("atalho", item, equipamento), [{ valor: "app:netflix", rotulo: "Netflix" }]);
  assert.deepEqual(opcoesDe("modo", item, equipamento), [{ valor: "movie", rotulo: "Cinema" }]);
  assert.deepEqual(opcoesDe("tecla", item, equipamento), [{ valor: "ok", rotulo: "ok" }, { valor: "menu", rotulo: "menu" }]);
  assert.deepEqual(opcoesDe("vento", item, equipamento), [{ valor: "auto", rotulo: "auto" }, { valor: "alto", rotulo: "alto" }]);
  // Why: with no list the inputs the driver read stand in, so a speaker offers what it has.
  // Por que: sem lista as entradas que o driver leu entram no lugar, então uma caixa oferece o que tem.
  assert.deepEqual(opcoesDe("fonte", item, equipamentoDe()), [{ valor: "wifi", rotulo: "wifi" }, { valor: "line-in", rotulo: "line-in" }]);
  const ar = itemDe({ produto: "ar", categoria: "ar_condicionado", capacidades: ["modo"], modos: ["frio", "seco"] });
  assert.deepEqual(opcoesDe("modo", ar, equipamentoDe()), [{ valor: "frio", rotulo: "frio" }, { valor: "seco", rotulo: "seco" }]);
  assert.deepEqual(opcoesDe("volume", item, equipamento), []);
});

test("prepararValor judges the value by the action of section 6 (julga o valor pela ação da seção 6)", () => {
  assert.deepEqual(prepararValor("ligar", ""), { ok: true, valor: null });
  assert.deepEqual(prepararValor("volume", " 30 "), { ok: true, valor: 30 });
  assert.equal(prepararValor("volume", "101").ok, false);
  assert.equal(prepararValor("volume", "-1").ok, false);
  assert.deepEqual(prepararValor("temperatura", "22"), { ok: true, valor: 22 });
  assert.equal(prepararValor("temperatura", "15").ok, false);
  assert.equal(prepararValor("temperatura", "31").ok, false);
  assert.deepEqual(prepararValor("mudo", "true"), { ok: true, valor: true });
  assert.equal(prepararValor("mudo", "sim").ok, false);
  assert.deepEqual(prepararValor("tecla", "canal_mais"), { ok: true, valor: "canal_mais" });
  assert.equal(prepararValor("tecla", "voar").ok, false);
  assert.deepEqual(prepararValor("vento", "alto"), { ok: true, valor: "alto" });
  assert.equal(prepararValor("vento", "turbo").ok, false);
  assert.deepEqual(prepararValor("grupo", ""), { ok: true, valor: "" });
  assert.deepEqual(prepararValor("grupo", "uuid-2"), { ok: true, valor: "uuid-2" });
  assert.deepEqual(prepararValor("fonte", "hdmi1"), { ok: true, valor: "hdmi1" });
  assert.equal(prepararValor("fonte", "").ok, false);
  assert.equal(prepararValor("fonte", "a\u0000b").ok, false);
  assert.equal(prepararValor("comando_extra", "x".repeat(65)).ok, false);
  assert.deepEqual(prepararValor("modo", "frio"), { ok: true, valor: "frio" });
  assert.deepEqual(prepararValor("modo", "movie"), { ok: true, valor: "movie" });
  const recusa = prepararValor("volume", "abc");
  assert.equal(recusa.ok, false);
  assert.equal(!recusa.ok && recusa.codigo, "cena_valor_invalido");
});

test("prepararEspera keeps the wait inside the band of the daemon (mantém a espera dentro da faixa do daemon)", () => {
  assert.deepEqual(prepararEspera("", 30000), { ok: true, valor: null });
  assert.deepEqual(prepararEspera("0", 30000), { ok: true, valor: 0 });
  assert.deepEqual(prepararEspera(" 2500 ", 30000), { ok: true, valor: 2500 });
  assert.equal(prepararEspera("30001", 30000).ok, false);
  assert.equal(prepararEspera("-1", 30000).ok, false);
  assert.equal(prepararEspera("1.5", 30000).ok, false);
});

test("prepararIntervalo refuses an empty interval (recusa um intervalo vazio)", () => {
  assert.equal(prepararIntervalo("", 30000).ok, false);
  assert.deepEqual(prepararIntervalo("1000", 30000), { ok: true, valor: 1000 });
  const recusa = prepararIntervalo("x", 30000);
  assert.equal(!recusa.ok && recusa.codigo, "cena_intervalo_invalido");
});

test("valorPadrao and textoDoValor agree on what a control shows (concordam sobre o que um controle mostra)", () => {
  assert.equal(valorPadrao("ligar", []), null);
  assert.equal(valorPadrao("volume", []), 0);
  assert.equal(valorPadrao("temperatura", []), 22);
  assert.equal(valorPadrao("mudo", []), true);
  assert.equal(valorPadrao("grupo", []), "");
  assert.equal(valorPadrao("fonte", [{ valor: "hdmi1", rotulo: "HDMI 1" }]), "hdmi1");
  assert.equal(valorPadrao("fonte", []), "");
  assert.equal(textoDoValor(true), "true");
  assert.equal(textoDoValor(null), "");
  assert.equal(textoDoValor(30), "30");
});

test("nomeValido measures the name the way the daemon does (mede o nome como o daemon mede)", () => {
  assert.equal(nomeValido("a".repeat(40)), true);
  assert.equal(nomeValido("a".repeat(41)), false);
  // Why: an astral character is one code point for the daemon and two UTF-16 units here.
  // Por que: um caractere astral é um ponto de código para o daemon e duas unidades UTF-16 aqui.
  assert.equal(nomeValido("😀".repeat(40)), true);
  assert.equal(nomeValido("😀".repeat(41)), false);
  assert.equal(nomeValido("Cinema\nnoite"), false);
  assert.equal(nomeValido(""), true);
});

test("corpoDeCenas and comCenas keep the position of a scene (mantêm a posição de uma cena)", () => {
  const cenas: Cena[] = [
    { numero: 1, nome: "Cinema", intervalo_ms: 1000, em_curso: true, passos: [PASSO] },
    { numero: 2, nome: "", intervalo_ms: 1000, em_curso: false, passos: [] },
  ];
  assert.deepEqual(corpoDeCenas(cenas), [
    { nome: "Cinema", intervalo_ms: 1000, passos: [PASSO] },
    { nome: "", intervalo_ms: 1000, passos: [] },
  ]);
  const cheias = comCenas(cenas, 32, 1000);
  assert.equal(cheias.length, 32);
  assert.equal(cheias[0]?.nome, "Cinema");
  assert.equal(cheias[31]?.numero, 32);
  assert.deepEqual(cheias[31]?.passos, []);
  assert.equal(ultimaEmUso(cheias), 1);
  assert.equal(ultimaEmUso([]), 0);
});

test("every code of a scene has a phrase in both dictionaries (todo código de cena tem frase nos dois dicionários)", () => {
  for (const idioma of ["pt", "en"]) {
    const caminho = new URL(`./i18n/${idioma}.json`, import.meta.url);
    const textos = JSON.parse(readFileSync(caminho, "utf-8")) as Record<string, string>;
    for (const codigo of CODIGOS_CENAS) {
      assert.equal(typeof textos[`erro_${codigo}`], "string", `${idioma}: erro_${codigo}`);
    }
    for (const acao of ACOES) {
      assert.equal(typeof textos[`acao_${acao}`], "string", `${idioma}: acao_${acao}`);
    }
  }
});

test("a step wraps instead of scrolling sideways (um passo quebra em vez de rolar de lado)", () => {
  const css = readFileSync(new URL("./estilos-cenas.css", import.meta.url), "utf-8");
  assert.match(css, /\.passo\s*\{[^}]*flex-wrap:\s*wrap/s);
});
