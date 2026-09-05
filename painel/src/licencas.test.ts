// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { Equipamento, EstadoEquipamento, ItemCatalogo } from "./equipamentos.ts";
import {
  CODIGOS_LICENCAS,
  SOLO,
  bitDe,
  comIdentidade,
  comandoDe,
  controlesDoNumero,
  gruposPossiveis,
  idValido,
  lerLeituraDeLicencas,
  lerLicenca,
  lerNumero,
  lerSnapshot,
  licencasDe,
  onde,
  ordemDe,
  paresDe,
  podeAgrupar,
  prepararNivel,
  semIdentidade,
  tocando,
  type Licenca,
  type Numero,
} from "./licencas.ts";

function estadoDe(parcial: Partial<EstadoEquipamento> = {}): EstadoEquipamento {
  return {
    online: true,
    ligado: null,
    volume: 20,
    mudo: null,
    fonte: "wifi",
    fontes: ["wifi", "line-in"],
    reproduzindo: null,
    tocando: null,
    temperatura: null,
    modo: null,
    vento: null,
    detalhe: "",
    ...parcial,
  };
}

function numeroDe(parcial: Partial<Numero> = {}): Numero {
  return {
    numero: 1,
    identidade: "uuid-1",
    nome: "Sala",
    tipo: "multiroom_linkplay",
    papel: "",
    dps: { ligado: 101, nivel: 121 },
    estado: estadoDe(),
    ...parcial,
  };
}

function vazio(numero: number): Numero {
  return numeroDe({ numero, identidade: "", nome: "", tipo: "", estado: null, dps: { ligado: 100 + numero, nivel: 120 + numero } });
}

function licencaDe(parcial: Partial<Licenca> = {}): Licenca {
  return {
    id: "av1",
    produto: "av",
    nome: "Casa",
    uuid: "uuid-tuya",
    pid: "pid",
    chave_definida: true,
    capacidade: 12,
    numeros: [numeroDe(), numeroDe({ numero: 2, identidade: "uuid-2", nome: "Cozinha", dps: { ligado: 102, nivel: 122 } }), ...[3, 4].map(vazio)],
    grupo: SOLO,
    reports_do_dia: 3,
    ouvintes: 1,
    ...parcial,
  };
}

function itemDe(parcial: Partial<ItemCatalogo> = {}): ItemCatalogo {
  return {
    tipo: "multiroom_linkplay",
    categoria: "multiroom",
    motor: "nativo",
    auth: "nenhuma",
    capacidades: ["volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra"],
    teclas: [],
    modos: [],
    ventos: [],
    produto: "av",
    template: "au",
    rotulo: { pt: "Caixa", en: "Speaker" },
    textos: { pt: {}, en: {} },
    config_campos: [],
    sugestoes: [],
    ...parcial,
  };
}

function equipamentoDe(parcial: Partial<Equipamento> = {}): Equipamento {
  return {
    identidade: "uuid-1",
    tipo: "multiroom_linkplay",
    nome: "Sala",
    ip: "192.0.2.10",
    campos: {},
    segredos_definidos: [],
    listas: { entradas: [{ rotulo: "Wi-Fi", valor: "wifi" }] },
    licenca: "av1",
    numero: 1,
    estado: estadoDe(),
    ...parcial,
  };
}

test("lerNumero reads a number and refuses one outside the contract (lê um número e recusa um fora do contrato)", () => {
  const bruto = { numero: 1, identidade: "uuid-1", nome: "Sala", tipo: "x", papel: "mestre", dps: { ligado: 101 }, estado: null };
  assert.deepEqual(lerNumero(bruto), { ...bruto, papel: "mestre" });
  assert.equal(lerNumero({ ...bruto, papel: "alheio" })?.papel, "alheio");
  assert.equal(lerNumero({ ...bruto, papel: "chefe" }), null);
  assert.equal(lerNumero({ ...bruto, dps: { ligado: "101" } }), null);
  assert.equal(lerNumero({ ...bruto, numero: "1" }), null);
  assert.equal(lerNumero({ ...bruto, estado: { online: "sim" } }), null);
});

test("lerLicenca never carries the chave, only whether one is defined (nunca leva a chave, só se há uma definida)", () => {
  const bruto = {
    id: "av1",
    produto: "av",
    nome: "Casa",
    uuid: "u",
    pid: "p",
    chave_definida: true,
    capacidade: 12,
    numeros: [],
    grupo: 0,
    reports_do_dia: 2,
    ouvintes: 0,
    chave: "segredo",
  };
  const licenca = lerLicenca(bruto);
  assert.ok(licenca !== null);
  assert.equal("chave" in licenca, false);
  assert.equal(licenca.chave_definida, true);
  assert.equal(lerLicenca({ ...bruto, produto: "tv" }), null);
  assert.equal(lerLicenca({ ...bruto, chave_definida: "sim" }), null);
  assert.equal(lerLicenca({ ...bruto, numeros: [{ numero: 1 }] }), null);
});

test("lerLeituraDeLicencas carries the capacities of the two products (leva as capacidades dos dois produtos)", () => {
  const leitura = lerLeituraDeLicencas({ licencas: [], produtos: { ar: 8, av: 12 }, reports_por_dia: 300, aviso_do_dia: 250 });
  assert.deepEqual(leitura, { licencas: [], produtos: { ar: 8, av: 12 }, reports_por_dia: 300, aviso_do_dia: 250 });
  assert.equal(lerLeituraDeLicencas({ licencas: [], produtos: { ar: "8" } }), null);
  assert.equal(lerLeituraDeLicencas({ licencas: "x", produtos: {} }), null);
});

test("lerSnapshot reads the table of section 8 as the daemon answers it (lê a tabela da seção 8 como o daemon responde)", () => {
  const item = {
    dpid: 142,
    numero: 0,
    indice: 0,
    funcao: "grupo",
    tipo: "value",
    sentido: "rw",
    classe: "A",
    valores: [],
    minimo: 0,
    maximo: 12,
    empurrado: true,
  };
  const snapshot = lerSnapshot({ dps: { "142": 0 }, mapa: [item], produto: "av", reports_do_dia: 1 });
  assert.deepEqual(snapshot, { dps: { "142": 0 }, mapa: [item], produto: "av", reports_do_dia: 1 });
  assert.equal(lerSnapshot({ dps: {}, mapa: [{ ...item, tipo: "float" }], produto: "av" }), null);
  assert.equal(lerSnapshot({ dps: {}, mapa: [], produto: "tv" }), null);
});

test("comIdentidade and semIdentidade empty a number in place and never shift (esvaziam um número no lugar e nunca empurram)", () => {
  const ordem = ["uuid-1", "uuid-2", "", ""];
  assert.deepEqual(comIdentidade(ordem, 3, "uuid-1"), ["", "uuid-2", "uuid-1", ""]);
  assert.deepEqual(comIdentidade(ordem, 1, ""), ["", "uuid-2", "", ""]);
  assert.deepEqual(semIdentidade(ordem, "uuid-2"), ["uuid-1", "", "", ""]);
  assert.deepEqual(ordemDe(licencaDe()), ["uuid-1", "uuid-2", "", ""]);
});

test("licencasDe and onde find the licences of the product and the number an equipment holds (acham as licenças do produto e o número que um equipamento ocupa)", () => {
  const ar = licencaDe({ id: "ar1", produto: "ar", capacidade: 8, numeros: [] });
  const licencas = [licencaDe(), ar];
  assert.deepEqual(licencasDe(licencas, itemDe()).map((licenca) => licenca.id), ["av1"]);
  assert.deepEqual(licencasDe(licencas, itemDe({ produto: "ar" })).map((licenca) => licenca.id), ["ar1"]);
  assert.deepEqual(licencasDe(licencas, undefined).map((licenca) => licenca.id), ["av1"]);
  const posicao = onde(licencas, "uuid-2");
  assert.equal(posicao?.licenca.id, "av1");
  assert.equal(posicao?.numero.numero, 2);
  assert.equal(onde(licencas, "uuid-9"), undefined);
});

test("gruposPossiveis offers solo and the numbers that can lead a group of their own tipo (oferece solo e os números que podem liderar um grupo do próprio tipo)", () => {
  const catalogo = [itemDe(), itemDe({ tipo: "projetor", categoria: "projetor", capacidades: ["ligar", "desligar"] })];
  assert.deepEqual(gruposPossiveis(licencaDe(), catalogo), [SOLO, 1, 2]);
  const sozinha = licencaDe({ numeros: [numeroDe(), numeroDe({ numero: 2, identidade: "uuid-2", tipo: "projetor" })] });
  assert.deepEqual(gruposPossiveis(sozinha, catalogo), [SOLO]);
  assert.deepEqual(gruposPossiveis(licencaDe({ produto: "ar" }), catalogo), []);
  assert.equal(podeAgrupar(itemDe()), true);
  assert.equal(podeAgrupar(itemDe({ categoria: "audio" })), false);
  assert.equal(podeAgrupar(undefined), false);
});

test("bitDe and paresDe read the packed reports of section 8 (leem os reports empacotados da seção 8)", () => {
  assert.equal(bitDe(5, 1), true);
  assert.equal(bitDe(5, 2), false);
  assert.equal(bitDe(5, 3), true);
  assert.equal(bitDe(2 ** 11, 12), true);
  assert.equal(bitDe("5", 1), false);
  assert.equal(bitDe(5, 0), false);
  assert.deepEqual(paresDe("1=2;3=Bohemian Rhapsody"), { 1: "2", 3: "Bohemian Rhapsody" });
  assert.deepEqual(paresDe(""), {});
  assert.deepEqual(paresDe("x=1;=2;0=3"), {});
  assert.deepEqual(paresDe(7), {});
});

test("comandoDe writes the string the panel of the platform writes (escreve a string que o painel da plataforma escreve)", () => {
  assert.equal(comandoDe(3, "mudo"), "3:mudo");
  assert.equal(comandoDe(12, "entrada", 2), "12:entrada:2");
  assert.equal(comandoDe(1, "tecla", "canal_mais"), "1:tecla:canal_mais");
});

test("controlesDoNumero draws only what the manifest and the lists offer (desenha só o que o manifesto e as listas oferecem)", () => {
  const controles = controlesDoNumero(itemDe(), equipamentoDe());
  assert.equal(controles.ligado, false);
  assert.equal(controles.nivel, true);
  assert.equal(controles.mudo, true);
  assert.equal(controles.transporte, true);
  // Why: the previous and next keys are their own capabilities, drawn only for a driver that
  // declares them; a key the daemon would refuse with nao_suportado is not offered.
  // Por que: as teclas de anterior e próxima são capacidades próprias, desenhadas só para um
  // driver que as declara; uma tecla que o daemon recusaria com nao_suportado não é oferecida.
  assert.equal(controles.proxima, false);
  assert.equal(controles.anterior, false);
  assert.equal(controles.parar, false);
  const caixa = controlesDoNumero(itemDe({ capacidades: ["tocar", "pausar", "proxima", "parar"] }), equipamentoDe());
  assert.equal(caixa.transporte, true);
  assert.equal(caixa.proxima, true);
  assert.equal(caixa.anterior, false);
  assert.equal(caixa.parar, true);
  const soAnterior = controlesDoNumero(itemDe({ capacidades: ["anterior"] }), equipamentoDe());
  assert.equal(soAnterior.anterior, true);
  assert.equal(soAnterior.proxima, false);
  assert.deepEqual(controles.entradas, [{ rotulo: "Wi-Fi", valor: "wifi" }]);
  assert.deepEqual(controles.atalhos, []);
  // Why: half of the power pair is no switch, and a list the manifest cannot act on is not
  // offered, so a matrix without the fonte capability draws no inputs.
  // Por que: metade do par de energia não é chave, e uma lista sobre a qual o manifesto não age
  // não é oferecida, então uma matriz sem a capacidade fonte não desenha entradas.
  const meia = controlesDoNumero(itemDe({ capacidades: ["ligar", "volume"] }), equipamentoDe());
  assert.equal(meia.ligado, false);
  assert.deepEqual(meia.entradas, []);
  const tv = itemDe({ tipo: "tv", categoria: "tv", capacidades: ["ligar", "desligar", "tecla", "atalho", "modo"], teclas: ["ok"], produto: "av", template: "tv" });
  const equipamento = equipamentoDe({ listas: { atalhos: [{ rotulo: "Netflix", valor: "app" }], modos: [{ rotulo: "Cinema", valor: "movie" }] } });
  const daTv = controlesDoNumero(tv, equipamento);
  assert.equal(daTv.ligado, true);
  assert.deepEqual(daTv.teclas, ["ok"]);
  assert.deepEqual(daTv.atalhos, [{ rotulo: "Netflix", valor: "app" }]);
  assert.deepEqual(daTv.modos, [{ rotulo: "Cinema", valor: "movie" }]);
  const ar = itemDe({ produto: "ar", categoria: "ar_condicionado", capacidades: ["ligar", "desligar", "temperatura", "modo", "vento"], modos: ["frio"], ventos: ["auto", "alto"] });
  const doAr = controlesDoNumero(ar, undefined);
  assert.equal(doAr.temperatura, true);
  assert.deepEqual(doAr.modosDeAr, ["frio"]);
  assert.deepEqual(doAr.ventos, ["auto", "alto"]);
  assert.deepEqual(doAr.modos, []);
  assert.equal(controlesDoNumero(undefined, undefined).nivel, false);
});

test("prepararNivel and idValido refuse what the daemon would refuse (recusam o que o daemon recusaria)", () => {
  assert.deepEqual(prepararNivel(" 50 "), { ok: true, valor: 50 });
  const recusa = prepararNivel("101");
  assert.equal(!recusa.ok && recusa.codigo, "valor_invalido");
  assert.equal(prepararNivel("abc").ok, false);
  assert.equal(idValido(""), true);
  assert.equal(idValido("av1"), true);
  assert.equal(idValido("sala-2_b"), true);
  assert.equal(idValido("Av1"), false);
  assert.equal(idValido("-a"), false);
  assert.equal(idValido("a".repeat(41)), false);
});

test("tocando reads the mirrored title and never guesses (lê o título espelhado e nunca chuta)", () => {
  assert.equal(tocando(numeroDe({ estado: estadoDe({ tocando: "Rádio" }) })), true);
  assert.equal(tocando(numeroDe({ estado: estadoDe({ tocando: "" }) })), false);
  assert.equal(tocando(numeroDe({ estado: null })), false);
});

test("every code of a licence has a phrase in both dictionaries (todo código de licença tem frase nos dois dicionários)", () => {
  for (const idioma of ["pt", "en"]) {
    const caminho = new URL(`./i18n/${idioma}.json`, import.meta.url);
    const textos = JSON.parse(readFileSync(caminho, "utf-8")) as Record<string, string>;
    for (const codigo of CODIGOS_LICENCAS) {
      assert.equal(typeof textos[`erro_${codigo}`], "string", `${idioma}: erro_${codigo}`);
    }
    for (const produto of ["ar", "av"]) {
      assert.equal(typeof textos[`produto_${produto}`], "string", `${idioma}: produto_${produto}`);
    }
  }
});
