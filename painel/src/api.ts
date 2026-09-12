// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { corpoDeCenas, lerLeituraDeCenas, type Cena, type LeituraDeCenas } from "./cenas.ts";
import {
  RESULTADOS_AUTENTICACAO,
  lerAchado,
  lerEquipamento,
  lerItemCatalogo,
  lerLista,
  type Achado,
  type Equipamento,
  type ItemCatalogo,
  type ResultadoAutenticacao,
} from "./equipamentos.ts";
import type { CorpoCadastro } from "./formulario.ts";
import {
  lerLeituraDeLicencas,
  lerLicenca,
  lerQr,
  lerSnapshot,
  type CorpoDeLicenca,
  type LeituraDeLicencas,
  type Licenca,
  type Qr,
  type Snapshot,
} from "./licencas.ts";
import { guardar, ler, limpar } from "./sessao.ts";

export const SENHA_MINIMA = 8;

// The daemon answers a stable code and never a human phrase, so these three
// stand in when there is no code to read: no answer, a body outside the contract,
// and an HTTP status the daemon did not label.
export const CODIGO_SEM_RESPOSTA = "sem_resposta";
export const CODIGO_CORPO_INVALIDO = "corpo_invalido";
export const CODIGO_ERRO_HTTP = "erro_http";

// These two are the only answers that mean the session itself is gone. Every
// other 401 (a wrong current password, for one) is about the body of the request,
// and dropping the token there signs the user out of a session the daemon honours.
export const CODIGOS_SESSAO_MORTA: readonly string[] = ["sessao_invalida", "nao_autenticado"];

const PRAZO_MS = 5_000;
// A backup derives its key with six hundred thousand iterations on an ARM board.
const PRAZO_BACKUP_MS = 60_000;

// A discovery sweep waits for answers from the whole network for seconds on end,
// so the login deadline would cut it short before the last device answered.
const PRAZO_VARREDURA_MS = 20_000;
// A range of 254 addresses on a small board takes longer than any other request of the panel,
// and giving up early is what put a second sweep on top of the first.
const PRAZO_BUSCA_MS = 120_000;

// A refusal may name the FIELD it refused, so the panel points at the input instead of
// at the form; a route that names none answers one code and an empty list.
export interface Problema {
  campo: string;
  codigo: string;
}

export function lerProblema(valor: unknown): Problema | null {
  if (!ehObjeto(valor) || typeof valor.codigo !== "string" || !valor.codigo) return null;
  const campo = valor.campo === undefined ? "" : valor.campo;
  if (typeof campo !== "string") return null;
  return { campo, codigo: valor.codigo };
}

export class ErroApi extends Error {
  readonly code: string;
  readonly status: number;
  readonly problemas: readonly Problema[];

  constructor(code: string, status = 0, problemas: readonly Problema[] = []) {
    super(code);
    this.name = "ErroApi";
    this.code = code;
    this.status = status;
    this.problemas = problemas;
  }
}

export interface Estado {
  configurado: boolean;
  versao: string;
  schema_version: number;
  nome_instalacao: string;
}

export interface Credencial {
  token: string;
  expira_em_s: number;
}

export interface Sessao {
  expira_em_s: number;
}

type Objeto = Record<string, unknown>;

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

// Where this panel is being served from. On the local network it is the root of the hub;
// through the relay of the remote access it is /h/<endereco>/, and every call has to carry
// that prefix or it lands on the relay instead of on the hub behind it. Read once, from the
// address of the page itself, so nothing has to be configured for it.
// Guarded, because this module is imported by tests that run in node, where there is no
// window; without the guard the whole file fails to load and every test of it disappears.
const BASE =
  typeof window === "undefined" ? "/" : window.location.pathname.replace(/[^/]*$/, "");

export function comBase(caminho: string): string {
  return BASE + caminho.replace(/^\//, "");
}

async function pedir(
  caminho: string,
  metodo: string,
  corpo?: unknown,
  prazoMs: number = PRAZO_MS,
): Promise<Objeto> {
  const cabecalhos: Record<string, string> = { Accept: "application/json" };
  const token = ler();
  if (token) cabecalhos.Authorization = `Bearer ${token}`;
  if (corpo !== undefined) cabecalhos["Content-Type"] = "application/json";

  let resposta: Response;
  try {
    resposta = await fetch(comBase(caminho), {
      method: metodo,
      headers: cabecalhos,
      body: corpo === undefined ? undefined : JSON.stringify(corpo),
      cache: "no-store",
      signal: AbortSignal.timeout(prazoMs),
    });
  } catch {
    throw new ErroApi(CODIGO_SEM_RESPOSTA);
  }

  const dados: unknown = await resposta.json().catch(() => null);
  const code = ehObjeto(dados) && typeof dados.code === "string" ? dados.code : null;
  // A token the daemon no longer accepts would make every later request
  // answer 401, and the panel would keep showing a session that does not exist.
  if (resposta.status === 401 && code !== null && CODIGOS_SESSAO_MORTA.includes(code)) limpar();
  if (ehObjeto(dados) && dados.ok === true) return dados;
  const problemas = ehObjeto(dados) ? lerLista(dados.problemas, lerProblema) : null;
  throw new ErroApi(
    code ?? (resposta.ok ? CODIGO_CORPO_INVALIDO : CODIGO_ERRO_HTTP),
    resposta.status,
    problemas ?? [],
  );
}

function aceitarCredencial(dados: Objeto): Credencial {
  if (typeof dados.token !== "string" || !dados.token || typeof dados.expira_em_s !== "number") {
    throw new ErroApi(CODIGO_CORPO_INVALIDO);
  }
  guardar(dados.token);
  return { token: dados.token, expira_em_s: dados.expira_em_s };
}

export async function lerEstado(): Promise<Estado> {
  const dados = await pedir("/api/estado", "GET");
  if (
    typeof dados.configurado !== "boolean" ||
    typeof dados.versao !== "string" ||
    typeof dados.schema_version !== "number" ||
    typeof dados.nome_instalacao !== "string"
  ) {
    throw new ErroApi(CODIGO_CORPO_INVALIDO);
  }
  return {
    configurado: dados.configurado,
    versao: dados.versao,
    schema_version: dados.schema_version,
    nome_instalacao: dados.nome_instalacao,
  };
}

export async function tomarPosse(senha: string): Promise<Credencial> {
  return aceitarCredencial(await pedir("/api/posse", "POST", { senha }));
}

export async function entrar(
  senha: string,
  codigo?: string,
  acesso?: string,
): Promise<Credencial> {
  // Neither code travels unless there is one to send: a hub that never enrolled a second
  // factor answers the password alone, and the code of the window only exists through the
  // relay. The login page adds each field after the daemon says it wants it.
  const corpo: Record<string, string> = { senha };
  if (codigo) corpo.codigo = codigo;
  if (acesso) corpo.acesso = acesso;
  return aceitarCredencial(await pedir("/api/entrar", "POST", corpo));
}

export interface Remoto {
  // Whether the miniApp of the maker has written the home of the app this hub belongs to;
  // without it the door is not shown, because the relay would have nothing to check.
  disponivel: boolean;
  home: string;
  armado: boolean;
  restante_s: number;
  codigo: string;
  url: string;
  // Whether the socket to the relay is up right now; the window can be open before it is.
  conectado: boolean;
  // The relay asked the registry of the maker and the home of this hub was not there.
  recusado: boolean;
  otp_ativo: boolean;
  pronto: boolean;
  faltando: string[];
}

export async function lerRemoto(): Promise<Remoto> {
  return (await pedir("/api/remoto", "GET")) as unknown as Remoto;
}

export async function armarRemoto(armar: boolean): Promise<Remoto> {
  return (await pedir("/api/remoto", "POST", { armar })) as unknown as Remoto;
}

export interface Pareamento {
  segredo: string;
  uri: string;
}

export async function comecarOtp(): Promise<Pareamento> {
  return (await pedir("/api/otp", "POST")) as unknown as Pareamento;
}

export async function confirmarOtp(codigo: string): Promise<void> {
  await pedir("/api/otp/confirmar", "POST", { codigo });
}

export async function removerOtp(senha: string): Promise<void> {
  await pedir("/api/otp", "DELETE", { senha });
}

export interface Seguranca {
  cofre: { presa_ao_hardware: boolean; ilegiveis: number };
  nomes: string[];
  porta: number;
}

export async function lerSeguranca(): Promise<Seguranca> {
  return (await pedir("/api/seguranca", "GET")) as unknown as Seguranca;
}

export interface Agendamento {
  cena: number;
  hora: string;
  dias: number[];
  ativo: boolean;
}

export interface LeituraDeAgenda {
  fuso: string;
  fuso_efetivo: string;
  agora: string;
  agendamentos: Agendamento[];
  maximo: number;
}

export async function lerAgendamentos(): Promise<LeituraDeAgenda> {
  return (await pedir("/api/agendamentos", "GET")) as unknown as LeituraDeAgenda;
}

export async function salvarAgendamentos(
  fuso: string,
  agendamentos: readonly Agendamento[],
): Promise<LeituraDeAgenda> {
  const corpo = { fuso, agendamentos };
  return (await pedir("/api/agendamentos", "POST", corpo)) as unknown as LeituraDeAgenda;
}

// The one answer of the daemon that is a file and not JSON, so it does not go through
// pedir: the body is the backup, and the name of the file comes in the headers.
export async function exportarBackup(senha: string): Promise<{ blob: Blob; nome: string }> {
  const cabecalhos: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/octet-stream, application/json",
  };
  const token = ler();
  if (token) cabecalhos.Authorization = `Bearer ${token}`;
  let resposta: Response;
  try {
    resposta = await fetch(comBase("/api/backup"), {
      method: "POST",
      headers: cabecalhos,
      body: JSON.stringify({ senha }),
      cache: "no-store",
      signal: AbortSignal.timeout(PRAZO_BACKUP_MS),
    });
  } catch {
    throw new ErroApi(CODIGO_SEM_RESPOSTA);
  }
  if (!resposta.ok) {
    const dados: unknown = await resposta.json().catch(() => null);
    const code = ehObjeto(dados) && typeof dados.code === "string" ? dados.code : null;
    if (resposta.status === 401 && code !== null && CODIGOS_SESSAO_MORTA.includes(code)) limpar();
    throw new ErroApi(code ?? CODIGO_ERRO_HTTP, resposta.status);
  }
  const disposicao = resposta.headers.get("Content-Disposition") ?? "";
  const achado = /filename="([^"]+)"/.exec(disposicao);
  return { blob: await resposta.blob(), nome: achado?.[1] ?? "tuya-ip-hub.iphub" };
}

export async function restaurarBackup(
  arquivo: string,
  senha: string,
): Promise<{ nome_instalacao: string }> {
  const dados = await pedir("/api/restaurar", "POST", { senha, arquivo }, PRAZO_BACKUP_MS);
  return { nome_instalacao: typeof dados.nome_instalacao === "string" ? dados.nome_instalacao : "" };
}

export async function trocarSenha(atual: string, nova: string): Promise<Credencial> {
  const dados = await pedir("/api/senha", "POST", { senha_atual: atual, senha_nova: nova });
  return aceitarCredencial(dados);
}

export async function lerSessao(): Promise<Sessao> {
  const dados = await pedir("/api/sessao", "GET");
  if (typeof dados.expira_em_s !== "number") throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return { expira_em_s: dados.expira_em_s };
}

export async function renomearInstalacao(nome: string): Promise<string> {
  const dados = await pedir("/api/instalacao", "POST", { nome });
  if (typeof dados.nome_instalacao !== "string") throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return dados.nome_instalacao;
}

export async function reiniciar(): Promise<void> {
  await pedir("/api/reiniciar", "POST");
}

export interface Atualizacao {
  atual: string;
  ultima: string | null;
  disponivel: boolean;
  verificada: boolean;
}

export async function lerAtualizacao(): Promise<Atualizacao> {
  const dados = await pedir("/api/atualizacao", "GET", undefined, PRAZO_VARREDURA_MS);
  const ultima = dados.ultima;
  if (
    typeof dados.atual !== "string" ||
    (ultima !== null && typeof ultima !== "string") ||
    typeof dados.disponivel !== "boolean" ||
    typeof dados.verificada !== "boolean"
  ) {
    throw new ErroApi(CODIGO_CORPO_INVALIDO);
  }
  return { atual: dados.atual, ultima, disponivel: dados.disponivel, verificada: dados.verificada };
}

export async function sair(): Promise<void> {
  // The daemon revoking the token is the point, but a failure to reach it
  // must not leave the browser holding a credential the user asked to drop.
  try {
    await pedir("/api/sair", "POST");
  } finally {
    limpar();
  }
}

// An identity is a uuid, a MAC or a serial, and encoding it keeps a slash or a
// question mark inside one from addressing another route.
function rotaDoEquipamento(identidade: string): string {
  return `/api/equipamentos/${encodeURIComponent(identidade)}`;
}

// The diary is the last lines the daemon kept in memory, read whole on every tick
// because it is small on purpose and because a page of a log is not worth a cursor.
export interface LinhaDoLog {
  t: number;
  nivel: string;
  origem: string;
  onde: string;
  texto: string;
}

export interface Log {
  linhas: LinhaDoLog[];
  descartadas: number;
  teto: number;
}

function lerLinhaDoLog(valor: unknown): LinhaDoLog | null {
  if (typeof valor !== "object" || valor === null) return null;
  const bruto = valor as Record<string, unknown>;
  const { t: instante, nivel, origem, onde, texto } = bruto;
  if (typeof instante !== "number" || !Number.isFinite(instante)) return null;
  if (typeof nivel !== "string" || typeof origem !== "string") return null;
  if (typeof onde !== "string" || typeof texto !== "string") return null;
  return { t: instante, nivel, origem, onde, texto };
}

export async function lerLog(): Promise<Log> {
  const dados = await pedir("/api/log", "GET");
  const linhas: LinhaDoLog[] = [];
  if (Array.isArray(dados.linhas)) {
    for (const bruto of dados.linhas) {
      const linha = lerLinhaDoLog(bruto);
      if (linha !== null) linhas.push(linha);
    }
  }
  const { descartadas, teto } = dados as Record<string, unknown>;
  return {
    linhas,
    descartadas: typeof descartadas === "number" ? descartadas : 0,
    teto: typeof teto === "number" ? teto : 0,
  };
}

export async function lerCatalogo(): Promise<ItemCatalogo[]> {
  const dados = await pedir("/api/catalogo", "GET");
  const catalogo = lerLista(dados.catalogo, lerItemCatalogo);
  if (catalogo === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return catalogo;
}

export async function lerEquipamentos(): Promise<Equipamento[]> {
  const dados = await pedir("/api/equipamentos", "GET");
  const equipamentos = lerLista(dados.equipamentos, lerEquipamento);
  if (equipamentos === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return equipamentos;
}

export async function cadastrarEquipamento(corpo: CorpoCadastro): Promise<void> {
  await pedir("/api/equipamentos", "POST", corpo);
}

// The route takes the identity from the path and the body may only repeat it, so a
// correction changes the address, the name or a field and never the key of the
// registration; an absent secret keeps the credential the daemon already stores.
export async function atualizarEquipamento(
  identidade: string,
  corpo: CorpoCadastro,
): Promise<void> {
  await pedir(rotaDoEquipamento(identidade), "POST", corpo);
}

export async function removerEquipamento(identidade: string): Promise<void> {
  await pedir(rotaDoEquipamento(identidade), "DELETE");
}

export async function executarAcao(
  identidade: string,
  acao: string,
  valor: unknown,
): Promise<void> {
  // The action of a speaker that follows a master goes through the book of
  // licences, behind whatever set of that licence is in flight, so it gets the long deadline
  // the licence routes already use.
  await pedir(`${rotaDoEquipamento(identidade)}/acao`, "POST", { acao, valor }, PRAZO_VARREDURA_MS);
}

export async function autenticarEquipamento(identidade: string): Promise<ResultadoAutenticacao> {
  const dados = await pedir(`${rotaDoEquipamento(identidade)}/autenticar`, "POST");
  const resultado = RESULTADOS_AUTENTICACAO.find((esperado) => esperado === dados.resultado);
  if (resultado === undefined) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return resultado;
}


// A driver of the cloud of a maker commands one device out of an account, and the id of
// that device exists only inside the account. The panel asks the daemon what the credentials
// the operator just typed can reach, and shows him the list; the token travels in the body of
// this call the same way it travels in a registration, and nothing here keeps it.
export interface AparelhoDaConta {
  id: string;
  nome: string;
}

export async function listarAparelhosDaConta(
  tipo: string,
  campos: Record<string, string>,
): Promise<{ campo: string; aparelhos: AparelhoDaConta[] }> {
  const dados = await pedir(`/api/catalogo/${encodeURIComponent(tipo)}/aparelhos`, "POST", {
    campos,
  });
  const campo = typeof dados.campo === "string" ? dados.campo : "";
  const brutos = Array.isArray(dados.aparelhos) ? dados.aparelhos : null;
  if (campo === "" || brutos === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  const aparelhos: AparelhoDaConta[] = [];
  for (const bruto of brutos) {
    if (bruto === null || typeof bruto !== "object") throw new ErroApi(CODIGO_CORPO_INVALIDO);
    const registro = bruto as Record<string, unknown>;
    if (typeof registro.id !== "string" || typeof registro.nome !== "string") {
      throw new ErroApi(CODIGO_CORPO_INVALIDO);
    }
    if (registro.id !== "") aparelhos.push({ id: registro.id, nome: registro.nome });
  }
  return { campo, aparelhos };
}

// The range is what makes the sweep work where multicast does not cross, which is every
// hub inside a bridge network. Empty means the sweep the segment answers on its
// own, so the button keeps working with nothing typed.
export async function varrer(faixa = ""): Promise<Achado[]> {
  const corpo = faixa.trim() === "" ? undefined : { faixa: faixa.trim() };
  const dados = await pedir("/api/descoberta", "POST", corpo, PRAZO_BUSCA_MS);
  const achados = lerLista(dados.achados, lerAchado);
  if (achados === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return achados;
}
// The id of a licence is a short identifier the daemon refuses before it becomes a path,
// and encoding it keeps a hand written one from addressing another route anyway.
function rotaDaLicenca(id: string): string {
  return `/api/licencas/${encodeURIComponent(id)}`;
}

export async function lerLicencas(): Promise<LeituraDeLicencas> {
  const leitura = lerLeituraDeLicencas(await pedir("/api/licencas", "GET"));
  if (leitura === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return leitura;
}

function aceitarLicenca(dados: Objeto): Licenca {
  const licenca = lerLicenca(dados.licenca);
  if (licenca === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return licenca;
}

export async function criarLicenca(corpo: CorpoDeLicenca): Promise<Licenca> {
  return aceitarLicenca(await pedir("/api/licencas", "POST", corpo));
}

// A field the body omits keeps the stored value, the chave included, so an edit that only
// fixes the name never erases the credential of the device.
export async function atualizarLicenca(id: string, corpo: CorpoDeLicenca): Promise<Licenca> {
  return aceitarLicenca(await pedir(rotaDaLicenca(id), "POST", corpo));
}

export async function removerLicenca(id: string): Promise<void> {
  await pedir(rotaDaLicenca(id), "DELETE");
}

// The whole order travels, because the POSITION of a number is the contract;
// sending one slot alone would need an index anyway and a shorter list would move an equipment
// from number 2 to number 1 in every automation the customer already built.
// A set on a licence may reach a whole group of speakers, each with a deadline of its
// own on the daemon, so these three wait the long deadline instead of giving up first.
export async function salvarNumeros(id: string, numeros: readonly string[]): Promise<void> {
  await pedir(`${rotaDaLicenca(id)}/numeros`, "POST", { numeros }, PRAZO_VARREDURA_MS);
}

export async function lerDps(id: string): Promise<Snapshot> {
  const snapshot = lerSnapshot(await pedir(`${rotaDaLicenca(id)}/dps`, "GET"));
  if (snapshot === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return snapshot;
}

export async function ajustarDp(id: string, dpid: number, valor: unknown): Promise<void> {
  await pedir(`${rotaDaLicenca(id)}/dp/${dpid}`, "POST", { v: valor }, PRAZO_VARREDURA_MS);
}

// A master carries several slaves and the customer picks them one by one, so
// the panel sends the set it wants; without membros the daemon keeps the meaning of the data
// point of the bus, which is every speaker of the tipo of the master.
export async function definirGrupo(
  id: string,
  valor: number,
  membros?: number[],
): Promise<void> {
  const corpo = membros === undefined ? { v: valor } : { v: valor, membros };
  await pedir(`${rotaDaLicenca(id)}/grupo`, "POST", corpo, PRAZO_VARREDURA_MS);
}

export async function lerQrDaLicenca(id: string): Promise<Qr> {
  const qr = lerQr(await pedir(`${rotaDaLicenca(id)}/qr`, "GET"));
  if (qr === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return qr;
}

export async function lerCenas(): Promise<LeituraDeCenas> {
  const leitura = lerLeituraDeCenas(await pedir("/api/cenas", "GET"));
  if (leitura === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return leitura;
}

export async function salvarCenas(cenas: readonly Cena[]): Promise<void> {
  await pedir("/api/cenas", "POST", { cenas: corpoDeCenas(cenas) });
}

export async function executarCena(numero: number): Promise<void> {
  await pedir(`/api/cenas/${numero}/executar`, "POST");
}

export function codigoDoErro(erro: unknown): string {
  return erro instanceof ErroApi ? erro.code : CODIGO_ERRO_HTTP;
}

export function problemasDoErro(erro: unknown): readonly Problema[] {
  return erro instanceof ErroApi ? erro.problemas : [];
}

// The daemon measures the password in code points, the way python len does, and
// String.length measures UTF-16 units, so an astral character counts twice here and
// once there; the panel would accept a password the daemon then refuses.
export function senhaCurta(senha: string): boolean {
  return [...senha].length < SENHA_MINIMA;
}

// The private networks of the host that runs the hub, first the one that looks most like the LAN.
export async function lerRede(): Promise<string[]> {
  const dados = await pedir("/api/rede", "GET");
  const faixas = dados.faixas;
  if (!Array.isArray(faixas)) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return faixas.filter((faixa): faixa is string => typeof faixa === "string");
}
