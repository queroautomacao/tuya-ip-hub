// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: section 6, the controls of an equipment are the capabilities the manifest declares and
// nothing else, drawn the way a remote draws them: power as two keys, volume as a slider with
// mute beside it, transport as round keys, the input as a list. Every press is one action on
// the daemon, and the state read back is what the screen shows, never the press.
// Por que: seção 6, os controles de um equipamento são as capacidades que o manifesto declara
// e nada mais, desenhados como um controle remoto os desenha: energia em duas teclas, volume
// como slider com o mudo ao lado, transporte em teclas redondas, a entrada como lista. Toda
// apertada é uma ação no daemon, e o estado lido de volta é o que a tela mostra, nunca a
// apertada.

import { useState, type ReactNode } from "react";
import {
  paineis,
  prepararTexto,
  type Capacidade,
  type EstadoEquipamento,
  type Preparo,
  type Transporte,
} from "./equipamentos.ts";
import { t } from "./i18n";

const ICONES: Record<Transporte, string> = {
  anterior: "M6 6h2v12H6zm3.5 6 8.5 6V6z",
  tocar: "M8 5v14l11-7z",
  pausar: "M7 5h4v14H7zM13 5h4v14h-4z",
  proxima: "M16 6h2v12h-2zM6 18l8.5-6L6 6z",
};

function Grupo({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <div className="controle-grupo">
      <span className="controle-rotulo">{rotulo}</span>
      {children}
    </div>
  );
}

export default function Controles({
  capacidades,
  estado,
  ocupado,
  aoExecutar,
}: {
  capacidades: string[];
  estado: EstadoEquipamento;
  ocupado: boolean;
  aoExecutar: (acao: string, preparo: Preparo) => void;
}) {
  const [arrastando, setArrastando] = useState<number | null>(null);
  const [fonteLivre, setFonteLivre] = useState("");
  const [extra, setExtra] = useState("");
  const painel = paineis(capacidades);
  if (!painel.algum) return null;
  const simples = (acao: Capacidade): void => aoExecutar(acao, { ok: true, valor: null });
  const volume = arrastando ?? estado.volume ?? 0;
  const soltar = (): void => {
    if (arrastando !== null) aoExecutar("volume", { ok: true, valor: arrastando });
    setArrastando(null);
  };
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
                  disabled={ocupado}
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
          {estado.fontes.length > 0 ? (
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
