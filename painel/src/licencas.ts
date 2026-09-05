// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a licence is a device on the platform and a slice of the bus (section 8), with numbers
// any registered equipment of its product may occupy, and nothing here decides which data point
// a number carries nor what it means: the daemon answers both, so the panel never keeps a
// second copy of the contract. What lives here is what a screen needs from that answer, as pure
// functions with tests.
// Por que: uma licença é um dispositivo na plataforma e uma fatia do barramento (seção 8), com
// números que qualquer equipamento cadastrado do produto dela pode ocupar, e nada aqui decide
// qual data point um número carrega nem o que ele significa: o daemon responde os dois, então o
// painel nunca guarda uma segunda cópia do contrato. Aqui mora o que uma tela precisa daquela
// resposta, como funções puras com teste.

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

// Why: section 6, multiroom is a capability of the equipment, declared by the manifest as the
// category plus agrupar, and the panel reads the same two facts the daemon reads.
// Por que: seção 6, multiroom é capacidade do equipamento, declarada pelo manifesto como a
// categoria mais agrupar, e o painel lê os mesmos dois fatos que o daemon lê.
export const CATEGORIA_DE_GRUPO = "multiroom";
export const CAPACIDADE_DE_GRUPO = "agrupar";

// Why: section 14, a speaker can be held in a group this hub does not lead (the app of the
// manufacturer, a lost reply, a restart with a group up); the daemon names that role apart
// because nothing routes its volume, transport or radios to a master the hub does not know.
// Por que: seção 14, uma caixa pode estar presa num grupo que este hub não lidera (o app do
// fabricante, uma resposta perdida, um reinício com um grupo de pé); o daemon nomeia esse papel
// à parte porque nada roteia o volume, o transporte ou as rádios dela para um mestre que o hub
// não conhece.
export const PAPEIS = ["", "mestre", "escravo", "alheio"] as const;
export type Papel = (typeof PAPEIS)[number];

export const TIPOS_DE_DP = ["value", "bool", "enum", "string"] as const;
export type TipoDeDp = (typeof TIPOS_DE_DP)[number];

export const SENTIDOS = ["rw", "envio", "reporte"] as const;
export type Sentido = (typeof SENTIDOS)[number];

// Why: the API answers a stable code and never a phrase, so each one needs an entry in both
// dictionaries, and a test asserts that it has one.
// Por que: a API responde um código estável e nunca uma frase, então cada um precisa de
// entrada nos dois dicionários, e um teste garante isso.
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
  // Why: a number nobody occupies answers a null state, which is not a broken answer: the hub
  // works with zero equipment and the POSITION of the number is the contract.
  // Por que: um número que ninguém ocupa responde estado nulo, o que não é resposta quebrada: o
  // hub funciona com zero equipamento e a POSIÇÃO do número é o contrato.
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

// Why: a shift would move the equipment of number 2 into number 1 in every automation the
// customer already built, so a number is emptied where it is and the identity is only taken
// off the number it used to occupy.
// Por que: um empurrão moveria o equipamento do número 2 para o número 1 em toda automação que
// o cliente já montou, então um número é esvaziado no lugar e a identidade só sai do número
// que ela ocupava.
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

// Why: section 8, an equipment only enters a licence of its product, so the licences a screen
// offers for one are the licences of that product and no other.
// Por que: seção 8, um equipamento só entra numa licença do produto dele, então as licenças que
// uma tela oferece para um são as licenças daquele produto e nenhuma outra.
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

// Why: section 14, a group only ever exists between speakers of the same domain, so a number
// that has nobody of its own tipo to lead is never offered as a group; offering a mixed one is
// what leaves half of it playing and the other half silent.
// Por que: seção 14, um grupo só existe entre caixas do mesmo domínio, então um número que não
// tem ninguém do tipo dele para liderar nunca é oferecido como grupo; oferecer um misto é o
// que deixa metade dele tocando e a outra metade calada.
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

// Why: the online and the muted travel as one bit per number, number n at bit n - 1.
// Por que: o online e o mudo viajam como um bit por número, o número n no bit n - 1.
export function bitDe(valor: unknown, numero: number): boolean {
  if (typeof valor !== "number" || !Number.isInteger(valor) || numero < 1) return false;
  return Math.floor(valor / 2 ** (numero - 1)) % 2 === 1;
}

// The inputs, the modes and the titles travel as n=texto joined by ';'.
// As entradas, os modos e os títulos viajam como n=texto unidos por ';'.
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

// Why: the command channel of section 8 is one string, n:acao[:valor], written by the panel of
// the platform; the simulator writes the very same string.
// Por que: o canal de comando da seção 8 é uma string, n:acao[:valor], escrita pelo painel da
// plataforma; o simulador escreve a mesmíssima string.
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

// Why: section 8, the panel of the platform draws a number from its profile, which is built
// from the manifest and the lists of the registration; the simulator reads the same two facts.
// A power switch needs both halves of the pair, because a switch that turns on and cannot turn
// off is a switch the customer cannot trust; the same holds for transport.
// Por que: seção 8, o painel da plataforma desenha um número a partir do perfil dele, que nasce
// do manifesto e das listas do cadastro; o simulador lê os mesmos dois fatos. Uma chave de ligar
// precisa das duas metades do par, porque uma chave que liga e não desliga é uma chave em que o
// cliente não pode confiar; o mesmo vale para o transporte.
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

// Why: a level of 300 is refused here with the same stable code the daemon would answer, so a
// typo costs no request; the daemon still judges it, because the panel is not the authority.
// Por que: um nível de 300 é recusado aqui com o mesmo código estável que o daemon responderia,
// então um erro de digitação não custa requisição; o daemon ainda julga, porque o painel não é a
// autoridade.
export function prepararNivel(entrada: string): Preparo {
  const limpo = entrada.trim();
  const dentro = /^\d{1,3}$/.test(limpo) && Number(limpo) <= 100;
  return dentro ? { ok: true, valor: Number(limpo) } : { ok: false, codigo: "valor_invalido" };
}

// Why: the id of a licence is a key of config.json and a segment of a route on the daemon, so
// the panel refuses here what the daemon would refuse with licenca_invalida.
// Por que: o id de uma licença é chave do config.json e segmento de rota no daemon, então o
// painel recusa aqui o que o daemon recusaria com licenca_invalida.
export function idValido(id: string): boolean {
  return id === "" || /^[a-z0-9][a-z0-9_-]{0,39}$/.test(id);
}

export function tocando(numero: Numero): boolean {
  // Why: section 14, a slave answers stop even while the group plays, so the daemon mirrors
  // what the master plays onto it and the screen reads that and never the slave itself.
  // Por que: seção 14, um escravo responde stop mesmo com o grupo tocando, então o daemon
  // espelha nele o que o mestre toca e a tela lê isso, e nunca o próprio escravo.
  return numero.estado !== null && numero.estado.tocando !== null && numero.estado.tocando !== "";
}
