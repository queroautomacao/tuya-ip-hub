// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Everything the panel knows about a driver comes from the manifest the API
// returns, so this module reads that answer and keeps no table of its own.

import type { Idioma } from "./i18n";

export const CAPACIDADES = [
  "ligar",
  "desligar",
  "volume",
  "mudo",
  "fonte",
  "tocar",
  "pausar",
  "parar",
  "proxima",
  "anterior",
  "agrupar",
  "tecla",
  "atalho",
  "modo",
  "vento",
  "temperatura",
  "comando_extra",
] as const;

export type Capacidade = (typeof CAPACIDADES)[number];

// The vocabularies: a key, a mode of an air conditioner and a fan speed are words
// the daemon translates; the panel only draws them.
export const TECLAS = [
  "mais",
  "menos",
  "canal_mais",
  "canal_menos",
  "cima",
  "baixo",
  "esquerda",
  "direita",
  "ok",
  "voltar",
  "inicio",
  "menu",
  "guia",
  "sair",
  "info",
  "play_pause",
  "proxima",
  "anterior",
  "digito_0",
  "digito_1",
  "digito_2",
  "digito_3",
  "digito_4",
  "digito_5",
  "digito_6",
  "digito_7",
  "digito_8",
  "digito_9",
] as const;
export type Tecla = (typeof TECLAS)[number];
export const MODOS_AR = ["auto", "frio", "quente", "vento", "seco"] as const;
export type ModoAr = (typeof MODOS_AR)[number];
export const VENTOS = ["auto", "baixo", "medio", "alto"] as const;
export type Vento = (typeof VENTOS)[number];
export const TEMPERATURA_MINIMA = 16;
export const TEMPERATURA_MAXIMA = 30;

export const CATEGORIA_DE_AR = "ar_condicionado";
export const PRODUTOS = ["ar", "av"] as const;
export type Produto = (typeof PRODUTOS)[number];

// The lists a registration of audio and video carries, each with its ceiling.
export const LISTAS = ["entradas", "atalhos", "modos"] as const;
export type Lista = (typeof LISTAS)[number];
export const LISTAS_MAXIMO: Record<Lista, number> = { entradas: 10, atalhos: 8, modos: 8 };
export const ROTULO_MAXIMO = 16;
export const VALOR_DE_LISTA_MAXIMO = 64;

export interface Item {
  rotulo: string;
  valor: string;
}

export const RESULTADOS_AUTENTICACAO = ["pareado", "aguardando", "falhou"] as const;

export type ResultadoAutenticacao = (typeof RESULTADOS_AUTENTICACAO)[number];

// The API answers a stable code and never a phrase, so each one needs an entry in
// both dictionaries, and a test asserts that it has one.
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
  "lista_invalida",
  "lista_demais",
  "perfil_longo",
  "perfis_longos",
] as const;

// Estado.detalhe carries the empty string or ONE code of this fixed vocabulary and
// nothing else, so what a device or an exception said stays in the log of the daemon and
// never reaches this screen as a phrase nobody translated.
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

// The same 10 s the gestor polls with, so no reading is older than one cycle.
export const INTERVALO_MS = 10_000;

export type TipoCampo = "texto" | "inteiro" | "segredo";

export interface Campo {
  nome: string;
  tipo: TipoCampo;
  obrigatorio: boolean;
  padrao: string;
}

export interface Sugestao extends Item {
  lista: Lista;
}

export interface ItemCatalogo {
  tipo: string;
  categoria: string;
  auth: string;
  capacidades: string[];
  teclas: string[];
  modos: string[];
  ventos: string[];
  produto: string;
  template: string;
  rotulo: Record<string, string>;
  textos: Record<string, Record<string, string>>;
  config_campos: Campo[];
  // What the driver offers to fill a list of the registration with.
  sugestoes: Sugestao[];
  // The device has no local API, so the registration asks for no address.
  nuvem: boolean;
  // The name of the field a device picked from the account fills, empty when the
  // driver lists nothing.
  lista_da_conta: string;
  // The name of the maker this driver reaches, nominative use and nothing else.
  marca: string;
}

export interface EstadoEquipamento {
  online: boolean;
  ligado: boolean | null;
  volume: number | null;
  mudo: boolean | null;
  fonte: string | null;
  fontes: string[];
  modos: string[];
  atalhos: ItemDeLista[];
  reproduzindo: boolean | null;
  tocando: string | null;
  temperatura: number | null;
  modo: string | null;
  vento: string | null;
  detalhe: string;
}

export type Listas = Partial<Record<Lista, Item[]>>;

export interface Equipamento {
  identidade: string;
  tipo: string;
  nome: string;
  ip: string;
  // The loudest the level may be set to, from anywhere; 100 is no ceiling.
  nivel_maximo: number;
  campos: Record<string, string>;
  segredos_definidos: string[];
  listas: Listas;
  licenca: string | null;
  numero: number | null;
  estado: EstadoEquipamento;
}

export interface Achado {
  tipo: string;
  identidade: string;
  ip: string;
  porta: number | null;
  descricao: string;
  nome: string;
  ja_cadastrado: boolean;
}

type Objeto = Record<string, unknown>;

const ehLogico = (valor: unknown): valor is boolean => typeof valor === "boolean";
const ehNumero = (valor: unknown): valor is number => typeof valor === "number";
const ehTexto = (valor: unknown): valor is string => typeof valor === "string";

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

// Undefined says the answer broke the contract, null says the field is absent on
// purpose, and the caller has to tell one from the other.
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
  const { tipo, categoria, auth } = valor;
  if (!ehTexto(categoria) || !ehTexto(auth)) return null;
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
  const sugestoes = valor.sugestoes === undefined ? [] : lerLista(valor.sugestoes, lerSugestao);
  if (sugestoes === null) return null;
  const nuvem = valor.nuvem === undefined ? false : valor.nuvem;
  if (typeof nuvem !== "boolean") return null;
  const listaDaConta = valor.lista_da_conta === undefined ? "" : valor.lista_da_conta;
  if (typeof listaDaConta !== "string") return null;
  const marca = valor.marca === undefined ? "" : valor.marca;
  if (!ehTexto(marca)) return null;
  // The words and the product come from the manifest through the daemon, so
  // the panel reads them and never decides which category speaks which word.
  const teclas = valor.teclas === undefined ? [] : listaDeTexto(valor.teclas);
  const modos = valor.modos === undefined ? [] : listaDeTexto(valor.modos);
  const ventos = valor.ventos === undefined ? [] : listaDeTexto(valor.ventos);
  const produto = valor.produto === undefined ? "av" : valor.produto;
  const template = valor.template === undefined ? "au" : valor.template;
  if (teclas === null || modos === null || ventos === null) return null;
  if (!ehTexto(produto) || !ehTexto(template)) return null;
  return {
    tipo,
    categoria,
    auth,
    capacidades,
    teclas,
    modos,
    ventos,
    produto,
    template,
    rotulo,
    textos,
    config_campos,
    sugestoes,
    nuvem,
    lista_da_conta: listaDaConta,
    marca,
  };
}

function lerSugestao(valor: unknown): Sugestao | null {
  if (!ehObjeto(valor) || !ehTexto(valor.rotulo) || !ehTexto(valor.valor)) return null;
  const lista = LISTAS.find((nome) => nome === valor.lista);
  if (lista === undefined || !valor.rotulo || !valor.valor) return null;
  return { lista, rotulo: valor.rotulo, valor: valor.valor };
}

// A driver suggests items for a list, and what fills the list is the pair the
// registration carries; the name of the list is how the card knows where each pair goes.
export function sugeridos(item: ItemCatalogo | undefined, lista: Lista): Item[] {
  return (item?.sugestoes ?? [])
    .filter((sugestao) => sugestao.lista === lista)
    .map((sugestao) => ({ rotulo: sugestao.rotulo, valor: sugestao.valor }));
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
  const reproduzindo = opcional(valor.reproduzindo, ehLogico);
  const temperatura = opcional(valor.temperatura, ehNumero);
  const modo = opcional(valor.modo, ehTexto);
  const vento = opcional(valor.vento, ehTexto);
  const fontes = valor.fontes === undefined ? [] : listaDeTexto(valor.fontes);
  const modosDoAparelho = valor.modos === undefined ? [] : listaDeTexto(valor.modos);
  const atalhosDoAparelho = lerItensDeLista(valor.atalhos);
  const detalhe = valor.detalhe === undefined ? "" : valor.detalhe;
  if (ligado === undefined || mudo === undefined || volume === undefined) return null;
  if (fonte === undefined || tocando === undefined || fontes === null) return null;
  if (modosDoAparelho === null || atalhosDoAparelho === null) return null;
  if (reproduzindo === undefined || temperatura === undefined) return null;
  if (modo === undefined || vento === undefined) return null;
  if (!ehTexto(detalhe)) return null;
  return {
    online: valor.online,
    ligado,
    volume,
    mudo,
    fonte,
    fontes,
    modos: modosDoAparelho,
    atalhos: atalhosDoAparelho,
    reproduzindo,
    tocando,
    temperatura,
    modo,
    vento,
    detalhe,
  };
}

export function lerItem(valor: unknown): Item | null {
  if (!ehObjeto(valor) || !ehTexto(valor.rotulo) || !ehTexto(valor.valor)) return null;
  return { rotulo: valor.rotulo, valor: valor.valor };
}

export function lerListas(valor: unknown): Listas | null {
  if (valor === undefined) return {};
  if (!ehObjeto(valor)) return null;
  const listas: Listas = {};
  for (const nome of LISTAS) {
    const bruto = valor[nome];
    if (bruto === undefined) continue;
    const itens = lerLista(bruto, lerItem);
    if (itens === null) return null;
    listas[nome] = itens;
  }
  return listas;
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
  const nivel_maximo = valor.nivel_maximo === undefined ? 100 : valor.nivel_maximo;
  if (!ehNumero(nivel_maximo)) return null;
  const estado = lerEstadoEquipamento(valor.estado);
  const listas = lerListas(valor.listas);
  const licenca = opcional(valor.licenca, ehTexto);
  const numero = opcional(valor.numero, ehNumero);
  if (campos === null || segredos_definidos === null || estado === null) return null;
  if (listas === null || licenca === undefined || numero === undefined) return null;
  return {
    identidade,
    tipo: valor.tipo,
    nome,
    ip,
    nivel_maximo,
    campos,
    segredos_definidos,
    listas,
    licenca,
    numero,
    estado,
  };
}

export function lerAchado(valor: unknown): Achado | null {
  if (!ehObjeto(valor) || !ehTexto(valor.ip) || !valor.ip) return null;
  const { tipo, identidade, ip, descricao } = valor;
  if (!ehTexto(tipo) || !ehTexto(identidade) || !ehTexto(descricao)) return null;
  const porta = opcional(valor.porta, ehNumero);
  if (porta === undefined) return null;
  const nome = ehTexto(valor.nome) ? valor.nome : "";
  return { tipo, identidade, ip, porta, descricao, nome, ja_cadastrado: valor.ja_cadastrado === true };
}

// The sweep lists the whole segment now, so what this image can register comes first
// and the rest follows in the order of the addresses, which is the order a person scans a
// network in.
export function ordenarAchados(achados: readonly Achado[]): Achado[] {
  const numero = (ip: string): number =>
    ip.split(".").reduce((soma, parte) => soma * 256 + (Number(parte) || 0), 0);
  return [...achados].sort((um, outro) => {
    if ((um.tipo === "") !== (outro.tipo === "")) return um.tipo === "" ? 1 : -1;
    return numero(um.ip) - numero(outro.ip);
  });
}

export type EspecieControle =
  | "simples"
  | "alternar"
  | "escala"
  | "escolha"
  | "texto"
  | "tecla"
  | "temperatura";

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
  parar: "simples",
  proxima: "simples",
  anterior: "simples",
  agrupar: "texto",
  tecla: "tecla",
  atalho: "escolha",
  modo: "escolha",
  vento: "escolha",
  temperatura: "temperatura",
  comando_extra: "texto",
};

// A capability the manifest does not declare gets no button, because the
// gestor answers nao_suportado before the driver is touched; walking CAPACIDADES also
// drops a capability the panel does not know and fixes the order for every driver.
export function controles(capacidades: readonly string[]): Controle[] {
  return CAPACIDADES.filter((acao) => capacidades.includes(acao)).map((acao) => ({
    acao,
    especie: ESPECIES[acao],
  }));
}

export type Preparo = { ok: true; valor: unknown } | { ok: false; codigo: string };

export const ENERGIA = ["ligar", "desligar"] as const;
export const TRANSPORTE = ["anterior", "tocar", "pausar", "parar", "proxima"] as const;
export type Energia = (typeof ENERGIA)[number];
export type Transporte = (typeof TRANSPORTE)[number];

export interface Paineis {
  energia: Energia[];
  volume: boolean;
  mudo: boolean;
  transporte: Transporte[];
  fonte: boolean;
  teclas: boolean;
  atalho: boolean;
  modo: boolean;
  vento: boolean;
  temperatura: boolean;
  extra: boolean;
  algum: boolean;
}

// The integrator bench-tests an equipment with the controls of a remote, grouped the way
// a remote groups them, so the capabilities are read into panels here and the
// screen draws a panel when its capabilities exist. Grouping is not a button: it is the
// multiroom card, which needs the number of the equipment on the app.
export function paineis(capacidades: readonly string[]): Paineis {
  const tem = (capacidade: Capacidade): boolean => capacidades.includes(capacidade);
  const energia = ENERGIA.filter(tem);
  const transporte = TRANSPORTE.filter(tem);
  const [volume, mudo, fonte, extra] = [tem("volume"), tem("mudo"), tem("fonte"), tem("comando_extra")];
  const [teclas, atalho, modo] = [tem("tecla"), tem("atalho"), tem("modo")];
  const [vento, temperatura] = [tem("vento"), tem("temperatura")];
  const algum =
    energia.length > 0 ||
    transporte.length > 0 ||
    volume ||
    mudo ||
    fonte ||
    extra ||
    teclas ||
    atalho ||
    modo ||
    vento ||
    temperatura;
  return { energia, volume, mudo, transporte, fonte, teclas, atalho, modo, vento, temperatura, extra, algum };
}

// The setpoint is whole degrees inside the range, refused here with the same
// stable code the daemon answers so a typo costs no request.
export function prepararTemperatura(entrada: string): Preparo {
  const limpo = entrada.trim();
  const dentro =
    /^\d{1,2}$/.test(limpo) &&
    Number(limpo) >= TEMPERATURA_MINIMA &&
    Number(limpo) <= TEMPERATURA_MAXIMA;
  return dentro ? { ok: true, valor: Number(limpo) } : { ok: false, codigo: "invalid_value" };
}

export function prepararTexto(entrada: string): Preparo {
  const limpo = entrada.trim();
  return limpo ? { ok: true, valor: limpo } : { ok: false, codigo: "invalid_value" };
}

// A volume of 300 is refused with the same stable code the daemon would use.
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
  if (controle.especie === "temperatura") return prepararTemperatura(limpo);
  return prepararTexto(limpo);
}

export type LinhaEstado =
  | { campo: "ligado" | "mudo" | "reproduzindo"; especie: "logico"; logico: boolean }
  | { campo: "volume" | "temperatura"; especie: "numero"; numero: number }
  | { campo: "fonte" | "tocando" | "modo" | "vento"; especie: "texto"; texto: string }
  | { campo: "detalhe"; especie: "codigo"; codigo: Detalhe };

// False and zero are readings the driver made, not absences, so only null and the
// empty string stay out; hiding a muted device or a volume of 0 would lie.
export function linhasDoEstado(estado: EstadoEquipamento): LinhaEstado[] {
  const linhas: LinhaEstado[] = [];
  const { ligado, volume, mudo, reproduzindo, temperatura } = estado;
  if (ligado !== null) linhas.push({ campo: "ligado", especie: "logico", logico: ligado });
  if (volume !== null) linhas.push({ campo: "volume", especie: "numero", numero: volume });
  if (mudo !== null) linhas.push({ campo: "mudo", especie: "logico", logico: mudo });
  if (reproduzindo !== null) {
    linhas.push({ campo: "reproduzindo", especie: "logico", logico: reproduzindo });
  }
  if (temperatura !== null) {
    linhas.push({ campo: "temperatura", especie: "numero", numero: temperatura });
  }
  for (const campo of ["fonte", "tocando", "modo", "vento"] as const) {
    const texto = estado[campo];
    if (texto) linhas.push({ campo, especie: "texto", texto });
  }
  // A detalhe outside the vocabulary is a daemon this panel does not know how to
  // translate, and printing it raw would put a phrase nobody wrote for a screen on it.
  if (ehDetalhe(estado.detalhe)) {
    linhas.push({ campo: "detalhe", especie: "codigo", codigo: estado.detalhe });
  }
  return linhas;
}

// A manifest without one of the languages shows the other, never the word undefined.
// The brands this hub reaches are a fact of the catalog, so the home reads
// them from there and no screen keeps a list of its own that a new driver would leave stale.
// The name of a maker travels as text; whether a drawing exists for it is decided by logos.ts,
// and a brand with no drawing is written in the type of the panel, which is the same
// nominative use.
export function marcasDoCatalogo(catalogo: readonly ItemCatalogo[]): string[] {
  const vistas = new Map<string, string>();
  for (const item of catalogo) {
    const marca = item.marca.trim();
    if (marca === "") continue;
    const chave = marca.toLowerCase();
    if (!vistas.has(chave)) vistas.set(chave, marca);
  }
  return [...vistas.values()].sort((uma, outra) => uma.localeCompare(outra));
}

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

// A SEGREDO is a device credential that never leaves the daemon, so the panel
// refuses to render one even if some answer ever carried it back.
// An air conditioner enters a licence of ar and everything else a licence of
// av; the daemon says so in the manifest and the panel only reads it.
export function produtoDe(item: ItemCatalogo | undefined): Produto {
  return item?.produto === "ar" ? "ar" : "av";
}

export function itensDe(equipamento: Equipamento, lista: Lista): Item[] {
  return equipamento.listas[lista] ?? [];
}

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

// The shortcuts a device publishes are pairs and not words, because the device already
// names each one the way the customer reads it: an id like com.disney.disneyplus-prod on a
// button teaches nobody, and Disney+ does. Anything that is not a pair is dropped, because a
// daemon that broke the shape must not take the reading of the equipment down with it.
export type ItemDeLista = { valor: string; rotulo: string };

export function lerItensDeLista(bruto: unknown): ItemDeLista[] | null {
  if (bruto === undefined) return [];
  if (!Array.isArray(bruto)) return null;
  const achados: ItemDeLista[] = [];
  for (const item of bruto) {
    if (item === null || typeof item !== "object") continue;
    const registro = item as Record<string, unknown>;
    const valor = registro.valor;
    const rotulo = registro.rotulo;
    if (typeof valor !== "string" || typeof rotulo !== "string" || valor === "") continue;
    achados.push({ valor, rotulo });
  }
  return achados;
}

// A unit in a mode that refuses a setpoint (the auto of an LG) is on, has a mode and
// answers no temperature, and the panel says so instead of drawing minus and plus keys the
// device refuses: the same guard as the volume bar of a television whose sound leaves by ARC.
export function semSetpoint(
  estado: Pick<EstadoEquipamento, "ligado" | "modo" | "temperatura">,
): boolean {
  return estado.ligado === true && estado.modo !== null && estado.temperatura === null;
}

// The sweep asks for a range, and the range of the installation is the one the panel was
// opened from, because the hub answers on the LAN at the address in the browser. When the
// panel is opened as localhost, the addresses of the registered equipment say which /24 the
// LAN is; a public or test address is never a LAN.
export function faixaSugerida(
  hostname: string,
  enderecos: readonly string[],
  faixasDoHub: readonly string[] = [],
): string {
  const propria = redeDe(hostname);
  if (propria !== null) return propria;
  const contagem = new Map<string, number>();
  for (const endereco of enderecos) {
    const rede = redeDe(endereco);
    if (rede !== null) contagem.set(rede, (contagem.get(rede) ?? 0) + 1);
  }
  let melhor = "";
  let maior = 0;
  for (const [rede, vezes] of contagem) {
    if (vezes > maior) {
      melhor = rede;
      maior = vezes;
    }
  }
  return melhor || (faixasDoHub[0] ?? "");
}

function redeDe(endereco: string): string | null {
  const casado = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(endereco.trim());
  if (casado === null) return null;
  const [a = 0, b = 0, c = 0, d = 0] = casado.slice(1).map(Number);
  if ([a, b, c, d].some((octeto) => octeto > 255)) return null;
  const privado = a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
  return privado ? `${a}.${b}.${c}.0/24` : null;
}
