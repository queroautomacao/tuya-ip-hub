// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a scene is DATA, so this screen is an editor of a list and never a little language:
// a step names one equipment, one action and one value, plus a wait after it, and that is the
// whole vocabulary. Which actions an equipment offers comes from its manifest, and the daemon
// refuses the list field by field, so the screen never has to decide what a scene may do. What
// the screen does decide is how a step reads: an equipment, then what to do with it, then the
// value; the wait after a step is the interval of the scene unless the step names its own.
// Por que: uma cena é DADO, então esta tela é um editor de uma lista e nunca uma
// linguagenzinha: um passo nomeia um equipamento, uma ação e um valor, mais uma espera depois
// dele, e esse é o vocabulário inteiro. Quais ações um equipamento oferece vêm do manifesto
// dele, e o daemon recusa a lista campo a campo, então a tela nunca precisa decidir o que uma
// cena pode fazer. O que a tela decide é como um passo se lê: um equipamento, depois o que fazer
// com ele, depois o valor; a espera depois de um passo é o intervalo da cena, salvo o passo
// nomear a dele.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  codigoDoErro,
  executarCena,
  lerCatalogo,
  lerCenas,
  lerEquipamentos,
  problemasDoErro,
  salvarCenas,
} from "./api.ts";
import {
  acoesDe,
  comCenas,
  especieDe,
  nomeValido,
  opcoesDe,
  prepararEspera,
  prepararIntervalo,
  prepararValor,
  textoDoValor,
  ultimaEmUso,
  valorPadrao,
  type Cena,
  type PassoDeCena,
} from "./cenas.ts";
import { palavra } from "./ControlesEquipamento.tsx";
import {
  INTERVALO_MS,
  TEMPERATURA_MAXIMA,
  TEMPERATURA_MINIMA,
  type Equipamento,
  type ItemCatalogo,
} from "./equipamentos.ts";
import { t, traduzirErro } from "./i18n";

interface Leitura {
  cenas: Cena[];
  maximo: number;
  acoes: string[];
  espera_maxima_ms: number;
  intervalo_padrao_ms: number;
  passos_maximos: number;
  equipamentos: Equipamento[];
  catalogo: ItemCatalogo[];
  erro: string | null;
}

const VAZIA: Leitura = {
  cenas: [],
  maximo: 0,
  acoes: [],
  espera_maxima_ms: 0,
  intervalo_padrao_ms: 0,
  passos_maximos: 0,
  equipamentos: [],
  catalogo: [],
  erro: null,
};

function nomeDe(equipamento: Equipamento | undefined, identidade: string): string {
  if (equipamento === undefined) return identidade;
  return equipamento.nome || equipamento.identidade;
}

// Why: the interval is typed over, so the field keeps what is being typed as a draft and only
// a number that passes reaches the scene; an empty field is a refusal shown in the footer and
// never a zero written in silence, and leaving the field puts the last good value back.
// Por que: o intervalo é digitado por cima, então o campo guarda o que está sendo digitado como
// rascunho e só um número que passa chega à cena; um campo vazio é recusa mostrada no rodapé e
// nunca um zero gravado em silêncio, e sair do campo devolve o último valor bom.
function CampoIntervalo({
  valor,
  maximo,
  aoMudar,
}: {
  valor: number;
  maximo: number;
  aoMudar: (novo: number | null, codigo: string | null) => void;
}) {
  const [rascunho, setRascunho] = useState<string | null>(null);
  const invalido = useRef(false);
  return (
    <input
      className="curto"
      type="number"
      inputMode="numeric"
      min={0}
      max={maximo}
      aria-label={t("cenas_intervalo")}
      value={rascunho ?? String(valor)}
      onChange={(evento) => {
        setRascunho(evento.target.value);
        const preparo = prepararIntervalo(evento.target.value, maximo);
        invalido.current = !preparo.ok;
        aoMudar(preparo.ok ? (preparo.valor as number) : null, preparo.ok ? null : preparo.codigo);
      }}
      onBlur={() => {
        setRascunho(null);
        // Why: leaving the field puts the last good value back on the screen, so the refusal
        // shown for the draft goes with it instead of lingering over a value that is fine.
        // Por que: sair do campo devolve o último valor bom à tela, então a recusa mostrada
        // para o rascunho vai junto em vez de ficar sobre um valor que está certo.
        if (invalido.current) {
          invalido.current = false;
          aoMudar(null, null);
        }
      }}
    />
  );
}

function Valor({
  passo,
  item,
  equipamento,
  equipamentos,
  aoMudar,
}: {
  passo: PassoDeCena;
  item: ItemCatalogo | undefined;
  equipamento: Equipamento | undefined;
  equipamentos: Equipamento[];
  aoMudar: (valor: unknown, codigo: string | null) => void;
}) {
  const especie = especieDe(passo.acao);
  const texto = textoDoValor(passo.valor);
  const escolher = (bruto: string): void => {
    const preparo = prepararValor(passo.acao, bruto);
    aoMudar(preparo.ok ? preparo.valor : bruto, preparo.ok ? null : preparo.codigo);
  };
  if (especie === "nenhum") return null;
  if (especie === "logico") {
    return (
      <select aria-label={t("cenas_valor")} value={texto} onChange={(evento) => escolher(evento.target.value)}>
        <option value="true">{t("sim")}</option>
        <option value="false">{t("nao")}</option>
      </select>
    );
  }
  if (especie === "grupo") {
    // Why: section 14, a group only exists between equipment of the same tipo, and section 8
    // makes it a group of one licence, so the master offered is one of this tipo with a number
    // on the same licence as the step; the step puts ITS OWN equipment in that master, one
    // member per step, and the empty value takes it out. Nobody joins itself.
    // Por que: seção 14, um grupo só existe entre equipamentos do mesmo tipo, e a seção 8 faz
    // dele um grupo de uma licença, então o mestre oferecido é um deste tipo com número na
    // mesma licença do passo; o passo põe o PRÓPRIO equipamento dele naquele mestre, um membro
    // por passo, e o valor vazio o tira. Ninguém entra em si mesmo.
    const mestres = equipamentos.filter(
      (candidato) =>
        candidato.tipo === equipamento?.tipo &&
        candidato.identidade !== equipamento?.identidade &&
        candidato.numero !== null &&
        candidato.licenca !== null &&
        candidato.licenca === equipamento?.licenca,
    );
    return (
      <select aria-label={t("cenas_valor")} value={texto} onChange={(evento) => escolher(evento.target.value)}>
        <option value="">{t("cenas_grupo_solo")}</option>
        {mestres.map((mestre) => (
          <option key={mestre.identidade} value={mestre.identidade}>
            {`${t("cenas_grupo_mestre")} ${nomeDe(mestre, mestre.identidade)}`}
          </option>
        ))}
      </select>
    );
  }
  if (especie === "escolha") {
    const opcoes = opcoesDe(passo.acao, item, equipamento);
    if (opcoes.length === 0) {
      return (
        <span className="passo-sem-opcao">
          <input
            className="curto"
            type="text"
            aria-label={t("cenas_valor")}
            value={texto}
            onChange={(evento) => escolher(evento.target.value)}
          />
          <span className="dica">{t("cenas_sem_opcao")}</span>
        </span>
      );
    }
    const prefixo = passo.acao === "tecla" ? "tecla" : passo.acao === "vento" ? "vento" : item?.produto === "ar" && passo.acao === "modo" ? "modo_ar" : "";
    // Why: a value saved before the list changed is still the value of the step, so it stays
    // visible as itself instead of silently reading as the first option.
    // Por que: um valor salvo antes de a lista mudar continua sendo o valor do passo, então ele
    // fica visível como ele mesmo em vez de se ler em silêncio como a primeira opção.
    const fora = !opcoes.some((opcao) => opcao.valor === texto);
    return (
      <select aria-label={t("cenas_valor")} value={texto} onChange={(evento) => escolher(evento.target.value)}>
        {fora && <option value={texto}>{texto}</option>}
        {opcoes.map((opcao) => (
          <option key={opcao.valor} value={opcao.valor}>
            {prefixo ? palavra(prefixo, opcao.rotulo) : opcao.rotulo}
          </option>
        ))}
      </select>
    );
  }
  const numero = especie === "numero";
  const temperatura = passo.acao === "temperatura";
  return (
    <input
      className="curto"
      type={numero ? "number" : "text"}
      inputMode={numero ? "numeric" : "text"}
      min={temperatura ? TEMPERATURA_MINIMA : 0}
      max={temperatura ? TEMPERATURA_MAXIMA : 100}
      aria-label={t("cenas_valor")}
      value={texto}
      onChange={(evento) => escolher(evento.target.value)}
    />
  );
}

function Passo({
  passo,
  leitura,
  intervalo,
  aoMudar,
  aoRemover,
}: {
  passo: PassoDeCena;
  leitura: Leitura;
  intervalo: number;
  aoMudar: (novo: PassoDeCena, codigo: string | null) => void;
  aoRemover: () => void;
}) {
  const { equipamentos, catalogo, acoes } = leitura;
  const equipamento = equipamentos.find((candidato) => candidato.identidade === passo.equipamento);
  const item = catalogo.find((candidato) => candidato.tipo === equipamento?.tipo);
  const oferecidas = acoesDe(acoes, item);
  const remover = (
    <button type="button" className="passo-remover" aria-label={t("cenas_remover_passo")} onClick={aoRemover}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
        <path d="M6 6l12 12M18 6 6 18" />
      </svg>
    </button>
  );
  const trocarEquipamento = (identidade: string): void => {
    // Why: moving a step to another equipment keeps what it does when that equipment offers
    // it, so "volume of the receiver" moved to the speaker is "volume of the speaker" and not
    // a reset; an action it does not offer falls to its first one.
    // Por que: mover um passo para outro equipamento mantém o que ele faz quando aquele
    // equipamento oferece isso, então "volume do receiver" levado à caixa é "volume da caixa" e
    // não um recomeço; uma ação que ele não oferece cai na primeira dele.
    const novo = equipamentos.find((candidato) => candidato.identidade === identidade);
    const itemNovo = catalogo.find((candidato) => candidato.tipo === novo?.tipo);
    const disponiveis = acoesDe(acoes, itemNovo);
    const acao = disponiveis.includes(passo.acao) ? passo.acao : (disponiveis[0] ?? passo.acao);
    const valor = acao === passo.acao ? passo.valor : valorPadrao(acao, opcoesDe(acao, itemNovo, novo));
    aoMudar({ ...passo, equipamento: identidade, acao, valor }, null);
  };
  const trocarAcao = (acao: string): void => {
    aoMudar({ ...passo, acao, valor: valorPadrao(acao, opcoesDe(acao, item, equipamento)) }, null);
  };
  return (
    <li className="passo">
      <div className="passo-alvo">
        <select
          aria-label={t("cenas_passo_equipamento")}
          value={passo.equipamento}
          onChange={(evento) => trocarEquipamento(evento.target.value)}
        >
          {equipamento === undefined && <option value={passo.equipamento}>{passo.equipamento}</option>}
          {equipamentos.map((candidato) => (
            <option key={candidato.identidade} value={candidato.identidade}>
              {nomeDe(candidato, candidato.identidade)}
            </option>
          ))}
        </select>
        <select
          aria-label={t("cenas_passo_o_que")}
          value={passo.acao}
          onChange={(evento) => trocarAcao(evento.target.value)}
        >
          {!oferecidas.includes(passo.acao) && <option value={passo.acao}>{palavra("acao", passo.acao)}</option>}
          {oferecidas.map((acao) => (
            <option key={acao} value={acao}>
              {palavra("acao", acao)}
            </option>
          ))}
        </select>
      </div>
      <Valor
        passo={passo}
        item={item}
        equipamento={equipamento}
        equipamentos={equipamentos}
        aoMudar={(valor, codigo) => aoMudar({ ...passo, valor }, codigo)}
      />
      <label className="passo-espera">
        <span className="texto-suave">{t("cenas_e_depois")}</span>
        <input
          className="curto"
          type="number"
          inputMode="numeric"
          min={0}
          max={leitura.espera_maxima_ms}
          aria-label={t("cenas_espera")}
          placeholder={String(intervalo)}
          value={passo.espera_ms === null ? "" : String(passo.espera_ms)}
          onChange={(evento) => {
            const preparo = prepararEspera(evento.target.value, leitura.espera_maxima_ms);
            const espera = preparo.ok ? (preparo.valor as number | null) : passo.espera_ms;
            aoMudar({ ...passo, espera_ms: espera }, preparo.ok ? null : preparo.codigo);
          }}
        />
        <span className="texto-suave">{t("cenas_ms")}</span>
      </label>
      {equipamento === undefined && (
        <span className="erro">{traduzirErro("cena_equipamento_desconhecido")}</span>
      )}
      {remover}
    </li>
  );
}

function novoPasso(leitura: Leitura): PassoDeCena | null {
  const equipamento = leitura.equipamentos[0];
  if (equipamento === undefined) return null;
  const item = leitura.catalogo.find((candidato) => candidato.tipo === equipamento.tipo);
  const acao = acoesDe(leitura.acoes, item)[0] ?? "ligar";
  return {
    equipamento: equipamento.identidade,
    acao,
    valor: valorPadrao(acao, opcoesDe(acao, item, equipamento)),
    espera_ms: null,
  };
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
        <label className="cena-intervalo">
          <span>{t("cenas_intervalo")}</span>
          <CampoIntervalo
            valor={cena.intervalo_ms}
            maximo={leitura.espera_maxima_ms}
            aoMudar={(intervalo, codigo) => aoMudar({ ...cena, intervalo_ms: intervalo ?? cena.intervalo_ms }, codigo)}
          />
          <span>{t("cenas_ms")}</span>
          <span className="texto-suave cena-intervalo-ajuda">{t("cenas_intervalo_ajuda")}</span>
        </label>
      )}
      {!vazia && (
        <ol className="passos">
          {cena.passos.map((passo, indice) => (
            <Passo
              key={`${indice}-${passo.equipamento}-${passo.acao}`}
              passo={passo}
              leitura={leitura}
              intervalo={cena.intervalo_ms}
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
        disabled={cheia || leitura.equipamentos.length === 0}
        onClick={() => {
          const passo = novoPasso(leitura);
          if (passo !== null) aoMudar({ ...cena, passos: [...cena.passos, passo] }, null);
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
  const [todas, setTodas] = useState(false);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      const [cenas, equipamentos, catalogo] = await Promise.all([
        lerCenas(),
        lerEquipamentos(),
        lerCatalogo(),
      ]);
      setLeitura({
        cenas: comCenas(cenas.cenas, cenas.maximo, cenas.intervalo_padrao_ms),
        maximo: cenas.maximo,
        acoes: cenas.acoes,
        espera_maxima_ms: cenas.espera_maxima_ms,
        intervalo_padrao_ms: cenas.intervalo_padrao_ms,
        passos_maximos: cenas.passos_maximos,
        equipamentos,
        catalogo,
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
  // Why: thirty two cards is a wall; the screen shows the scenes in use plus one free slot, and
  // the whole list on request.
  // Por que: trinta e dois cartões são um muro; a tela mostra as cenas em uso mais uma vaga
  // livre, e a lista inteira a pedido.
  const visiveis = todas ? cenas : cenas.slice(0, Math.min(cenas.length, ultimaEmUso(cenas) + 1));

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
      {leitura.erro === null && leitura.maximo > 0 && leitura.equipamentos.length === 0 && (
        <p className="dica">{t("cenas_sem_equipamento")}</p>
      )}
      <ul className="cenas">
        {visiveis.map((cena) => (
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
      {(todas || visiveis.length < cenas.length) && (
        <button type="button" className="botao secundario" onClick={() => setTodas(!todas)}>
          {todas ? t("cenas_menos") : t("cenas_mais")}
        </button>
      )}
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
