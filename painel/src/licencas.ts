// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// A licence is a device on the platform and a slice of the bus, with numbers
// any registered equipment of its product may occupy, and nothing here decides which data point
// a number carries nor what it means: the daemon answers both, so the panel never keeps a
// second copy of the contract. What lives here is what a screen needs from that answer, as pure
// functions with tests.

import {
  lerEstadoEquipamento,
  lerLista,
  produtoDe,
  type Capacidade,
  type Equipamento,
  type EstadoEquipamento,
  type Item,
  type ItemCatalogo,
  type Produto,
} from "./equipamentos.ts";

export const SOLO = 0;

// Multiroom is a capability of the equipment, declared by the manifest as the
// category plus agrupar, and the panel reads the same two facts the daemon reads.
export const CATEGORIA_DE_GRUPO = "multiroom";
export const CAPACIDADE_DE_GRUPO = "agrupar";

// A speaker can be held in a group this hub does not lead (the app of the
// manufacturer, a lost reply, a restart with a group up); the daemon names that role apart
// because nothing routes its volume, transport or radios to a master the hub does not know.
export const PAPEIS = ["", "mestre", "escravo", "alheio"] as const;
export type Papel = (typeof PAPEIS)[number];

export const TIPOS_DE_DP = ["value", "bool", "enum", "string"] as const;
export type TipoDeDp = (typeof TIPOS_DE_DP)[number];

export const SENTIDOS = ["rw", "envio", "reporte"] as const;
export type Sentido = (typeof SENTIDOS)[number];

// The API answers a stable code and never a phrase, so each one needs an entry in both
// dictionaries, and a test asserts that it has one.
export const CODIGOS_LICENCAS = [
  "licenca_invalida",
  "licenca_repetida",
  "licenca_nao_encontrada",
  "licenca_incompleta",
  "licenca_desconhecida",
  "licencas_demais",
  "produto_invalido",
  "numeros_demais",
  "numero_repetido",
  "numero_ocupado",
  "identidade_invalida",
  "produto_incompativel",
  "perfis_longos",
  "dp_desconhecido",
  "dp_somente_leitura",
  "valor_invalido",
  "numero_offline",
] as const;

export interface Numero {
  numero: number;
  identidade: string;
  nome: string;
  tipo: string;
  papel: Papel;
  dps: Record<string, number>;
  estado: EstadoEquipamento | null;
}

export interface Licenca {
  id: string;
  produto: Produto;
  nome: string;
  uuid: string;
  pid: string;
  chave_definida: boolean;
  capacidade: number;
  numeros: Numero[];
  grupo: number;
  reports_do_dia: number;
  ouvintes: number;
}

export interface LeituraDeLicencas {
  licencas: Licenca[];
  produtos: Record<string, number>;
  reports_por_dia: number;
  aviso_do_dia: number;
}

export interface ItemDoMapa {
  dpid: number;
  numero: number;
  indice: number;
  funcao: string;
  tipo: TipoDeDp;
  sentido: Sentido;
  classe: string;
  valores: string[];
  minimo: number;
  maximo: number;
  empurrado: boolean;
}

export interface Snapshot {
  dps: Record<string, unknown>;
  mapa: ItemDoMapa[];
  produto: Produto;
  reports_do_dia: number;
}

export interface Qr {
  conteudo: string;
  uuid: string;
  pid: string;
}

export interface CorpoDeLicenca {
  produto?: Produto;
  id?: string;
  nome?: string;
  uuid?: string;
  pid?: string;
  chave?: string;
}

type Objeto = Record<string, unknown>;

const ehNumero = (valor: unknown): valor is number => typeof valor === "number";
const ehTexto = (valor: unknown): valor is string => typeof valor === "string";
const ehLogico = (valor: unknown): valor is boolean => typeof valor === "boolean";

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

function lerPapel(valor: unknown): Papel | null {
  return PAPEIS.find((papel) => papel === valor) ?? null;
}

function lerProduto(valor: unknown): Produto | null {
  return valor === "ar" || valor === "av" ? valor : null;
}

function lerDps(valor: unknown): Record<string, number> | null {
  if (!ehObjeto(valor)) return null;
  const dps: Record<string, number> = {};
  for (const [funcao, dpid] of Object.entries(valor)) {
    if (!ehNumero(dpid)) return null;
    dps[funcao] = dpid;
  }
  return dps;
}

export function lerNumero(valor: unknown): Numero | null {
  if (!ehObjeto(valor) || !ehNumero(valor.numero)) return null;
  const { identidade, nome, tipo } = valor;
  if (!ehTexto(identidade) || !ehTexto(nome) || !ehTexto(tipo)) return null;
  const papel = lerPapel(valor.papel);
  const dps = lerDps(valor.dps);
  if (papel === null || dps === null) return null;
  // A number nobody occupies answers a null state, which is not a broken answer: the hub
  // works with zero equipment and the POSITION of the number is the contract.
  const estado = valor.estado === null ? null : lerEstadoEquipamento(valor.estado);
  if (valor.estado !== null && estado === null) return null;
  return { numero: valor.numero, identidade, nome, tipo, papel, dps, estado };
}

export function lerLicenca(valor: unknown): Licenca | null {
  if (!ehObjeto(valor) || !ehTexto(valor.id) || !valor.id) return null;
  const produto = lerProduto(valor.produto);
  const { nome, uuid, pid } = valor;
  if (produto === null || !ehTexto(nome) || !ehTexto(uuid) || !ehTexto(pid)) return null;
  if (!ehLogico(valor.chave_definida) || !ehNumero(valor.capacidade)) return null;
  const numeros = lerLista(valor.numeros, lerNumero);
  if (numeros === null || !ehNumero(valor.grupo)) return null;
  const reports_do_dia = ehNumero(valor.reports_do_dia) ? valor.reports_do_dia : 0;
  const ouvintes = ehNumero(valor.ouvintes) ? valor.ouvintes : 0;
  return {
    id: valor.id,
    produto,
    nome,
    uuid,
    pid,
    chave_definida: valor.chave_definida,
    capacidade: valor.capacidade,
    numeros,
    grupo: valor.grupo,
    reports_do_dia,
    ouvintes,
  };
}

export function lerLeituraDeLicencas(dados: Objeto): LeituraDeLicencas | null {
  const licencas = lerLista(dados.licencas, lerLicenca);
  if (licencas === null || !ehObjeto(dados.produtos)) return null;
  const produtos: Record<string, number> = {};
  for (const [produto, capacidade] of Object.entries(dados.produtos)) {
    if (!ehNumero(capacidade)) return null;
    produtos[produto] = capacidade;
  }
  const reports_por_dia = ehNumero(dados.reports_por_dia) ? dados.reports_por_dia : 0;
  const aviso_do_dia = ehNumero(dados.aviso_do_dia) ? dados.aviso_do_dia : 0;
  return { licencas, produtos, reports_por_dia, aviso_do_dia };
}

export function lerItemDoMapa(valor: unknown): ItemDoMapa | null {
  if (!ehObjeto(valor) || !ehNumero(valor.dpid) || !ehNumero(valor.numero)) return null;
  if (!ehTexto(valor.funcao) || !ehNumero(valor.indice)) return null;
  const tipo = TIPOS_DE_DP.find((candidato) => candidato === valor.tipo);
  const sentido = SENTIDOS.find((candidato) => candidato === valor.sentido);
  const valores = lerLista(valor.valores, (bruto) => (ehTexto(bruto) ? bruto : null));
  if (tipo === undefined || sentido === undefined || valores === null) return null;
  if (!ehTexto(valor.classe) || !ehNumero(valor.minimo) || !ehNumero(valor.maximo)) return null;
  return {
    dpid: valor.dpid,
    numero: valor.numero,
    indice: valor.indice,
    funcao: valor.funcao,
    tipo,
    sentido,
    classe: valor.classe,
    valores,
    minimo: valor.minimo,
    maximo: valor.maximo,
    empurrado: valor.empurrado !== false,
  };
}

export function lerSnapshot(dados: Objeto): Snapshot | null {
  const mapa = lerLista(dados.mapa, lerItemDoMapa);
  const produto = lerProduto(dados.produto);
  if (mapa === null || produto === null || !ehObjeto(dados.dps)) return null;
  const reports_do_dia = ehNumero(dados.reports_do_dia) ? dados.reports_do_dia : 0;
  return { dps: dados.dps, mapa, produto, reports_do_dia };
}

export function lerQr(dados: Objeto): Qr | null {
  const { conteudo, uuid, pid } = dados;
  if (!ehTexto(conteudo) || !ehTexto(uuid) || !ehTexto(pid)) return null;
  return { conteudo, uuid, pid };
}

export function ordemDe(licenca: Licenca): string[] {
  return licenca.numeros.map((numero) => numero.identidade);
}

// A shift would move the equipment of number 2 into number 1 in every automation the
// customer already built, so a number is emptied where it is and the identity is only taken
// off the number it used to occupy.
export function comIdentidade(
  ordem: readonly string[],
  numero: number,
  identidade: string,
): string[] {
  return ordem.map((atual, posicao) => {
    if (posicao === numero - 1) return identidade;
    return identidade !== "" && atual === identidade ? "" : atual;
  });
}

export function semIdentidade(ordem: readonly string[], identidade: string): string[] {
  return ordem.map((atual) => (atual === identidade ? "" : atual));
}

export function podeAgrupar(item: ItemCatalogo | undefined): boolean {
  if (item === undefined) return false;
  return (
    item.categoria === CATEGORIA_DE_GRUPO &&
    item.capacidades.includes(CAPACIDADE_DE_GRUPO as Capacidade)
  );
}

// An equipment only enters a licence of its product, so the licences a screen
// offers for one are the licences of that product and no other.
export function licencasDoProduto(licencas: readonly Licenca[], produto: Produto): Licenca[] {
  return licencas.filter((licenca) => licenca.produto === produto);
}

export function licencasDe(licencas: readonly Licenca[], item: ItemCatalogo | undefined): Licenca[] {
  return licencasDoProduto(licencas, produtoDe(item));
}

export function numeroDe(licenca: Licenca, identidade: string): Numero | undefined {
  return licenca.numeros.find((numero) => numero.identidade === identidade);
}

export function onde(licencas: readonly Licenca[], identidade: string): { licenca: Licenca; numero: Numero } | undefined {
  for (const licenca of licencas) {
    const numero = numeroDe(licenca, identidade);
    if (numero !== undefined) return { licenca, numero };
  }
  return undefined;
}

// A group only ever exists between speakers of the same domain, so a number
// that has nobody of its own tipo to lead is never offered as a group; offering a mixed one is
// what leaves half of it playing and the other half silent.
export function gruposPossiveis(licenca: Licenca, catalogo: readonly ItemCatalogo[]): number[] {
  if (licenca.produto !== "av") return [];
  const item = (tipo: string): ItemCatalogo | undefined =>
    catalogo.find((candidato) => candidato.tipo === tipo);
  const lideres = licenca.numeros.filter(
    (numero) =>
      numero.identidade !== "" &&
      podeAgrupar(item(numero.tipo)) &&
      licenca.numeros.some(
        (outro) =>
          outro.numero !== numero.numero && outro.identidade !== "" && outro.tipo === numero.tipo,
      ),
  );
  return [SOLO, ...lideres.map((numero) => numero.numero)];
}

export function nomeDoNumero(numero: Numero): string {
  return numero.nome || numero.identidade;
}

// The online and the muted travel as one bit per number, number n at bit n - 1.
export function bitDe(valor: unknown, numero: number): boolean {
  if (typeof valor !== "number" || !Number.isInteger(valor) || numero < 1) return false;
  return Math.floor(valor / 2 ** (numero - 1)) % 2 === 1;
}

// The inputs, the modes and the titles travel as n=texto joined by ';'.
export function paresDe(texto: unknown): Record<number, string> {
  const pares: Record<number, string> = {};
  if (typeof texto !== "string" || texto === "") return pares;
  for (const parte of texto.split(";")) {
    const separador = parte.indexOf("=");
    if (separador <= 0) continue;
    const numero = Number(parte.slice(0, separador));
    if (!Number.isInteger(numero) || numero < 1) continue;
    pares[numero] = parte.slice(separador + 1);
  }
  return pares;
}

// The command channel is one string, n:acao[:valor], written by the panel of
// the platform; the simulator writes the very same string.
export function comandoDe(numero: number, acao: string, valor?: string | number): string {
  return valor === undefined ? `${numero}:${acao}` : `${numero}:${acao}:${valor}`;
}

export interface ControlesDoNumero {
  ligado: boolean;
  nivel: boolean;
  mudo: boolean;
  transporte: boolean;
  parar: boolean;
  proxima: boolean;
  anterior: boolean;
  entradas: Item[];
  atalhos: Item[];
  modos: Item[];
  teclas: string[];
  temperatura: boolean;
  modosDeAr: string[];
  ventos: string[];
}

// The panel of the platform draws a number from its profile, which is built
// from the manifest and the lists of the registration; the simulator reads the same two facts.
// A power switch needs both halves of the pair, because a switch that turns on and cannot turn
// off is a switch the customer cannot trust; the same holds for transport.
export function controlesDoNumero(
  item: ItemCatalogo | undefined,
  equipamento: Equipamento | undefined,
): ControlesDoNumero {
  const capacidades = item?.capacidades ?? [];
  const tem = (capacidade: Capacidade): boolean => capacidades.includes(capacidade);
  const listas = equipamento?.listas ?? {};
  return {
    ligado: tem("ligar") && tem("desligar"),
    nivel: tem("volume"),
    mudo: tem("mudo"),
    transporte: tem("tocar") && tem("pausar"),
    parar: tem("parar"),
    proxima: tem("proxima"),
    anterior: tem("anterior"),
    entradas: tem("fonte") ? (listas.entradas ?? []) : [],
    atalhos: tem("atalho") ? (listas.atalhos ?? []) : [],
    modos: tem("modo") && item?.produto !== "ar" ? (listas.modos ?? []) : [],
    teclas: tem("tecla") ? (item?.teclas ?? []) : [],
    temperatura: tem("temperatura"),
    modosDeAr: tem("modo") && item?.produto === "ar" ? (item?.modos ?? []) : [],
    ventos: tem("vento") ? (item?.ventos ?? []) : [],
  };
}

export type Preparo = { ok: true; valor: unknown } | { ok: false; codigo: string };

// A level of 300 is refused here with the same stable code the daemon would answer, so a
// typo costs no request; the daemon still judges it, because the panel is not the authority.
export function prepararNivel(entrada: string): Preparo {
  const limpo = entrada.trim();
  const dentro = /^\d{1,3}$/.test(limpo) && Number(limpo) <= 100;
  return dentro ? { ok: true, valor: Number(limpo) } : { ok: false, codigo: "valor_invalido" };
}

// The id of a licence is a key of config.json and a segment of a route on the daemon, so
// the panel refuses here what the daemon would refuse with licenca_invalida.
export function idValido(id: string): boolean {
  return id === "" || /^[a-z0-9][a-z0-9_-]{0,39}$/.test(id);
}

export function tocando(numero: Numero): boolean {
  // A slave answers stop even while the group plays, so the daemon mirrors
  // what the master plays onto it and the screen reads that and never the slave itself.
  return numero.estado !== null && numero.estado.tocando !== null && numero.estado.tocando !== "";
}
