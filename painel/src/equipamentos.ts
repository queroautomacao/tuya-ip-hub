// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: everything the panel knows about a driver comes from the manifest the API
// returns (section 6), so this module reads that answer and keeps no table of its own.
// Por que: tudo que o painel sabe de um driver vem do manifesto que a API devolve
// (seção 6), então este módulo lê essa resposta e não guarda tabela própria.

import type { Idioma } from "./i18n";

export const CAPACIDADES = [
  "ligar",
  "desligar",
  "volume",
  "mudo",
  "fonte",
  "tocar",
  "pausar",
  "proxima",
  "anterior",
  "agrupar",
  "comando_extra",
] as const;

export type Capacidade = (typeof CAPACIDADES)[number];

export const RESULTADOS_AUTENTICACAO = ["pareado", "aguardando", "falhou"] as const;

export type ResultadoAutenticacao = (typeof RESULTADOS_AUTENTICACAO)[number];

// Why: the API answers a stable code and never a phrase, so each one needs an entry in
// both dictionaries, and a test asserts that it has one.
// Por que: a API responde um código estável e nunca uma frase, então cada um precisa de
// entrada nos dois dicionários, e um teste garante isso.
export const CODIGOS_EQUIPAMENTO = [
  "nao_suportado",
  "eq_offline",
  "invalid_value",
  "auth_pendente",
  "erro_aparelho",
  "eq_nao_encontrado",
  "tipo_desconhecido",
  "identidade_duplicada",
  "ip_invalido",
  "campo_invalido",
] as const;

// Why: Estado.detalhe carries the empty string or ONE code of this fixed vocabulary and
// nothing else, so what a device or an exception said stays in the log of the daemon and
// never reaches this screen as a phrase nobody translated (section 11).
// Por que: Estado.detalhe carrega o texto vazio ou UM código deste vocabulário fixo e
// nada mais, então o que um aparelho ou uma exceção disse fica no log do daemon e nunca
// chega a esta tela como frase que ninguém traduziu (seção 11).
export const DETALHES = [
  "eq_offline",
  "erro_aparelho",
  "auth_pendente",
  "invalid_value",
  "nao_suportado",
  "tipo_desconhecido",
  "contrato_quebrado",
] as const;

export type Detalhe = (typeof DETALHES)[number];

export function ehDetalhe(valor: string): valor is Detalhe {
  return (DETALHES as readonly string[]).includes(valor);
}

// Why: the same 10 s the gestor polls with, so no reading is older than one cycle.
// Por que: os mesmos 10 s do poll do gestor, para nenhuma leitura passar de um ciclo.
export const INTERVALO_MS = 10_000;

export type TipoCampo = "texto" | "inteiro" | "segredo";

export interface Campo {
  nome: string;
  tipo: TipoCampo;
  obrigatorio: boolean;
  padrao: string;
}

export interface ItemCatalogo {
  tipo: string;
  categoria: string;
  motor: string;
  auth: string;
  capacidades: string[];
  rotulo: Record<string, string>;
  textos: Record<string, Record<string, string>>;
  config_campos: Campo[];
}

export interface EstadoEquipamento {
  online: boolean;
  ligado: boolean | null;
  volume: number | null;
  mudo: boolean | null;
  fonte: string | null;
  fontes: string[];
  tocando: string | null;
  detalhe: string;
}

export interface Equipamento {
  identidade: string;
  tipo: string;
  nome: string;
  ip: string;
  campos: Record<string, string>;
  segredos_definidos: string[];
  estado: EstadoEquipamento;
}

export interface Achado {
  tipo: string;
  identidade: string;
  ip: string;
  porta: number | null;
  descricao: string;
  ja_cadastrado: boolean;
}

type Objeto = Record<string, unknown>;

const ehLogico = (valor: unknown): valor is boolean => typeof valor === "boolean";
const ehNumero = (valor: unknown): valor is number => typeof valor === "number";
const ehTexto = (valor: unknown): valor is string => typeof valor === "string";

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

// Why: undefined says the answer broke the contract, null says the field is absent on
// purpose, and the caller has to tell one from the other.
// Por que: undefined diz que a resposta quebrou o contrato, null diz que o campo falta
// de propósito, e quem chama precisa distinguir os dois.
function opcional<T>(valor: unknown, eh: (v: unknown) => v is T): T | null | undefined {
  if (valor === null || valor === undefined) return null;
  return eh(valor) ? valor : undefined;
}

function dicionario(valor: unknown): Record<string, string> | null {
  if (!ehObjeto(valor)) return null;
  return Object.values(valor).every(ehTexto) ? (valor as Record<string, string>) : null;
}

function listaDeTexto(valor: unknown): string[] | null {
  if (!Array.isArray(valor)) return null;
  return valor.every(ehTexto) ? (valor as string[]) : null;
}

function lerCampo(valor: unknown): Campo | null {
  if (!ehObjeto(valor) || !ehTexto(valor.nome) || !valor.nome) return null;
  const { tipo, obrigatorio, padrao } = valor;
  if (tipo !== "texto" && tipo !== "inteiro" && tipo !== "segredo") return null;
  if (!ehLogico(obrigatorio) || !ehTexto(padrao)) return null;
  return { nome: valor.nome, tipo, obrigatorio, padrao };
}

export function lerItemCatalogo(valor: unknown): ItemCatalogo | null {
  if (!ehObjeto(valor) || !ehTexto(valor.tipo) || !valor.tipo) return null;
  const { tipo, categoria, motor, auth } = valor;
  if (!ehTexto(categoria) || !ehTexto(motor) || !ehTexto(auth)) return null;
  const capacidades = listaDeTexto(valor.capacidades);
  const rotulo = dicionario(valor.rotulo);
  if (capacidades === null || rotulo === null || !ehObjeto(valor.textos)) return null;
  const textos: Record<string, Record<string, string>> = {};
  for (const [idioma, bruto] of Object.entries(valor.textos)) {
    const lidos = dicionario(bruto);
    if (lidos === null) return null;
    textos[idioma] = lidos;
  }
  const config_campos = lerLista(valor.config_campos, lerCampo);
  if (config_campos === null) return null;
  return { tipo, categoria, motor, auth, capacidades, rotulo, textos, config_campos };
}

export function lerLista<T>(valor: unknown, ler: (bruto: unknown) => T | null): T[] | null {
  if (!Array.isArray(valor)) return null;
  const saida: T[] = [];
  for (const bruto of valor) {
    const item = ler(bruto);
    if (item === null) return null;
    saida.push(item);
  }
  return saida;
}

export function lerEstadoEquipamento(valor: unknown): EstadoEquipamento | null {
  if (!ehObjeto(valor) || !ehLogico(valor.online)) return null;
  const ligado = opcional(valor.ligado, ehLogico);
  const mudo = opcional(valor.mudo, ehLogico);
  const volume = opcional(valor.volume, ehNumero);
  const fonte = opcional(valor.fonte, ehTexto);
  const tocando = opcional(valor.tocando, ehTexto);
  const fontes = valor.fontes === undefined ? [] : listaDeTexto(valor.fontes);
  const detalhe = valor.detalhe === undefined ? "" : valor.detalhe;
  if (ligado === undefined || mudo === undefined || volume === undefined) return null;
  if (fonte === undefined || tocando === undefined || fontes === null) return null;
  if (!ehTexto(detalhe)) return null;
  return { online: valor.online, ligado, volume, mudo, fonte, fontes, tocando, detalhe };
}

export function lerEquipamento(valor: unknown): Equipamento | null {
  if (!ehObjeto(valor) || !ehTexto(valor.identidade) || !valor.identidade) return null;
  const { identidade } = valor;
  const nome = valor.nome === undefined ? "" : valor.nome;
  const ip = valor.ip === undefined ? "" : valor.ip;
  if (!ehTexto(valor.tipo) || !ehTexto(nome) || !ehTexto(ip)) return null;
  const campos = valor.campos === undefined ? {} : dicionario(valor.campos);
  const brutos = valor.segredos_definidos;
  const segredos_definidos = brutos === undefined ? [] : listaDeTexto(brutos);
  const estado = lerEstadoEquipamento(valor.estado);
  if (campos === null || segredos_definidos === null || estado === null) return null;
  return { identidade, tipo: valor.tipo, nome, ip, campos, segredos_definidos, estado };
}

export function lerAchado(valor: unknown): Achado | null {
  if (!ehObjeto(valor) || !ehTexto(valor.ip) || !valor.ip) return null;
  const { tipo, identidade, ip, descricao } = valor;
  if (!ehTexto(tipo) || !ehTexto(identidade) || !ehTexto(descricao)) return null;
  const porta = opcional(valor.porta, ehNumero);
  if (porta === undefined) return null;
  return { tipo, identidade, ip, porta, descricao, ja_cadastrado: valor.ja_cadastrado === true };
}

export type EspecieControle = "simples" | "alternar" | "escala" | "escolha" | "texto";

export interface Controle {
  acao: Capacidade;
  especie: EspecieControle;
}

const ESPECIES: Record<Capacidade, EspecieControle> = {
  ligar: "simples",
  desligar: "simples",
  volume: "escala",
  mudo: "alternar",
  fonte: "escolha",
  tocar: "simples",
  pausar: "simples",
  proxima: "simples",
  anterior: "simples",
  agrupar: "texto",
  comando_extra: "texto",
};

// Why: section 6, a capability the manifest does not declare gets no button, because the
// gestor answers nao_suportado before the driver is touched; walking CAPACIDADES also
// drops a capability the panel does not know and fixes the order for every driver.
// Por que: seção 6, capacidade que o manifesto não declara não ganha botão, porque o
// gestor responde nao_suportado antes de tocar no driver; percorrer CAPACIDADES também
// descarta a que o painel não conhece e fixa a ordem para todo driver.
export function controles(capacidades: readonly string[]): Controle[] {
  return CAPACIDADES.filter((acao) => capacidades.includes(acao)).map((acao) => ({
    acao,
    especie: ESPECIES[acao],
  }));
}

export type Preparo = { ok: true; valor: unknown } | { ok: false; codigo: string };

export const ENERGIA = ["ligar", "desligar"] as const;
export const TRANSPORTE = ["anterior", "tocar", "pausar", "proxima"] as const;
export type Energia = (typeof ENERGIA)[number];
export type Transporte = (typeof TRANSPORTE)[number];

export interface Paineis {
  energia: Energia[];
  volume: boolean;
  mudo: boolean;
  transporte: Transporte[];
  fonte: boolean;
  extra: boolean;
  algum: boolean;
}

// Why: the integrator bench-tests an equipment with the controls of a remote, grouped the way
// a remote groups them, so the capabilities of section 6 are read into panels here and the
// screen draws a panel when its capabilities exist. Grouping is not a button: it is the
// multiroom card, which needs the number of the equipment on the app.
// Por que: o integrador testa um equipamento na bancada com os controles de um controle
// remoto, agrupados como um controle os agrupa, então as capacidades da seção 6 são lidas em
// painéis aqui e a tela desenha um painel quando as capacidades dele existem. Agrupar não é
// botão: é o cartão de multiroom, que precisa do número do equipamento no app.
export function paineis(capacidades: readonly string[]): Paineis {
  const tem = (capacidade: Capacidade): boolean => capacidades.includes(capacidade);
  const energia = ENERGIA.filter(tem);
  const transporte = TRANSPORTE.filter(tem);
  const [volume, mudo, fonte, extra] = [tem("volume"), tem("mudo"), tem("fonte"), tem("comando_extra")];
  const algum = energia.length > 0 || transporte.length > 0 || volume || mudo || fonte || extra;
  return { energia, volume, mudo, transporte, fonte, extra, algum };
}

export function prepararTexto(entrada: string): Preparo {
  const limpo = entrada.trim();
  return limpo ? { ok: true, valor: limpo } : { ok: false, codigo: "invalid_value" };
}

// Why: a volume of 300 is refused with the same stable code the daemon would use.
// Por que: um volume de 300 é recusado com o mesmo código estável que o daemon usaria.
export function prepararAcao(
  controle: Controle,
  entrada: string,
  estado: EstadoEquipamento,
): Preparo {
  if (controle.especie === "simples") return { ok: true, valor: null };
  if (controle.especie === "alternar") return { ok: true, valor: !(estado.mudo ?? false) };
  const limpo = entrada.trim();
  if (controle.especie === "escala") {
    const dentro = /^\d{1,3}$/.test(limpo) && Number(limpo) <= 100;
    return dentro ? { ok: true, valor: Number(limpo) } : { ok: false, codigo: "invalid_value" };
  }
  return prepararTexto(limpo);
}

export type LinhaEstado =
  | { campo: "ligado" | "mudo"; especie: "logico"; logico: boolean }
  | { campo: "volume"; especie: "numero"; numero: number }
  | { campo: "fonte" | "tocando"; especie: "texto"; texto: string }
  | { campo: "detalhe"; especie: "codigo"; codigo: Detalhe };

// Why: false and zero are readings the driver made, not absences, so only null and the
// empty string stay out; hiding a muted device or a volume of 0 would lie.
// Por que: falso e zero são leituras que o driver fez, não ausências, então só o nulo e
// o texto vazio ficam de fora; esconder um aparelho mudo ou volume 0 seria mentir.
export function linhasDoEstado(estado: EstadoEquipamento): LinhaEstado[] {
  const linhas: LinhaEstado[] = [];
  const { ligado, volume, mudo } = estado;
  if (ligado !== null) linhas.push({ campo: "ligado", especie: "logico", logico: ligado });
  if (volume !== null) linhas.push({ campo: "volume", especie: "numero", numero: volume });
  if (mudo !== null) linhas.push({ campo: "mudo", especie: "logico", logico: mudo });
  for (const campo of ["fonte", "tocando"] as const) {
    const texto = estado[campo];
    if (texto) linhas.push({ campo, especie: "texto", texto });
  }
  // Why: a detalhe outside the vocabulary is a daemon this panel does not know how to
  // translate, and printing it raw would put a phrase nobody wrote for a screen on it.
  // Por que: um detalhe fora do vocabulário é um daemon que este painel não sabe
  // traduzir, e imprimi-lo cru poria na tela uma frase que ninguém escreveu para ela.
  if (ehDetalhe(estado.detalhe)) {
    linhas.push({ campo: "detalhe", especie: "codigo", codigo: estado.detalhe });
  }
  return linhas;
}

// Why: a manifest without one of the languages shows the other, never the word undefined.
// Por que: manifesto sem um dos idiomas mostra o outro, nunca a palavra undefined.
export function rotuloDoTipo(item: ItemCatalogo | undefined, idioma: Idioma, tipo: string): string {
  if (item === undefined) return tipo;
  return item.rotulo[idioma] || item.rotulo.en || item.rotulo.pt || item.tipo;
}

export function textoDoManifesto(
  item: ItemCatalogo | undefined,
  idioma: Idioma,
  chave: string,
): string {
  if (item === undefined) return "";
  const textos = item.textos[idioma] ?? item.textos.en ?? item.textos.pt ?? {};
  return textos[chave] ?? "";
}

// Why: a SEGREDO is a device credential that never leaves the daemon, so the panel
// refuses to render one even if some answer ever carried it back.
// Por que: um SEGREDO é credencial de aparelho que nunca sai do daemon, então o painel
// recusa mostrar um mesmo que alguma resposta o traga de volta.
export function camposVisiveis(
  item: ItemCatalogo | undefined,
  campos: Record<string, string>,
): { nome: string; valor: string }[] {
  const declarados = item?.config_campos ?? [];
  const segredos = new Set(declarados.filter((c) => c.tipo === "segredo").map((c) => c.nome));
  return Object.entries(campos)
    .filter(([nome, valor]) => !segredos.has(nome) && valor !== "")
    .map(([nome, valor]) => ({ nome, valor }));
}
