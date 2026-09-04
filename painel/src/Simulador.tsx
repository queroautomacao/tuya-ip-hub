// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the customer never sees this panel; they see an app built on the data points of
// section 8. This screen draws that app inside a phone, over the same snapshot and the same
// sets the bridge uses, so the integrator sees the installation the way the customer will,
// before the platform side exists. Nothing here is a mock: a slider moved is a set on the
// bus, and a report from the speaker moves it back.
// Por que: o cliente nunca vê este painel; ele vê um app construído sobre os data points da
// seção 8. Esta tela desenha esse app dentro de um celular, sobre o mesmo snapshot e os mesmos
// sets que a ponte usa, para o integrador ver a instalação como o cliente vai ver, antes de
// existir o lado da plataforma. Nada aqui é maquete: um slider movido é um set no barramento,
// e um report da caixa o move de volta.

import { useCallback, useEffect, useState } from "react";
import { ajustarDp, codigoDoErro, lerCatalogo, lerCenas, lerDps, lerZonas } from "./api.ts";
import { FUNCAO_DA_CENA, type Cena, type ItemDoMapa } from "./cenas.ts";
import { type ItemCatalogo } from "./equipamentos.ts";
import { t, traduzirErro } from "./i18n";
import { SOLO, gruposPossiveis, type LeituraDeZonas, type Zona } from "./zonas.ts";

interface Leitura {
  zonas: LeituraDeZonas | null;
  dps: Record<string, unknown>;
  mapa: ItemDoMapa[];
  cenas: Cena[];
  catalogo: ItemCatalogo[];
  erro: string | null;
}

const VAZIA: Leitura = { zonas: null, dps: {}, mapa: [], cenas: [], catalogo: [], erro: null };

// Why: the app of the customer polls its device a few times a minute, and the simulator
// wants to feel like it: three seconds is close to the report cadence of section 8.
// Por que: o app do cliente consulta o aparelho algumas vezes por minuto, e o simulador quer
// parecer com ele: três segundos ficam perto da cadência de report da seção 8.
const INTERVALO_MS = 3_000;
const HORA_DE_VITRINE = "9:41";

function numero(valor: unknown): number | null {
  return typeof valor === "number" && Number.isFinite(valor) ? valor : null;
}

function texto(valor: unknown): string {
  return typeof valor === "string" ? valor : "";
}

function CartaoZona({
  zona,
  dps,
  mapa,
  ocupado,
  aoAjustar,
}: {
  zona: Zona;
  dps: Record<string, unknown>;
  mapa: ItemDoMapa[];
  ocupado: boolean;
  aoAjustar: (dpid: number, valor: unknown) => void;
}) {
  const [arrastando, setArrastando] = useState<number | null>(null);
  const online = dps[String(zona.dps.online)] === true;
  const volume = numero(dps[String(zona.dps.volume)]) ?? 0;
  const tocando = dps[String(zona.dps.play)] === true;
  const titulo = texto(dps[String(zona.dps.tocando)]);
  const entrada = texto(dps[String(zona.dps.entrada)]);
  const presets = mapa.find((item) => item.dpid === zona.dps.preset)?.valores ?? [];
  const nome = zona.nome || zona.identidade;
  const mostrado = arrastando ?? volume;
  return (
    <section className={`app-zona ${online ? "" : "app-zona-offline"}`}>
      <header>
        <h4>{nome}</h4>
        <span className="app-estado">
          {online ? titulo || entrada : t("simulador_offline")}
        </span>
      </header>
      <div className="app-transporte">
        <button
          type="button"
          className="app-play"
          disabled={!online || ocupado}
          aria-label={tocando ? t("zonas_pausar") : t("zonas_tocar")}
          onClick={() => aoAjustar(zona.dps.play, !tocando)}
        >
          {tocando ? (
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M7 5h4v14H7zM13 5h4v14h-4z" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M8 5v14l11-7z" />
            </svg>
          )}
        </button>
        <label className="app-volume">
          <span className="app-volume-valor">{mostrado}</span>
          <input
            type="range"
            min={0}
            max={100}
            value={mostrado}
            disabled={!online || ocupado}
            aria-label={`${t("zonas_funcao_volume")} ${nome}`}
            onChange={(evento) => setArrastando(Number(evento.target.value))}
            onPointerUp={() => {
              if (arrastando !== null) aoAjustar(zona.dps.volume, arrastando);
              setArrastando(null);
            }}
            onKeyUp={() => {
              if (arrastando !== null) aoAjustar(zona.dps.volume, arrastando);
              setArrastando(null);
            }}
          />
        </label>
      </div>
      {zona.entradas.length > 0 && (
        <div className="app-fichas" role="group" aria-label={t("zonas_funcao_entrada")}>
          {zona.entradas.map((opcao) => (
            <button
              key={opcao}
              type="button"
              className="app-ficha"
              aria-pressed={opcao === entrada}
              disabled={!online || ocupado}
              onClick={() => aoAjustar(zona.dps.entrada, opcao)}
            >
              {opcao}
            </button>
          ))}
        </div>
      )}
      {presets.length > 0 && (
        <div className="app-fichas" role="group" aria-label={t("zonas_funcao_preset")}>
          {presets.map((preset, indice) => (
            <button
              key={preset}
              type="button"
              className="app-ficha app-preset"
              disabled={!online || ocupado}
              onClick={() => aoAjustar(zona.dps.preset, preset)}
            >
              {indice + 1}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

export default function Simulador({ nomeInstalacao }: { nomeInstalacao: string }) {
  const [leitura, setLeitura] = useState<Leitura>(VAZIA);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      const [zonas, snapshot, cenas, catalogo] = await Promise.all([
        lerZonas(),
        lerDps(),
        lerCenas(),
        lerCatalogo(),
      ]);
      setLeitura({ zonas, dps: snapshot.dps, mapa: snapshot.mapa, cenas: cenas.cenas, catalogo, erro: null });
    } catch (falha) {
      setLeitura((anterior) => ({ ...anterior, erro: codigoDoErro(falha) }));
    }
  }, []);

  useEffect(() => {
    void recarregar();
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);

  async function ajustar(dpid: number, valor: unknown): Promise<void> {
    setOcupado(true);
    try {
      await ajustarDp(dpid, valor);
      setErro(null);
      await recarregar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setOcupado(false);
    }
  }

  const zonas = (leitura.zonas?.zonas ?? []).filter((zona) => zona.identidade !== "");
  const grupos = leitura.zonas === null ? [] : gruposPossiveis(leitura.zonas.zonas, leitura.catalogo);
  const grupoAtual = leitura.zonas?.grupo ?? SOLO;
  const dpCena = leitura.mapa.find((item) => item.funcao === FUNCAO_DA_CENA)?.dpid;
  const cenas = leitura.cenas.filter((cena) => cena.passos.length > 0);
  return (
    <>
      <div className="tela-cabeca">
        <div>
          <h2>{t("simulador_titulo")}</h2>
          <p>{t("simulador_intro")}</p>
        </div>
      </div>
      {leitura.erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(leitura.erro)}
        </p>
      )}
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      <div className="telefone" role="region" aria-label={t("simulador_titulo")}>
        <div className="telefone-tela">
          <div className="app-status" aria-hidden="true">
            <span>{HORA_DE_VITRINE}</span>
            <span>●●●</span>
          </div>
          <header className="app-cabeca">
            <h3>{nomeInstalacao || t("produto")}</h3>
          </header>
          <div className="app-corpo">
            <h4 className="app-secao">{t("simulador_zonas")}</h4>
            {leitura.zonas !== null && zonas.length === 0 && (
              <p className="app-vazio">{t("simulador_vazio")}</p>
            )}
            {zonas.map((zona) => (
              <CartaoZona
                key={zona.zona}
                zona={zona}
                dps={leitura.dps}
                mapa={leitura.mapa}
                ocupado={ocupado}
                aoAjustar={(dpid, valor) => void ajustar(dpid, valor)}
              />
            ))}
            {leitura.zonas !== null && zonas.length > 1 && (
              <>
                <h4 className="app-secao">{t("simulador_grupo")}</h4>
                <div className="app-fichas" role="group" aria-label={t("simulador_grupo")}>
                  {grupos.map((valor) => (
                    <button
                      key={valor}
                      type="button"
                      className="app-ficha"
                      aria-pressed={valor === grupoAtual}
                      disabled={ocupado}
                      onClick={() => void ajustar(leitura.zonas?.dp_grupo ?? 0, valor)}
                    >
                      {valor === SOLO ? t("zonas_solo") : `${t("zonas_bloco")} ${valor.slice(5)}`}
                    </button>
                  ))}
                </div>
              </>
            )}
            <h4 className="app-secao">{t("simulador_cenas")}</h4>
            {cenas.length === 0 && <p className="app-vazio">{t("simulador_sem_cena")}</p>}
            {cenas.length > 0 && (
              <div className="app-cenas">
                {cenas.map((cena) => (
                  <button
                    key={cena.numero}
                    type="button"
                    className="app-cena"
                    disabled={ocupado || dpCena === undefined}
                    onClick={() => dpCena !== undefined && void ajustar(dpCena, `cena${cena.numero}`)}
                  >
                    <span className="app-cena-numero">{cena.numero}</span>
                    <span>{cena.nome || t("cenas_sem_nome")}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
