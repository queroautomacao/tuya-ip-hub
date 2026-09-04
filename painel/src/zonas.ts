// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a zone is a multiroom equipment occupying one of the six blocks of section 8, and
// nothing here decides which equipment that may be nor which data point a block carries: the
// daemon answers both, so the panel never keeps a second copy of the contract. What lives
// here is what a screen needs from that answer, as pure functions with tests.
// Por que: uma zona é um equipamento multiroom ocupando um dos seis blocos da seção 8, e nada
// aqui decide qual equipamento pode ser nem qual data point um bloco carrega: o daemon
// responde os dois, então o painel nunca guarda uma segunda cópia do contrato. Aqui mora o
// que uma tela precisa daquela resposta, como funções puras com teste.

import {
  lerEstadoEquipamento,
  lerLista,
  type Capacidade,
  type EstadoEquipamento,
  type ItemCatalogo,
} from "./equipamentos.ts";

export const SOLO = "solo";
export const PREFIXO_GRUPO = "grupo";

// Why: section 6 fixes what a zone is, and the daemon refuses a block for anything else; the
// panel offers the same set so the integrator is never offered a refusal.
// Por que: a seção 6 fixa o que é uma zona, e o daemon recusa um bloco para qualquer outra
// coisa; o painel oferece o mesmo conjunto para o integrador nunca receber uma recusa.
export const CATEGORIA_DE_ZONA = "multiroom";
export const CAPACIDADE_DE_GRUPO = "agrupar";

export const PAPEIS = ["", "mestre", "escravo"] as const;
export type Papel = (typeof PAPEIS)[number];

// The functions of a block, in the order section 8 numbers them.
// As funções de um bloco, na ordem em que a seção 8 as numera.
export const FUNCOES_DO_BLOCO = [
  "volume",
  "play",
  "preset",
  "online",
  "tocando",
  "entrada",
] as const;

export type FuncaoDoBloco = (typeof FUNCOES_DO_BLOCO)[number];

// Why: the API answers a stable code and never a phrase, so each one needs an entry in both
// dictionaries, and a test asserts that it has one.
// Por que: a API responde um código estável e nunca uma frase, então cada um precisa de
// entrada nos dois dicionários, e um teste garante isso.
export const CODIGOS_ZONAS = [
  "zonas_demais",
  "zona_repetida",
  "eq_nao_multiroom",
  "identidade_invalida",
  "dp_desconhecido",
  "dp_somente_leitura",
  "valor_invalido",
  "zona_offline",
] as const;

export type DpsDoBloco = Record<FuncaoDoBloco, number>;

export interface Zona {
  zona: number;
  identidade: string;
  nome: string;
  tipo: string;
  papel: Papel;
  entradas: string[];
  dps: DpsDoBloco;
  estado: EstadoEquipamento | null;
}

export interface LeituraDeZonas {
  zonas: Zona[];
  grupo: string;
  dp_grupo: number;
}

type Objeto = Record<string, unknown>;

const ehNumero = (valor: unknown): valor is number => typeof valor === "number";
const ehTexto = (valor: unknown): valor is string => typeof valor === "string";

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

function lerPapel(valor: unknown): Papel | null {
  return PAPEIS.find((papel) => papel === valor) ?? null;
}

function lerDps(valor: unknown): DpsDoBloco | null {
  if (!ehObjeto(valor)) return null;
  const dps: Partial<DpsDoBloco> = {};
  for (const funcao of FUNCOES_DO_BLOCO) {
    const dpid = valor[funcao];
    if (!ehNumero(dpid)) return null;
    dps[funcao] = dpid;
  }
  return dps as DpsDoBloco;
}

export function lerZona(valor: unknown): Zona | null {
  if (!ehObjeto(valor) || !ehNumero(valor.zona)) return null;
  const { identidade, nome, tipo } = valor;
  if (!ehTexto(identidade) || !ehTexto(nome) || !ehTexto(tipo)) return null;
  const papel = lerPapel(valor.papel);
  const entradas = lerLista(valor.entradas, (bruto) => (ehTexto(bruto) ? bruto : null));
  const dps = lerDps(valor.dps);
  if (papel === null || entradas === null || dps === null) return null;
  // Why: a block nobody occupies answers a null state, which is not a broken answer: the hub
  // works with zero equipment and the POSITION of the block is the contract.
  // Por que: um bloco que ninguém ocupa responde estado nulo, o que não é resposta quebrada: o
  // hub funciona com zero equipamento e a POSIÇÃO do bloco é o contrato.
  const estado = valor.estado === null ? null : lerEstadoEquipamento(valor.estado);
  if (estado === undefined) return null;
  return { zona: valor.zona, identidade, nome, tipo, papel, entradas, dps, estado };
}

export function lerLeituraDeZonas(dados: Objeto): LeituraDeZonas | null {
  const zonas = lerLista(dados.zonas, lerZona);
  if (zonas === null || !ehTexto(dados.grupo) || !ehNumero(dados.dp_grupo)) return null;
  return { zonas, grupo: dados.grupo, dp_grupo: dados.dp_grupo };
}

export function valorDoGrupo(zona: number): string {
  return `${PREFIXO_GRUPO}${zona}`;
}

export function ordemDe(zonas: readonly Zona[]): string[] {
  return zonas.map((bloco) => bloco.identidade);
}

// Why: a shift would move the speaker of zone 2 into zone 1 in every automation the customer
// already built, so a block is emptied where it is and the identity is only taken off the
// block it used to occupy.
// Por que: um empurrão moveria a caixa da zona 2 para a zona 1 em toda automação que o cliente
// já montou, então um bloco é esvaziado no lugar e a identidade só sai do bloco que ela
// ocupava.
export function comIdentidade(
  ordem: readonly string[],
  zona: number,
  identidade: string,
): string[] {
  return ordem.map((atual, posicao) => {
    if (posicao === zona - 1) return identidade;
    return identidade !== "" && atual === identidade ? "" : atual;
  });
}

export function podeOcuparBloco(item: ItemCatalogo | undefined): boolean {
  if (item === undefined) return false;
  return (
    item.categoria === CATEGORIA_DE_ZONA &&
    item.capacidades.includes(CAPACIDADE_DE_GRUPO as Capacidade)
  );
}

// Why: section 14, a group only ever exists between speakers of the same domain, so a zone
// that has nobody of its own tipo to lead is never offered as a group; offering a mixed one
// is what leaves half of it playing and the other half silent.
// Por que: seção 14, um grupo só existe entre caixas do mesmo domínio, então uma zona que não
// tem ninguém do tipo dela para liderar nunca é oferecida como grupo; oferecer um misto é o
// que deixa metade dele tocando e a outra metade calada.
export function gruposPossiveis(zonas: readonly Zona[], catalogo: readonly ItemCatalogo[]): string[] {
  const item = (tipo: string): ItemCatalogo | undefined =>
    catalogo.find((candidato) => candidato.tipo === tipo);
  const lideres = zonas.filter(
    (bloco) =>
      bloco.identidade !== "" &&
      podeOcuparBloco(item(bloco.tipo)) &&
      zonas.some(
        (outra) =>
          outra.zona !== bloco.zona && outra.identidade !== "" && outra.tipo === bloco.tipo,
      ),
  );
  return [SOLO, ...lideres.map((bloco) => valorDoGrupo(bloco.zona))];
}

export type EspecieDeControle = "escala" | "alternar" | "escolha" | "preset";

export interface ControleDeZona {
  funcao: FuncaoDoBloco;
  dpid: number;
  especie: EspecieDeControle;
}

// Why: section 6, a capability the manifest does not declare gets no button, because the
// daemon answers nao_suportado before the driver is touched. The play data point is a toggle
// over the two transport capabilities, so a driver that declares only one of them gets
// neither half of the button instead of a button that fails every other press.
// Por que: seção 6, uma capacidade que o manifesto não declara não ganha botão, porque o
// daemon responde nao_suportado antes de tocar no driver. O data point de play é uma chave
// sobre as duas capacidades de transporte, então um driver que declara só uma delas não ganha
// metade nenhuma do botão em vez de um botão que falha a cada duas apertadas.
const EXIGIDAS: Record<FuncaoDoBloco, Capacidade[]> = {
  volume: ["volume"],
  play: ["tocar", "pausar"],
  preset: ["comando_extra"],
  entrada: ["fonte"],
  online: [],
  tocando: [],
};

const ESPECIES: Partial<Record<FuncaoDoBloco, EspecieDeControle>> = {
  volume: "escala",
  play: "alternar",
  preset: "preset",
  entrada: "escolha",
};

export function controlesDaZona(
  zona: Zona,
  item: ItemCatalogo | undefined,
): ControleDeZona[] {
  if (zona.identidade === "" || item === undefined) return [];
  const controles: ControleDeZona[] = [];
  for (const funcao of FUNCOES_DO_BLOCO) {
    const especie = ESPECIES[funcao];
    const exigidas = EXIGIDAS[funcao];
    if (especie === undefined || exigidas.length === 0) continue;
    if (!exigidas.every((capacidade) => item.capacidades.includes(capacidade))) continue;
    if (funcao === "entrada" && zona.entradas.length === 0) continue;
    controles.push({ funcao, dpid: zona.dps[funcao], especie });
  }
  return controles;
}

export type Preparo = { ok: true; valor: unknown } | { ok: false; codigo: string };

// Why: a volume of 300 is refused here with the same stable code the daemon would answer, so
// a typo costs no request; the daemon still judges it, because the panel is not the authority.
// Por que: um volume de 300 é recusado aqui com o mesmo código estável que o daemon
// responderia, então um erro de digitação não custa requisição; o daemon ainda julga, porque o
// painel não é a autoridade.
export function prepararVolume(entrada: string): Preparo {
  const limpo = entrada.trim();
  const dentro = /^\d{1,3}$/.test(limpo) && Number(limpo) <= 100;
  return dentro ? { ok: true, valor: Number(limpo) } : { ok: false, codigo: "valor_invalido" };
}

export function tocando(zona: Zona): boolean {
  // Why: section 14, a slave answers stop even while the group plays, so the daemon mirrors
  // what the master plays onto it and the screen reads that and never the slave itself.
  // Por que: seção 14, um escravo responde stop mesmo com o grupo tocando, então o daemon
  // espelha nele o que o mestre toca e a tela lê isso, e nunca o próprio escravo.
  return zona.estado !== null && zona.estado.tocando !== null && zona.estado.tocando !== "";
}
