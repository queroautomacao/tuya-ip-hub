// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the customer never sees this panel; they see an app built on the data points of
// section 8, one device per licence. This screen draws that app inside a phone, over the same
// snapshot and the same sets the bridge uses, so the integrator sees the installation the way
// the customer will, before the platform side exists. Nothing here is a mock: a slider moved is
// a set on the bus, a chip pressed is a string on the command channel, and a report from the
// equipment moves it back.
// Por que: o cliente nunca vê este painel; ele vê um app construído sobre os data points da
// seção 8, um dispositivo por licença. Esta tela desenha esse app dentro de um celular, sobre o
// mesmo snapshot e os mesmos sets que a ponte usa, para o integrador ver a instalação como o
// cliente vai ver, antes de existir o lado da plataforma. Nada aqui é maquete: um slider movido
// é um set no barramento, uma ficha apertada é uma string no canal de comando, e um report do
// equipamento o move de volta.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ajustarDp,
  codigoDoErro,
  lerCatalogo,
  lerCenas,
  lerDps,
  lerEquipamentos,
  lerLicencas,
} from "./api.ts";
import { FUNCAO_DA_CENA, type Cena } from "./cenas.ts";
import { palavra } from "./ControlesEquipamento.tsx";
import {
  TEMPERATURA_MAXIMA,
  TEMPERATURA_MINIMA,
  type Equipamento,
  type ItemCatalogo,
} from "./equipamentos.ts";
import { t, traduzirErro } from "./i18n";
import {
  SOLO,
  bitDe,
  comandoDe,
  controlesDoNumero,
  gruposPossiveis,
  nomeDoNumero,
  paresDe,
  type ItemDoMapa,
  type Licenca,
  type Numero,
  type Snapshot,
} from "./licencas.ts";

interface Leitura {
  licencas: Licenca[];
  equipamentos: Equipamento[];
  catalogo: ItemCatalogo[];
  cenas: Cena[];
  snapshot: Snapshot | null;
  // Why: the snapshot is the slice of ONE licence, so it travels with the id it belongs to
  // and a phone showing another licence never draws it.
  // Por que: o snapshot é a fatia de UMA licença, então viaja com o id a que pertence e um
  // celular mostrando outra licença nunca o desenha.
  snapshotDe: string | null;
  erro: string | null;
}

const VAZIA: Leitura = {
  licencas: [],
  equipamentos: [],
  catalogo: [],
  cenas: [],
  snapshot: null,
  snapshotDe: null,
  erro: null,
};

// Why: the app of the customer polls its device a few times a minute, and the simulator
// wants to feel like it: three seconds is close to the report cadence of section 8.
// Por que: o app do cliente consulta o aparelho algumas vezes por minuto, e o simulador quer
// parecer com ele: três segundos ficam perto da cadência de report da seção 8.
const INTERVALO_MS = 3_000;
const HORA_DE_VITRINE = "9:41";

function numero(valor: unknown): number | null {
  return typeof valor === "number" && Number.isFinite(valor) ? valor : null;
}

function dpDe(mapa: readonly ItemDoMapa[], funcao: string): number | undefined {
  return mapa.find((item) => item.funcao === funcao && item.numero === 0)?.dpid;
}

function Fichas({
  rotulo,
  opcoes,
  atual,
  ocupado,
  aoEscolher,
}: {
  rotulo: string;
  opcoes: { valor: string; rotulo: string }[];
  atual: string | null;
  ocupado: boolean;
  aoEscolher: (valor: string) => void;
}) {
  if (opcoes.length === 0) return null;
  return (
    <div className="app-fichas" role="group" aria-label={rotulo}>
      {opcoes.map((opcao) => (
        <button
          key={opcao.valor}
          type="button"
          className="app-ficha"
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

function Energia({
  ligado,
  ocupado,
  aoAlternar,
}: {
  ligado: boolean;
  ocupado: boolean;
  aoAlternar: () => void;
}) {
  return (
    <button
      type="button"
      className={`app-play app-energia ${ligado ? "app-energia-ligada" : ""}`}
      disabled={ocupado}
      aria-label={ligado ? t("simulador_desligar") : t("simulador_ligar")}
      onClick={aoAlternar}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" aria-hidden="true">
        <path d="M12 3v9" />
        <path d="M6.3 6.5a8 8 0 1 0 11.4 0" />
      </svg>
    </button>
  );
}

function CartaoAv({
  numero: alvo,
  equipamento,
  item,
  snapshot,
  ocupado,
  aoAjustar,
}: {
  numero: Numero;
  equipamento: Equipamento | undefined;
  item: ItemCatalogo | undefined;
  snapshot: Snapshot;
  ocupado: boolean;
  aoAjustar: (dpid: number, valor: unknown) => void;
}) {
  const [arrastando, setArrastando] = useState<number | null>(null);
  const controles = controlesDoNumero(item, equipamento);
  const { dps, mapa } = snapshot;
  const online = bitDe(dps[String(dpDe(mapa, "online"))], alvo.numero);
  const mudo = bitDe(dps[String(dpDe(mapa, "mudos"))], alvo.numero);
  const ligado = dps[String(alvo.dps.ligado)] === true;
  const nivel = numero(dps[String(alvo.dps.nivel)]) ?? 0;
  const entrada = paresDe(dps[String(dpDe(mapa, "entradas"))])[alvo.numero] ?? "";
  const modo = paresDe(dps[String(dpDe(mapa, "modos"))])[alvo.numero] ?? "";
  const titulo = paresDe(dps[String(dpDe(mapa, "titulos"))])[alvo.numero] ?? "";
  const comando = dpDe(mapa, "comando");
  const mandar = (acao: string, valor?: string | number): void => {
    if (comando !== undefined) aoAjustar(comando, comandoDe(alvo.numero, acao, valor));
  };
  const mostrado = arrastando ?? nivel;
  const indexadas = (itens: { rotulo: string }[]): { valor: string; rotulo: string }[] =>
    itens.map((entradaDaLista, indice) => ({ valor: String(indice + 1), rotulo: entradaDaLista.rotulo }));
  return (
    <section className={`app-bloco ${online ? "" : "app-bloco-offline"}`}>
      <header>
        <h4>{nomeDoNumero(alvo)}</h4>
        <span className="app-estado">{online ? titulo : t("simulador_offline")}</span>
      </header>
      <div className="app-transporte">
        {controles.ligado && (
          <Energia ligado={ligado} ocupado={!online || ocupado} aoAlternar={() => aoAjustar(alvo.dps.ligado, !ligado)} />
        )}
        {controles.transporte && (
          <button
            type="button"
            className="app-play"
            disabled={!online || ocupado}
            aria-label={alvo.estado?.reproduzindo ? t("acao_pausar") : t("acao_tocar")}
            onClick={() => mandar(alvo.estado?.reproduzindo ? "pausar" : "tocar")}
          >
            {alvo.estado?.reproduzindo ? (
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
        {controles.nivel && (
          <label className="app-volume">
            <span className="app-volume-valor">{mostrado}</span>
            <input
              type="range"
              min={0}
              max={100}
              value={mostrado}
              disabled={!online || ocupado}
              aria-label={`${t("acao_volume")} ${nomeDoNumero(alvo)}`}
              onChange={(evento) => setArrastando(Number(evento.target.value))}
              onPointerUp={() => {
                if (arrastando !== null) aoAjustar(alvo.dps.nivel, arrastando);
                setArrastando(null);
              }}
              onKeyUp={() => {
                if (arrastando !== null) aoAjustar(alvo.dps.nivel, arrastando);
                setArrastando(null);
              }}
            />
          </label>
        )}
        {controles.mudo && (
          <button
            type="button"
            className="app-ficha"
            aria-pressed={mudo}
            disabled={!online || ocupado}
            onClick={() => mandar("mudo")}
          >
            {t("simulador_mudo")}
          </button>
        )}
      </div>
      <Fichas
        rotulo={t("simulador_entradas")}
        opcoes={indexadas(controles.entradas)}
        atual={entrada}
        ocupado={!online || ocupado}
        aoEscolher={(valor) => mandar("entrada", valor)}
      />
      <Fichas
        rotulo={t("simulador_atalhos")}
        opcoes={indexadas(controles.atalhos)}
        atual={null}
        ocupado={!online || ocupado}
        aoEscolher={(valor) => mandar("atalho", valor)}
      />
      <Fichas
        rotulo={t("simulador_modos")}
        opcoes={indexadas(controles.modos)}
        atual={modo}
        ocupado={!online || ocupado}
        aoEscolher={(valor) => mandar("modo", valor)}
      />
      <Fichas
        rotulo={t("simulador_teclas")}
        opcoes={controles.teclas.map((tecla) => ({ valor: tecla, rotulo: palavra("tecla", tecla) }))}
        atual={null}
        ocupado={!online || ocupado}
        aoEscolher={(valor) => mandar("tecla", valor)}
      />
    </section>
  );
}

function CartaoAr({
  numero: alvo,
  item,
  snapshot,
  ocupado,
  aoAjustar,
}: {
  numero: Numero;
  item: ItemCatalogo | undefined;
  snapshot: Snapshot;
  ocupado: boolean;
  aoAjustar: (dpid: number, valor: unknown) => void;
}) {
  const controles = controlesDoNumero(item, undefined);
  const { dps, mapa } = snapshot;
  const online = bitDe(dps[String(dpDe(mapa, "online"))], alvo.numero);
  const ligado = dps[String(alvo.dps.ligado)] === true;
  const graus = numero(dps[String(alvo.dps.temperatura)]);
  const modo = typeof dps[String(alvo.dps.modo)] === "string" ? String(dps[String(alvo.dps.modo)]) : null;
  const vento = typeof dps[String(alvo.dps.vento)] === "string" ? String(dps[String(alvo.dps.vento)]) : null;
  const doVocabulario = (prefixo: string, palavras: string[]): { valor: string; rotulo: string }[] =>
    palavras.map((valor) => ({ valor, rotulo: palavra(prefixo, valor) }));
  return (
    <section className={`app-bloco ${online ? "" : "app-bloco-offline"}`}>
      <header>
        <h4>{nomeDoNumero(alvo)}</h4>
        <span className="app-estado">
          {online ? (graus === null ? "" : `${graus} °C`) : t("simulador_offline")}
        </span>
      </header>
      <div className="app-transporte">
        {controles.ligado && (
          <Energia ligado={ligado} ocupado={!online || ocupado} aoAlternar={() => aoAjustar(alvo.dps.ligado, !ligado)} />
        )}
        {controles.temperatura && (
          <div className="app-graus" role="group" aria-label={t("simulador_temperatura")}>
            <button
              type="button"
              className="app-ficha"
              disabled={!online || ocupado || graus === null || graus <= TEMPERATURA_MINIMA}
              aria-label={`${t("simulador_temperatura")} -`}
              onClick={() => graus !== null && aoAjustar(alvo.dps.temperatura, graus - 1)}
            >
              -
            </button>
            <span className="app-volume-valor">{graus === null ? "--" : `${graus}°`}</span>
            <button
              type="button"
              className="app-ficha"
              disabled={!online || ocupado || graus === null || graus >= TEMPERATURA_MAXIMA}
              aria-label={`${t("simulador_temperatura")} +`}
              onClick={() => graus !== null && aoAjustar(alvo.dps.temperatura, graus + 1)}
            >
              +
            </button>
          </div>
        )}
      </div>
      <Fichas
        rotulo={t("simulador_modo")}
        opcoes={doVocabulario("modo_ar", controles.modosDeAr)}
        atual={modo}
        ocupado={!online || ocupado}
        aoEscolher={(valor) => aoAjustar(alvo.dps.modo, valor)}
      />
      <Fichas
        rotulo={t("simulador_vento")}
        opcoes={doVocabulario("vento", controles.ventos)}
        atual={vento}
        ocupado={!online || ocupado}
        aoEscolher={(valor) => aoAjustar(alvo.dps.vento, valor)}
      />
    </section>
  );
}

export default function Simulador({ nomeInstalacao }: { nomeInstalacao: string }) {
  const [leitura, setLeitura] = useState<Leitura>(VAZIA);
  const [escolhida, setEscolhida] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const pedidos = useRef(0);

  const recarregar = useCallback(async (): Promise<void> => {
    // Why: a poll for the licence shown before may land after the poll for the one shown now,
    // so only the latest request is allowed to write the screen.
    // Por que: uma consulta da licença mostrada antes pode chegar depois da consulta da
    // mostrada agora, então só o pedido mais recente pode escrever a tela.
    pedidos.current += 1;
    const pedido = pedidos.current;
    try {
      const [licencas, equipamentos, catalogo, cenas] = await Promise.all([
        lerLicencas(),
        lerEquipamentos(),
        lerCatalogo(),
        lerCenas(),
      ]);
      const atual = escolhida ?? licencas.licencas[0]?.id ?? null;
      const snapshot = atual === null ? null : await lerDps(atual);
      if (pedido !== pedidos.current) return;
      setLeitura({
        licencas: licencas.licencas,
        equipamentos,
        catalogo,
        cenas: cenas.cenas,
        snapshot,
        snapshotDe: atual,
        erro: null,
      });
      if (escolhida === null && atual !== null) setEscolhida(atual);
    } catch (falha) {
      if (pedido !== pedidos.current) return;
      setLeitura((anterior) => ({ ...anterior, erro: codigoDoErro(falha) }));
    }
  }, [escolhida]);

  useEffect(() => {
    void recarregar();
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);

  const licenca = leitura.licencas.find((candidata) => candidata.id === escolhida);

  async function ajustar(dpid: number, valor: unknown): Promise<void> {
    if (licenca === undefined) return;
    setOcupado(true);
    try {
      await ajustarDp(licenca.id, dpid, valor);
      setErro(null);
      await recarregar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setOcupado(false);
    }
  }

  const snapshot = licenca !== undefined && leitura.snapshotDe === licenca.id ? leitura.snapshot : null;
  const numeros = licenca === undefined ? [] : licenca.numeros.filter((numero) => numero.identidade !== "");
  const grupos = licenca === undefined ? [] : gruposPossiveis(licenca, leitura.catalogo);
  const dpGrupo = snapshot === null ? undefined : dpDe(snapshot.mapa, "grupo");
  const dpCena = snapshot === null ? undefined : dpDe(snapshot.mapa, FUNCAO_DA_CENA);
  const cenas = leitura.cenas.filter((cena) => cena.passos.length > 0);
  const itemDe = (numero: Numero): ItemCatalogo | undefined =>
    leitura.catalogo.find((candidato) => candidato.tipo === numero.tipo);
  const equipamentoDe = (numero: Numero): Equipamento | undefined =>
    leitura.equipamentos.find((candidato) => candidato.identidade === numero.identidade);
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
      {leitura.licencas.length > 1 && (
        <div className="fichas" role="group" aria-label={t("simulador_licenca")}>
          {leitura.licencas.map((candidata) => (
            <button
              key={candidata.id}
              type="button"
              className="ficha"
              aria-pressed={candidata.id === escolhida}
              onClick={() => setEscolhida(candidata.id)}
            >
              {candidata.nome || candidata.id}
            </button>
          ))}
        </div>
      )}
      <div className="telefone" role="region" aria-label={t("simulador_titulo")}>
        <div className="telefone-tela">
          <div className="app-status" aria-hidden="true">
            <span>{HORA_DE_VITRINE}</span>
            <span>●●●</span>
          </div>
          <header className="app-cabeca">
            <h3>{licenca?.nome || nomeInstalacao || t("produto")}</h3>
            {licenca !== undefined && (
              <p className="app-subtitulo">
                {t(`produto_${licenca.produto}` as const)}
                {" · "}
                {`${licenca.reports_do_dia} ${t("simulador_reports")}`}
              </p>
            )}
          </header>
          <div className="app-corpo">
            {leitura.erro === null && leitura.licencas.length === 0 && (
              <p className="app-vazio">{t("simulador_sem_licenca")}</p>
            )}
            {licenca !== undefined && (
              <>
                <h4 className="app-secao">{t("simulador_numeros")}</h4>
                {numeros.length === 0 && <p className="app-vazio">{t("simulador_vazio")}</p>}
                {snapshot !== null &&
                  numeros.map((numeroDaLicenca) =>
                    licenca.produto === "ar" ? (
                      <CartaoAr
                        key={numeroDaLicenca.numero}
                        numero={numeroDaLicenca}
                        item={itemDe(numeroDaLicenca)}
                        snapshot={snapshot}
                        ocupado={ocupado}
                        aoAjustar={(dpid, valor) => void ajustar(dpid, valor)}
                      />
                    ) : (
                      <CartaoAv
                        key={numeroDaLicenca.numero}
                        numero={numeroDaLicenca}
                        equipamento={equipamentoDe(numeroDaLicenca)}
                        item={itemDe(numeroDaLicenca)}
                        snapshot={snapshot}
                        ocupado={ocupado}
                        aoAjustar={(dpid, valor) => void ajustar(dpid, valor)}
                      />
                    ),
                  )}
                {grupos.length > 1 && dpGrupo !== undefined && (
                  <>
                    <h4 className="app-secao">{t("simulador_grupo")}</h4>
                    <div className="app-fichas" role="group" aria-label={t("simulador_grupo")}>
                      {grupos.map((valor) => (
                        <button
                          key={valor}
                          type="button"
                          className="app-ficha"
                          aria-pressed={valor === licenca.grupo}
                          disabled={ocupado}
                          onClick={() => void ajustar(dpGrupo, valor)}
                        >
                          {valor === SOLO
                            ? t("simulador_solo")
                            : nomeDoNumero(licenca.numeros[valor - 1] ?? licenca.numeros[0])}
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
                        onClick={() => dpCena !== undefined && void ajustar(dpCena, cena.numero)}
                      >
                        <span className="app-cena-numero">{cena.numero}</span>
                        <span>{cena.nome || t("cenas_sem_nome")}</span>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
