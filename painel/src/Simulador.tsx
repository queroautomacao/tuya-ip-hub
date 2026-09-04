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
import { ajustarDp, codigoDoErro, lerCatalogo, lerCenas, lerDps, lerBlocos } from "./api.ts";
import { FUNCAO_DA_CENA, type Cena, type ItemDoMapa } from "./cenas.ts";
import { type ItemCatalogo } from "./equipamentos.ts";
import { t, traduzirErro } from "./i18n";
import { SOLO, controlesDaBloco, gruposPossiveis, type LeituraDeBlocos, type Bloco } from "./blocos.ts";

interface Leitura {
  blocos: LeituraDeBlocos | null;
  dps: Record<string, unknown>;
  mapa: ItemDoMapa[];
  cenas: Cena[];
  catalogo: ItemCatalogo[];
  erro: string | null;
}

const VAZIA: Leitura = { blocos: null, dps: {}, mapa: [], cenas: [], catalogo: [], erro: null };

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

function CartaoBloco({
  bloco,
  item,
  dps,
  mapa,
  ocupado,
  aoAjustar,
}: {
  bloco: Bloco;
  item: ItemCatalogo | undefined;
  dps: Record<string, unknown>;
  mapa: ItemDoMapa[];
  ocupado: boolean;
  aoAjustar: (dpid: number, valor: unknown) => void;
}) {
  const [arrastando, setArrastando] = useState<number | null>(null);
  // Why: section 8, DP 102 is play/pause for an equipment with transport and the power switch
  // for any other, so the same key is drawn as play or as power from what the driver declares.
  // Por que: seção 8, o DP 102 é play/pause para um equipamento com transporte e a chave de
  // ligar para qualquer outro, então a mesma tecla é desenhada como play ou como energia a
  // partir do que o driver declara.
  const controles = controlesDaBloco(bloco, item);
  const play = controles.find((controle) => controle.funcao === "play");
  const temVolume = controles.some((controle) => controle.funcao === "volume");
  const online = dps[String(bloco.dps.online)] === true;
  const volume = numero(dps[String(bloco.dps.volume)]) ?? 0;
  const tocando = dps[String(bloco.dps.play)] === true;
  const titulo = texto(dps[String(bloco.dps.tocando)]);
  const entrada = texto(dps[String(bloco.dps.entrada)]);
  const presets = mapa.find((item) => item.dpid === bloco.dps.preset)?.valores ?? [];
  const nome = bloco.nome || bloco.identidade;
  const mostrado = arrastando ?? volume;
  return (
    <section className={`app-bloco ${online ? "" : "app-bloco-offline"}`}>
      <header>
        <h4>{nome}</h4>
        <span className="app-estado">
          {online ? titulo || entrada : t("simulador_offline")}
        </span>
      </header>
      <div className="app-transporte">
        {play?.especie === "ligar" && (
          <button
            type="button"
            className="app-play app-energia"
            disabled={!online || ocupado}
            aria-pressed={tocando}
            aria-label={tocando ? t("simulador_desligar") : t("simulador_ligar")}
            onClick={() => aoAjustar(bloco.dps.play, !tocando)}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" aria-hidden="true">
              <path d="M12 3v9" />
              <path d="M6.3 6.5a8 8 0 1 0 11.4 0" />
            </svg>
          </button>
        )}
        {play?.especie === "alternar" && (
          <button
            type="button"
            className="app-play"
            disabled={!online || ocupado}
            aria-label={tocando ? t("blocos_pausar") : t("blocos_tocar")}
            onClick={() => aoAjustar(bloco.dps.play, !tocando)}
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
        )}
        {temVolume && (
        <label className="app-volume">
          <span className="app-volume-valor">{mostrado}</span>
          <input
            type="range"
            min={0}
            max={100}
            value={mostrado}
            disabled={!online || ocupado}
            aria-label={`${t("blocos_funcao_volume")} ${nome}`}
            onChange={(evento) => setArrastando(Number(evento.target.value))}
            onPointerUp={() => {
              if (arrastando !== null) aoAjustar(bloco.dps.volume, arrastando);
              setArrastando(null);
            }}
            onKeyUp={() => {
              if (arrastando !== null) aoAjustar(bloco.dps.volume, arrastando);
              setArrastando(null);
            }}
          />
        </label>
        )}
      </div>
      {bloco.entradas.length > 0 && (
        <div className="app-fichas" role="group" aria-label={t("blocos_funcao_entrada")}>
          {bloco.entradas.map((opcao) => (
            <button
              key={opcao}
              type="button"
              className="app-ficha"
              aria-pressed={opcao === entrada}
              disabled={!online || ocupado}
              onClick={() => aoAjustar(bloco.dps.entrada, opcao)}
            >
              {opcao}
            </button>
          ))}
        </div>
      )}
      {presets.length > 0 && (
        <div className="app-fichas" role="group" aria-label={t("blocos_funcao_preset")}>
          {presets.map((preset, indice) => (
            <button
              key={preset}
              type="button"
              className="app-ficha app-preset"
              disabled={!online || ocupado}
              onClick={() => aoAjustar(bloco.dps.preset, preset)}
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
      const [blocos, snapshot, cenas, catalogo] = await Promise.all([
        lerBlocos(),
        lerDps(),
        lerCenas(),
        lerCatalogo(),
      ]);
      setLeitura({ blocos, dps: snapshot.dps, mapa: snapshot.mapa, cenas: cenas.cenas, catalogo, erro: null });
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

  const blocos = (leitura.blocos?.blocos ?? []).filter((bloco) => bloco.identidade !== "");
  const grupos = leitura.blocos === null ? [] : gruposPossiveis(leitura.blocos.blocos, leitura.catalogo);
  const grupoAtual = leitura.blocos?.grupo ?? SOLO;
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
            <h4 className="app-secao">{t("simulador_blocos")}</h4>
            {leitura.blocos !== null && blocos.length === 0 && (
              <p className="app-vazio">{t("simulador_vazio")}</p>
            )}
            {blocos.map((bloco) => (
              <CartaoBloco
                key={bloco.bloco}
                bloco={bloco}
                item={leitura.catalogo.find((candidato) => candidato.tipo === bloco.tipo)}
                dps={leitura.dps}
                mapa={leitura.mapa}
                ocupado={ocupado}
                aoAjustar={(dpid, valor) => void ajustar(dpid, valor)}
              />
            ))}
            {leitura.blocos !== null && blocos.length > 1 && (
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
                      onClick={() => void ajustar(leitura.blocos?.dp_grupo ?? 0, valor)}
                    >
                      {valor === SOLO ? t("blocos_solo") : `${t("blocos_bloco")} ${valor.slice(5)}`}
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
