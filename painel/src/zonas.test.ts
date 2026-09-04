// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { ItemCatalogo } from "./equipamentos.ts";
import {
  CODIGOS_ZONAS,
  comIdentidade,
  controlesDaZona,
  gruposPossiveis,
  lerLeituraDeZonas,
  lerZona,
  ordemDe,
  podeOcuparBloco,
  prepararVolume,
  tocando,
  type Zona,
} from "./zonas.ts";

const DPS = { volume: 101, play: 102, preset: 103, online: 104, tocando: 105, entrada: 141 };

const CAPACIDADES = ["volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra"];

function zonaDe(parcial: Partial<Zona> = {}): Zona {
  return {
    zona: 1,
    identidade: "uuid-1",
    nome: "Sala",
    tipo: "multiroom_linkplay",
    papel: "",
    entradas: ["wifi", "line-in"],
    dps: { ...DPS },
    estado: {
      online: true,
      ligado: null,
      volume: 20,
      mudo: null,
      fonte: "wifi",
      fontes: ["wifi", "line-in"],
      tocando: null,
      detalhe: "",
    },
    ...parcial,
  };
}

function vazia(zona: number): Zona {
  return zonaDe({
    zona,
    identidade: "",
    nome: "",
    tipo: "",
    entradas: [],
    estado: null,
    dps: { ...DPS, volume: 101 + 5 * (zona - 1) },
  });
}

function itemDe(parcial: Partial<ItemCatalogo> = {}): ItemCatalogo {
  return {
    tipo: "multiroom_linkplay",
    categoria: "multiroom",
    motor: "nativo",
    auth: "nenhuma",
    capacidades: [...CAPACIDADES],
    rotulo: { pt: "Caixa", en: "Speaker" },
    textos: { pt: { descricao: "Caixa" }, en: { descricao: "Speaker" } },
    config_campos: [],
    ...parcial,
  };
}

test("lerZona takes a block and refuses an answer outside the contract (aceita um bloco e recusa resposta fora do contrato)", () => {
  const bruto = {
    zona: 2,
    identidade: "uuid-2",
    nome: "Cozinha",
    tipo: "multiroom_linkplay",
    papel: "escravo",
    entradas: ["wifi"],
    dps: { volume: 106, play: 107, preset: 108, online: 109, tocando: 110, entrada: 142 },
    estado: null,
  };
  const lida = lerZona(bruto);
  assert.ok(lida !== null);
  assert.equal(lida.zona, 2);
  assert.equal(lida.papel, "escravo");
  assert.equal(lida.estado, null);
  assert.equal(lida.dps.entrada, 142);
  // Why: a data point the answer does not carry would leave a button with no number to write
  // to, and the panel would send a set to undefined.
  // Por que: um data point que a resposta não traz deixaria um botão sem número para escrever,
  // e o painel mandaria um set para undefined.
  assert.equal(lerZona({ ...bruto, dps: { ...bruto.dps, entrada: undefined } }), null);
  assert.equal(lerZona({ ...bruto, papel: "dono" }), null);
  assert.equal(lerZona({ ...bruto, entradas: [1] }), null);
  assert.equal(lerZona({ ...bruto, zona: "2" }), null);
});

test("lerLeituraDeZonas needs the group and the data point of it (precisa do grupo e do data point dele)", () => {
  const zonas = [zonaDe()];
  assert.equal(lerLeituraDeZonas({ zonas, grupo: "solo", dp_grupo: 132 })?.dp_grupo, 132);
  assert.equal(lerLeituraDeZonas({ zonas, grupo: "solo" }), null);
  assert.equal(lerLeituraDeZonas({ zonas, grupo: 132, dp_grupo: 132 }), null);
  assert.equal(lerLeituraDeZonas({ grupo: "solo", dp_grupo: 132 }), null);
});

// Why: a shift would move the speaker of zone 2 into zone 1 in every automation the customer
// already built on the platform, and nothing on the bus would say it happened.
// Por que: um empurrão moveria a caixa da zona 2 para a zona 1 em toda automação que o cliente
// já montou na plataforma, e nada no barramento diria que isso aconteceu.
test("comIdentidade empties a block in place and never shifts the rest (esvazia o bloco no lugar e nunca empurra o resto)", () => {
  const ordem = ["uuid-1", "uuid-2", "uuid-3"];
  assert.deepEqual(comIdentidade(ordem, 1, ""), ["", "uuid-2", "uuid-3"]);
  assert.deepEqual(comIdentidade(ordem, 2, ""), ["uuid-1", "", "uuid-3"]);
  // Why: one speaker in two blocks answers the volume of two zones on the bus, so moving it
  // takes it off the block it used to occupy.
  // Por que: uma caixa em dois blocos responde o volume de duas zonas no barramento, então
  // movê-la a tira do bloco que ela ocupava.
  assert.deepEqual(comIdentidade(ordem, 1, "uuid-3"), ["uuid-3", "uuid-2", ""]);
  assert.deepEqual(comIdentidade(["", "", ""], 3, "uuid-9"), ["", "", "uuid-9"]);
});

test("ordemDe reads the identities in the order of the blocks (lê as identidades na ordem dos blocos)", () => {
  const zonas = [zonaDe(), vazia(2), zonaDe({ zona: 3, identidade: "uuid-3" })];
  assert.deepEqual(ordemDe(zonas), ["uuid-1", "", "uuid-3"]);
});

test("podeOcuparBloco takes only what section 6 calls a zone (só aceita o que a seção 6 chama de zona)", () => {
  assert.equal(podeOcuparBloco(itemDe()), true);
  assert.equal(podeOcuparBloco(itemDe({ categoria: "projetor" })), false);
  assert.equal(podeOcuparBloco(itemDe({ capacidades: ["volume", "fonte"] })), false);
  assert.equal(podeOcuparBloco(undefined), false);
});

// Why: section 14, a group only ever exists between speakers of the same domain; offering a
// mixed one is what leaves half of it playing and the other half silent.
// Por que: seção 14, um grupo só existe entre caixas do mesmo domínio; oferecer um misto é o
// que deixa metade dele tocando e a outra metade calada.
test("gruposPossiveis never offers a mixed group (nunca oferece um grupo misto)", () => {
  const catalogo = [itemDe(), itemDe({ tipo: "multiroom_de_outra_marca" })];
  const iguais = [zonaDe(), zonaDe({ zona: 2, identidade: "uuid-2" })];
  assert.deepEqual(gruposPossiveis(iguais, catalogo), ["solo", "grupo1", "grupo2"]);
  const mistas = [zonaDe(), zonaDe({ zona: 2, identidade: "uuid-2", tipo: "multiroom_de_outra_marca" })];
  assert.deepEqual(gruposPossiveis(mistas, catalogo), ["solo"]);
  // Why: a group of one is not a group, and a screen that offered it would publish a group
  // the customer cannot hear.
  // Por que: um grupo de um não é grupo, e uma tela que o oferecesse publicaria um grupo que o
  // cliente não escuta.
  assert.deepEqual(gruposPossiveis([zonaDe(), vazia(2)], catalogo), ["solo"]);
  assert.deepEqual(gruposPossiveis(iguais, []), ["solo"]);
});

// Why: section 6, a capability the manifest does not declare gets no button, because the
// daemon answers nao_suportado before the driver is touched.
// Por que: seção 6, uma capacidade que o manifesto não declara não ganha botão, porque o
// daemon responde nao_suportado antes de tocar no driver.
test("controlesDaZona offers only what the manifest declares (oferece só o que o manifesto declara)", () => {
  const todos = controlesDaZona(zonaDe(), itemDe());
  assert.deepEqual(
    todos.map((controle) => [controle.funcao, controle.dpid, controle.especie]),
    [
      ["volume", 101, "escala"],
      ["play", 102, "alternar"],
      ["preset", 103, "preset"],
      ["entrada", 141, "escolha"],
    ],
  );
  const semTransporte = controlesDaZona(zonaDe(), itemDe({ capacidades: ["volume", "tocar"] }));
  assert.deepEqual(
    semTransporte.map((controle) => controle.funcao),
    ["volume"],
  );
  const semEntradas = controlesDaZona(zonaDe({ entradas: [] }), itemDe());
  assert.ok(!semEntradas.some((controle) => controle.funcao === "entrada"));
  assert.deepEqual(controlesDaZona(vazia(2), itemDe()), []);
  assert.deepEqual(controlesDaZona(zonaDe(), undefined), []);
});

test("prepararVolume refuses what the data point does not take (recusa o que o data point não aceita)", () => {
  assert.deepEqual(prepararVolume("0"), { ok: true, valor: 0 });
  assert.deepEqual(prepararVolume(" 100 "), { ok: true, valor: 100 });
  for (const bruto of ["101", "-1", "abc", "", "1.5", "300"]) {
    assert.deepEqual(prepararVolume(bruto), { ok: false, codigo: "valor_invalido" }, bruto);
  }
});

// Why: section 14, a slave answers stop even while the group plays, so the daemon mirrors what
// the master plays onto it and the screen reads that.
// Por que: seção 14, um escravo responde stop mesmo com o grupo tocando, então o daemon
// espelha nele o que o mestre toca e a tela lê isso.
test("tocando reads the mirrored state of a slave (lê o estado espelhado de um escravo)", () => {
  const escravo = zonaDe({ papel: "escravo" });
  assert.equal(tocando(escravo), false);
  assert.equal(tocando(zonaDe({ estado: { ...escravo.estado!, tocando: "Musica 1" } })), true);
  assert.equal(tocando(zonaDe({ estado: { ...escravo.estado!, tocando: "" } })), false);
  assert.equal(tocando(vazia(3)), false);
});

test("every code of a zone has a phrase in both dictionaries (todo código de zona tem frase nos dois dicionários)", () => {
  for (const idioma of ["pt", "en"]) {
    const caminho = new URL(`./i18n/${idioma}.json`, import.meta.url);
    const textos = JSON.parse(readFileSync(caminho, "utf-8")) as Record<string, string>;
    for (const codigo of CODIGOS_ZONAS) {
      const frase = textos[`erro_${codigo}`];
      assert.equal(typeof frase, "string", `${idioma}.erro_${codigo}`);
      assert.ok(frase.trim().length > 0, `${idioma}.erro_${codigo}`);
    }
  }
});

// Why: the name of a zone is the name of an equipment, which the registration takes long and
// in any alphabet, so the stylesheet has to wrap it instead of letting it stretch the card
// past the edge of the screen.
// Por que: o nome de uma zona é o nome de um equipamento, que o cadastro aceita longo e em
// qualquer alfabeto, então a folha de estilo precisa quebrá-lo em vez de deixá-lo esticar o
// cartão para fora da tela.
test("zone text has a wrapping rule (texto de zona tem regra de quebra)", () => {
  const css = readFileSync(new URL("./estilos-zonas.css", import.meta.url), "utf-8");
  const inicio = css.indexOf(".zona h3,");
  assert.ok(inicio > 0, "no wrapping rule for zone text");
  const bloco = css.slice(inicio, css.indexOf("}", inicio));
  assert.ok(bloco.includes("overflow-wrap: anywhere"), "overflow-wrap");
});
