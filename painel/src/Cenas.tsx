// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a scene is DATA, so this screen is an editor of a list and never a little language:
// a step names one data point and one value, plus a wait after it, and that is the whole
// vocabulary. Which data points may be named comes from the daemon, and the daemon refuses
// the list field by field, so the screen never has to decide what a scene may do. What the
// screen does decide is how a step reads: a zone, then what to do with it, then the value.
// Por que: uma cena é DADO, então esta tela é um editor de uma lista e nunca uma
// linguagenzinha: um passo nomeia um data point e um valor, mais uma espera depois dele, e
// esse é o vocabulário inteiro. Quais data points podem ser nomeados vêm do daemon, e o daemon
// recusa a lista campo a campo, então a tela nunca precisa decidir o que uma cena pode fazer.
// O que a tela decide é como um passo se lê: uma zona, depois o que fazer com ela, depois o
// valor.

import { useCallback, useEffect, useState } from "react";
import {
  codigoDoErro,
  executarCena,
  lerCenas,
  lerDps,
  lerZonas,
  problemasDoErro,
  salvarCenas,
} from "./api.ts";
import {
  ajustaveis,
  comCenas,
  itemDoDp,
  nomeValido,
  prepararEspera,
  prepararValor,
  textoDoValor,
  valorPadrao,
  type Cena,
  type ItemDoMapa,
  type PassoDeCena,
} from "./cenas.ts";
import { INTERVALO_MS } from "./equipamentos.ts";
import { t, traduzirErro, type Chave } from "./i18n";
import type { Zona } from "./zonas.ts";

interface Leitura {
  cenas: Cena[];
  maximo: number;
  espera_maxima_ms: number;
  passos_maximos: number;
  mapa: ItemDoMapa[];
  zonas: Zona[];
  erro: string | null;
}

const VAZIA: Leitura = {
  cenas: [],
  maximo: 0,
  espera_maxima_ms: 0,
  passos_maximos: 0,
  mapa: [],
  zonas: [],
  erro: null,
};

// Why: the function of a data point comes from the daemon as a stable word, so one this
// panel does not know prints the word itself instead of an empty label; a phrase for it is
// added here the day section 8 grows one.
// Por que: a função de um data point vem do daemon como palavra estável, então uma que este
// painel não conhece imprime a própria palavra em vez de um rótulo vazio; a frase para ela é
// acrescentada aqui no dia em que a seção 8 ganhar uma.
const CHAVE_DA_FUNCAO: Record<string, Chave> = {
  volume: "cenas_funcao_volume",
  play: "cenas_funcao_play",
  preset: "cenas_funcao_preset",
  entrada: "cenas_funcao_entrada",
  grupo: "cenas_funcao_grupo",
};

function rotuloDaFuncao(funcao: string): string {
  const chave = CHAVE_DA_FUNCAO[funcao];
  return chave === undefined ? funcao : t(chave);
}

function rotuloDaZona(numero: number, zonas: readonly Zona[]): string {
  if (numero === 0) return t("cenas_global");
  const zona = zonas.find((candidata) => candidata.zona === numero);
  const nome = zona === undefined ? "" : zona.nome || zona.identidade;
  return nome ? `${t("zonas_bloco")} ${numero}: ${nome}` : `${t("zonas_bloco")} ${numero}`;
}

function zonasDoMapa(mapa: readonly ItemDoMapa[]): number[] {
  return [...new Set(mapa.map((item) => item.zona))].sort((a, b) => a - b);
}

function Valor({
  item,
  passo,
  aoMudar,
}: {
  item: ItemDoMapa;
  passo: PassoDeCena;
  aoMudar: (valor: unknown, codigo: string | null) => void;
}) {
  const texto = textoDoValor(passo.valor);
  const escolher = (bruto: string): void => {
    const preparo = prepararValor(item, bruto);
    aoMudar(preparo.ok ? preparo.valor : bruto, preparo.ok ? null : preparo.codigo);
  };
  if (item.tipo === "bool") {
    return (
      <select aria-label={t("cenas_valor")} value={texto} onChange={(evento) => escolher(evento.target.value)}>
        <option value="true">{item.funcao === "play" ? t("zonas_tocar") : t("sim")}</option>
        <option value="false">{item.funcao === "play" ? t("zonas_pausar") : t("nao")}</option>
      </select>
    );
  }
  if (item.tipo === "enum" && item.valores.length > 0) {
    return (
      <select aria-label={t("cenas_valor")} value={texto} onChange={(evento) => escolher(evento.target.value)}>
        {item.valores.map((valor) => (
          <option key={valor} value={valor}>
            {valor}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      className="curto"
      type={item.tipo === "value" ? "number" : "text"}
      inputMode={item.tipo === "value" ? "numeric" : "text"}
      min={0}
      max={100}
      aria-label={t("cenas_valor")}
      value={texto}
      onChange={(evento) => escolher(evento.target.value)}
    />
  );
}

function Passo({
  passo,
  mapa,
  zonas,
  maximoDeEspera,
  aoMudar,
  aoRemover,
}: {
  passo: PassoDeCena;
  mapa: ItemDoMapa[];
  zonas: Zona[];
  maximoDeEspera: number;
  aoMudar: (novo: PassoDeCena, codigo: string | null) => void;
  aoRemover: () => void;
}) {
  const item = itemDoDp(mapa, passo.dpid);
  const remover = (
    <button type="button" className="passo-remover" aria-label={t("cenas_remover_passo")} onClick={aoRemover}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
        <path d="M6 6l12 12M18 6 6 18" />
      </svg>
    </button>
  );
  if (item === undefined) {
    return (
      <li className="passo">
        <span className="erro">{traduzirErro("cena_dp_desconhecido")}</span>
        {remover}
      </li>
    );
  }
  const funcoes = mapa.filter((candidato) => candidato.zona === item.zona);
  const trocar = (escolhido: ItemDoMapa | undefined): void => {
    if (escolhido === undefined) return;
    aoMudar({ ...passo, dpid: escolhido.dpid, valor: valorPadrao(escolhido) }, null);
  };
  return (
    <li className="passo">
      <div className="passo-alvo">
        <select
          aria-label={t("cenas_passo_zona")}
          value={String(item.zona)}
          onChange={(evento) => {
            // Why: moving a step to another zone keeps what it does when that zone offers it,
            // so "volume of zone 1" dragged to zone 2 is "volume of zone 2" and not a reset.
            // Por que: mover um passo para outra zona mantém o que ele faz quando aquela zona
            // oferece isso, então "volume da zona 1" levado à zona 2 é "volume da zona 2" e
            // não um recomeço.
            const zona = Number(evento.target.value);
            const mesma = mapa.find((c) => c.zona === zona && c.funcao === item.funcao);
            trocar(mesma ?? mapa.find((c) => c.zona === zona));
          }}
        >
          {zonasDoMapa(mapa).map((zona) => (
            <option key={zona} value={String(zona)}>
              {rotuloDaZona(zona, zonas)}
            </option>
          ))}
        </select>
        <select
          aria-label={t("cenas_passo_o_que")}
          value={String(item.dpid)}
          onChange={(evento) => trocar(itemDoDp(mapa, Number(evento.target.value)))}
        >
          {funcoes.map((candidato) => (
            <option key={candidato.dpid} value={String(candidato.dpid)}>
              {rotuloDaFuncao(candidato.funcao)}
            </option>
          ))}
        </select>
      </div>
      <Valor item={item} passo={passo} aoMudar={(valor, codigo) => aoMudar({ ...passo, valor }, codigo)} />
      <label className="passo-espera">
        <span className="texto-suave">{t("cenas_e_depois")}</span>
        <input
          className="curto"
          type="number"
          inputMode="numeric"
          min={0}
          max={maximoDeEspera}
          aria-label={t("cenas_espera")}
          value={String(passo.espera_ms)}
          onChange={(evento) => {
            const preparo = prepararEspera(evento.target.value, maximoDeEspera);
            const espera = preparo.ok ? (preparo.valor as number) : passo.espera_ms;
            aoMudar({ ...passo, espera_ms: espera }, preparo.ok ? null : preparo.codigo);
          }}
        />
        <span className="texto-suave">{t("cenas_ms")}</span>
      </label>
      {remover}
    </li>
  );
}

function CartaoCena({
  cena,
  leitura,
  ocupado,
  aoMudar,
  aoExecutar,
}: {
  cena: Cena;
  leitura: Leitura;
  ocupado: boolean;
  aoMudar: (nova: Cena, codigo: string | null) => void;
  aoExecutar: () => void;
}) {
  const { mapa, zonas } = leitura;
  const cheia = cena.passos.length >= leitura.passos_maximos;
  const vazia = cena.passos.length === 0;
  return (
    <li className={`cena ${vazia ? "cena-vazia" : ""}`}>
      <div className="cena-cabeca">
        <span className="cena-numero" aria-label={`${t("cenas_numero")} ${cena.numero}`}>
          {cena.numero}
        </span>
        <input
          className="cena-nome"
          type="text"
          placeholder={t("cenas_sem_nome")}
          aria-label={t("cenas_nome")}
          value={cena.nome}
          onChange={(evento) =>
            aoMudar(
              { ...cena, nome: evento.target.value },
              nomeValido(evento.target.value) ? null : "cena_nome_invalido",
            )
          }
        />
        {cena.em_curso && <span className="etiqueta">{t("cenas_em_curso")}</span>}
        <button
          type="button"
          className="botao cena-executar"
          disabled={ocupado || vazia}
          aria-label={`${t("cenas_executar")} ${cena.numero}`}
          onClick={aoExecutar}
        >
          <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M8 5v14l11-7z" />
          </svg>
          <span>{t("cenas_executar")}</span>
        </button>
      </div>
      {vazia ? (
        <p className="texto-suave cena-dica">{t("cenas_vazia")}</p>
      ) : (
        <ol className="passos">
          {cena.passos.map((passo, indice) => (
            <Passo
              key={`${indice}-${passo.dpid}`}
              passo={passo}
              mapa={mapa}
              zonas={zonas}
              maximoDeEspera={leitura.espera_maxima_ms}
              aoMudar={(novo, codigo) =>
                aoMudar(
                  { ...cena, passos: cena.passos.map((atual, posicao) => (posicao === indice ? novo : atual)) },
                  codigo,
                )
              }
              aoRemover={() =>
                aoMudar({ ...cena, passos: cena.passos.filter((_ignorado, posicao) => posicao !== indice) }, null)
              }
            />
          ))}
        </ol>
      )}
      <button
        type="button"
        className="botao secundario"
        disabled={cheia || mapa.length === 0}
        onClick={() => {
          const primeiro = mapa[0];
          if (primeiro === undefined) return;
          aoMudar(
            { ...cena, passos: [...cena.passos, { dpid: primeiro.dpid, valor: valorPadrao(primeiro), espera_ms: 0 }] },
            null,
          );
        }}
      >
        + {t("cenas_novo_passo")}
      </button>
    </li>
  );
}

export default function Cenas() {
  const [leitura, setLeitura] = useState<Leitura>(VAZIA);
  const [rascunho, setRascunho] = useState<Cena[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [problemas, setProblemas] = useState<readonly { campo: string; codigo: string }[]>([]);
  const [salvo, setSalvo] = useState(false);
  const [ocupado, setOcupado] = useState(false);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      const [cenas, snapshot, zonas] = await Promise.all([lerCenas(), lerDps(), lerZonas()]);
      setLeitura({
        cenas: comCenas(cenas.cenas, cenas.maximo),
        maximo: cenas.maximo,
        espera_maxima_ms: cenas.espera_maxima_ms,
        passos_maximos: cenas.passos_maximos,
        mapa: ajustaveis(snapshot.mapa),
        zonas: zonas.zonas,
        erro: null,
      });
    } catch (falha) {
      setLeitura((anterior) => ({ ...anterior, erro: codigoDoErro(falha) }));
    }
  }, []);

  useEffect(() => {
    void recarregar();
    // Why: the cycle refreshes what is running, and a draft in the middle of being typed is
    // never overwritten by it; whoever is editing keeps what they wrote until they save.
    // Por que: o ciclo atualiza o que está rodando, e um rascunho sendo digitado nunca é
    // sobrescrito por ele; quem edita mantém o que escreveu até salvar.
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);

  const cenas = rascunho ?? leitura.cenas;

  function mudar(numero: number, nova: Cena, codigo: string | null): void {
    setSalvo(false);
    setErro(codigo);
    setRascunho(cenas.map((cena) => (cena.numero === numero ? nova : cena)));
  }

  async function chamar(trabalho: () => Promise<void>): Promise<void> {
    setOcupado(true);
    try {
      await trabalho();
      setErro(null);
      setProblemas([]);
      await recarregar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
      setProblemas(problemasDoErro(falha));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <>
      <div className="tela-cabeca">
        <div>
          <h2>{t("cenas_titulo")}</h2>
          <p>{t("cenas_intro")}</p>
        </div>
      </div>
      {leitura.erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(leitura.erro)}
        </p>
      )}
      <ul className="cenas">
        {cenas.map((cena) => (
          <CartaoCena
            key={cena.numero}
            cena={cena}
            leitura={leitura}
            ocupado={ocupado}
            aoMudar={(nova, codigo) => mudar(cena.numero, nova, codigo)}
            aoExecutar={() => void chamar(() => executarCena(cena.numero))}
          />
        ))}
      </ul>
      {(rascunho !== null || salvo || erro !== null || problemas.length > 0) && (
        <div className="cenas-rodape" role="region" aria-live="polite">
          {rascunho !== null && (
            <div className="acoes-largas">
              <button
                type="button"
                className="botao"
                disabled={ocupado}
                onClick={() =>
                  void chamar(async () => {
                    await salvarCenas(cenas);
                    setRascunho(null);
                    setSalvo(true);
                  })
                }
              >
                {ocupado ? t("enviando") : t("cenas_salvar")}
              </button>
              <button
                type="button"
                className="botao secundario"
                disabled={ocupado}
                onClick={() => {
                  setRascunho(null);
                  setErro(null);
                  setProblemas([]);
                }}
              >
                {t("cenas_descartar")}
              </button>
            </div>
          )}
          {salvo && (
            <p className="sucesso" role="status">
              {t("cenas_salvo")}
            </p>
          )}
          {erro !== null && (
            <p className="erro" role="alert">
              {traduzirErro(erro)}
            </p>
          )}
          {problemas.length > 0 && (
            <ul className="problemas">
              {problemas.map((problema) => (
                <li key={`${problema.campo}-${problema.codigo}`}>
                  <code>{problema.campo}</code> {traduzirErro(problema.codigo)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}
