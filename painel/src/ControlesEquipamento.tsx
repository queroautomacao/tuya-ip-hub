// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: section 6, the controls of an equipment are the capabilities the manifest declares and
// nothing else, drawn the way a remote draws them: power as two keys, volume as a slider with
// mute beside it, transport as round keys, the input as a list, the keys of a TV as a keypad,
// the setpoint of an air conditioner as a number with its mode and fan beside it. Every press
// is one action on the daemon, and the state read back is what the screen shows, never the
// press.
// Por que: seção 6, os controles de um equipamento são as capacidades que o manifesto declara
// e nada mais, desenhados como um controle remoto os desenha: energia em duas teclas, volume
// como slider com o mudo ao lado, transporte em teclas redondas, a entrada como lista, as
// teclas de uma TV como um teclado, o setpoint de um ar condicionado como número com o modo e o
// vento ao lado. Toda apertada é uma ação no daemon, e o estado lido de volta é o que a tela
// mostra, nunca a apertada.

import { useEffect, useState, type ReactNode } from "react";
import {
  TEMPERATURA_MAXIMA,
  TEMPERATURA_MINIMA,
  itensDe,
  paineis,
  prepararTemperatura,
  prepararTexto,
  type Capacidade,
  type Equipamento,
  type EstadoEquipamento,
  type Item,
  type ItemCatalogo,
  type Preparo,
  type Transporte,
} from "./equipamentos.ts";
import { t, type Chave } from "./i18n";

// Why: the slider shows the value it was released at until the equipment reads it back, or
// for this long when it never does, so the thumb does not bounce to the old volume during the
// request and a device that ignored the command still lets go of the value.
// Por que: o slider mostra o valor em que foi solto até o equipamento o ler de volta, ou por
// este tempo quando nunca lê, então o cursor não pula para o volume antigo durante a
// requisição e um aparelho que ignorou o comando ainda solta o valor.
const ESPERA_DE_LEITURA_MS = 4_000;

const ICONES: Record<Transporte, string> = {
  anterior: "M6 6h2v12H6zm3.5 6 8.5 6V6z",
  tocar: "M8 5v14l11-7z",
  pausar: "M7 5h4v14H7zM13 5h4v14h-4z",
  proxima: "M16 6h2v12h-2zM6 18l8.5-6L6 6z",
};

// Why: a word of the vocabulary of section 6 has a phrase in the dictionary, and a word this
// panel does not know yet prints itself instead of an empty button.
// Por que: uma palavra do vocabulário da seção 6 tem frase no dicionário, e uma palavra que
// este painel ainda não conhece imprime a si mesma em vez de um botão vazio.
export function palavra(prefixo: string, valor: string): string {
  const texto = t(`${prefixo}_${valor}` as Chave) as string | undefined;
  return texto ?? valor;
}

function Grupo({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <div className="controle-grupo">
      <span className="controle-rotulo">{rotulo}</span>
      {children}
    </div>
  );
}

function Fichas({
  rotulo,
  opcoes,
  atual,
  ocupado,
  aoEscolher,
}: {
  rotulo: string;
  opcoes: Item[];
  atual: string | null;
  ocupado: boolean;
  aoEscolher: (valor: string) => void;
}) {
  return (
    <div className="fichas" role="group" aria-label={rotulo}>
      {opcoes.map((opcao) => (
        <button
          key={opcao.valor}
          type="button"
          className="ficha"
          aria-pressed={atual === opcao.valor}
          disabled={ocupado}
          onClick={() => aoEscolher(opcao.valor)}
        >
          {opcao.rotulo}
        </button>
      ))}
    </div>
  );
}

export default function Controles({
  capacidades,
  estado,
  item,
  equipamento,
  ocupado,
  aoExecutar,
}: {
  capacidades: string[];
  estado: EstadoEquipamento;
  item?: ItemCatalogo;
  equipamento?: Equipamento;
  ocupado: boolean;
  aoExecutar: (acao: string, preparo: Preparo) => void;
}) {
  const [arrastando, setArrastando] = useState<number | null>(null);
  const [pendente, setPendente] = useState<number | null>(null);
  const [fonteLivre, setFonteLivre] = useState("");
  const [extra, setExtra] = useState("");
  const [graus, setGraus] = useState<string | null>(null);
  useEffect(() => {
    setPendente(null);
  }, [estado.volume]);
  useEffect(() => {
    if (pendente === null) return undefined;
    const temporizador = window.setTimeout(() => setPendente(null), ESPERA_DE_LEITURA_MS);
    return () => window.clearTimeout(temporizador);
  }, [pendente]);
  const painel = paineis(capacidades);
  if (!painel.algum) return null;
  const simples = (acao: Capacidade): void => aoExecutar(acao, { ok: true, valor: null });
  const volume = arrastando ?? pendente ?? estado.volume ?? 0;
  const soltar = (): void => {
    if (arrastando !== null) {
      setPendente(arrastando);
      aoExecutar("volume", { ok: true, valor: arrastando });
    }
    setArrastando(null);
  };
  const deAr = item?.produto === "ar";
  const entradas = equipamento === undefined ? [] : itensDe(equipamento, "entradas");
  const atalhos = equipamento === undefined ? [] : itensDe(equipamento, "atalhos");
  const modos = equipamento === undefined ? [] : itensDe(equipamento, "modos");
  const temperatura = graus ?? String(estado.temperatura ?? 22);
  const doVocabulario = (prefixo: string, palavras: readonly string[]): Item[] =>
    palavras.map((valor) => ({ valor, rotulo: palavra(prefixo, valor) }));
  return (
    <div className="painel-controles">
      {painel.energia.length > 0 && (
        <Grupo rotulo={t("controles_energia")}>
          <div className="segmentos" role="group" aria-label={t("controles_energia")}>
            {painel.energia.map((acao) => (
              <button
                key={acao}
                type="button"
                disabled={ocupado}
                aria-pressed={estado.ligado === (acao === "ligar")}
                onClick={() => simples(acao)}
              >
                {t(`acao_${acao}` as const)}
              </button>
            ))}
          </div>
        </Grupo>
      )}
      {painel.temperatura && (
        <Grupo rotulo={t("controles_temperatura")}>
          <div className="controle-linha">
            <input
              className="curto"
              type="number"
              inputMode="numeric"
              min={TEMPERATURA_MINIMA}
              max={TEMPERATURA_MAXIMA}
              value={temperatura}
              aria-label={t("acao_temperatura")}
              onChange={(evento) => setGraus(evento.target.value)}
            />
            <button
              type="button"
              className="botao secundario"
              disabled={ocupado}
              onClick={() => {
                aoExecutar("temperatura", prepararTemperatura(temperatura));
                setGraus(null);
              }}
            >
              {t("acao_aplicar")}
            </button>
          </div>
        </Grupo>
      )}
      {painel.modo && (
        <Grupo rotulo={t("controles_modo")}>
          {deAr ? (
            <Fichas
              rotulo={t("controles_modo")}
              opcoes={doVocabulario("modo_ar", item?.modos ?? [])}
              atual={estado.modo}
              ocupado={ocupado}
              aoEscolher={(valor) => aoExecutar("modo", { ok: true, valor })}
            />
          ) : modos.length > 0 ? (
            <Fichas
              rotulo={t("controles_modo")}
              opcoes={modos}
              atual={estado.modo}
              ocupado={ocupado}
              aoEscolher={(valor) => aoExecutar("modo", { ok: true, valor })}
            />
          ) : (
            <p className="dica">{t("controles_sem_lista")}</p>
          )}
        </Grupo>
      )}
      {painel.vento && (
        <Grupo rotulo={t("controles_vento")}>
          <Fichas
            rotulo={t("controles_vento")}
            opcoes={doVocabulario("vento", item?.ventos ?? [])}
            atual={estado.vento}
            ocupado={ocupado}
            aoEscolher={(valor) => aoExecutar("vento", { ok: true, valor })}
          />
        </Grupo>
      )}
      {(painel.volume || painel.mudo) && (
        <Grupo rotulo={t("controles_volume")}>
          <div className="controle-volume">
            {painel.volume && (
              <>
                <span className="controle-volume-valor">{volume}</span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={volume}
                  aria-label={t("acao_volume")}
                  onChange={(evento) => setArrastando(Number(evento.target.value))}
                  onPointerUp={soltar}
                  onKeyUp={soltar}
                />
              </>
            )}
            {painel.mudo && (
              <button
                type="button"
                className="botao secundario"
                disabled={ocupado}
                aria-pressed={estado.mudo === true}
                onClick={() => aoExecutar("mudo", { ok: true, valor: !(estado.mudo ?? false) })}
              >
                {t("acao_mudo")}
              </button>
            )}
          </div>
        </Grupo>
      )}
      {painel.transporte.length > 0 && (
        <Grupo rotulo={t("controles_transporte")}>
          <div className="transporte" role="group" aria-label={t("controles_transporte")}>
            {painel.transporte.map((acao) => (
              <button
                key={acao}
                type="button"
                className="botao-icone"
                disabled={ocupado}
                aria-label={t(`acao_${acao}` as const)}
                title={t(`acao_${acao}` as const)}
                onClick={() => simples(acao)}
              >
                <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d={ICONES[acao]} />
                </svg>
              </button>
            ))}
          </div>
        </Grupo>
      )}
      {painel.fonte && (
        <Grupo rotulo={t("controles_fonte")}>
          {entradas.length > 0 ? (
            <Fichas
              rotulo={t("controles_fonte")}
              opcoes={entradas}
              atual={estado.fonte}
              ocupado={ocupado}
              aoEscolher={(valor) => aoExecutar("fonte", { ok: true, valor })}
            />
          ) : estado.fontes.length > 0 ? (
            <select
              value={estado.fonte ?? ""}
              disabled={ocupado}
              aria-label={t("acao_fonte")}
              onChange={(evento) => {
                if (evento.target.value) aoExecutar("fonte", { ok: true, valor: evento.target.value });
              }}
            >
              <option value="">{t("acao_fonte")}</option>
              {estado.fontes.map((fonte) => (
                <option key={fonte} value={fonte}>
                  {fonte}
                </option>
              ))}
            </select>
          ) : (
            <div className="controle-linha">
              <input
                type="text"
                value={fonteLivre}
                placeholder={t("controles_fonte_livre")}
                aria-label={t("acao_fonte")}
                onChange={(evento) => setFonteLivre(evento.target.value)}
              />
              <button
                type="button"
                className="botao secundario"
                disabled={ocupado}
                onClick={() => aoExecutar("fonte", prepararTexto(fonteLivre))}
              >
                {t("acao_aplicar")}
              </button>
            </div>
          )}
        </Grupo>
      )}
      {painel.atalho && (
        <Grupo rotulo={t("controles_atalhos")}>
          {atalhos.length > 0 ? (
            <Fichas
              rotulo={t("controles_atalhos")}
              opcoes={atalhos}
              atual={null}
              ocupado={ocupado}
              aoEscolher={(valor) => aoExecutar("atalho", { ok: true, valor })}
            />
          ) : (
            <p className="dica">{t("controles_sem_lista")}</p>
          )}
        </Grupo>
      )}
      {painel.teclas && (
        <Grupo rotulo={t("controles_teclas")}>
          <div className="teclado" role="group" aria-label={t("controles_teclas")}>
            {(item?.teclas ?? []).map((tecla) => (
              <button
                key={tecla}
                type="button"
                className="ficha"
                disabled={ocupado}
                onClick={() => aoExecutar("tecla", { ok: true, valor: tecla })}
              >
                {palavra("tecla", tecla)}
              </button>
            ))}
          </div>
        </Grupo>
      )}
      {painel.extra && (
        <Grupo rotulo={t("controles_extra")}>
          <div className="controle-linha">
            <input
              type="text"
              value={extra}
              aria-label={t("acao_comando_extra")}
              onChange={(evento) => setExtra(evento.target.value)}
            />
            <button
              type="button"
              className="botao secundario"
              disabled={ocupado}
              onClick={() => aoExecutar("comando_extra", prepararTexto(extra))}
            >
              {t("acao_enviar")}
            </button>
          </div>
          <p className="dica">{t("controles_extra_ajuda")}</p>
        </Grupo>
      )}
    </div>
  );
}
