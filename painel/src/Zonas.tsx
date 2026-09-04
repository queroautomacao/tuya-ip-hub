// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: section 6, everything this screen shows about a speaker comes from the manifest and
// from the answer of the daemon: the label of the tipo, the buttons a block gets and the data
// point each button writes. The screen decides nothing, so a driver that does not declare a
// capability simply has no button for it.
// Por que: seção 6, tudo que esta tela mostra de uma caixa vem do manifesto e da resposta do
// daemon: o rótulo do tipo, os botões que um bloco ganha e o data point que cada botão
// escreve. A tela não decide nada, então um driver que não declara uma capacidade simplesmente
// não ganha botão para ela.

import { Fragment, useCallback, useEffect, useState } from "react";
import {
  ajustarDp,
  codigoDoErro,
  definirGrupo,
  lerCatalogo,
  lerDps,
  lerEquipamentos,
  lerZonas,
  salvarZonas,
} from "./api.ts";
import { itemDoDp, type ItemDoMapa } from "./cenas.ts";
import {
  INTERVALO_MS,
  rotuloDoTipo,
  type Equipamento,
  type ItemCatalogo,
} from "./equipamentos.ts";
import { t, traduzirErro, type Idioma } from "./i18n";
import {
  SOLO,
  comIdentidade,
  controlesDaZona,
  gruposPossiveis,
  ordemDe,
  podeOcuparBloco,
  prepararVolume,
  tocando,
  type ControleDeZona,
  type LeituraDeZonas,
  type Zona,
} from "./zonas.ts";

interface Leitura {
  leitura: LeituraDeZonas | null;
  catalogo: ItemCatalogo[] | null;
  equipamentos: Equipamento[] | null;
  mapa: ItemDoMapa[];
  erro: string | null;
}

const VAZIA: Leitura = {
  leitura: null,
  catalogo: null,
  equipamentos: null,
  mapa: [],
  erro: null,
};

function itemDe(catalogo: ItemCatalogo[] | null, tipo: string): ItemCatalogo | undefined {
  return (catalogo ?? []).find((candidato) => candidato.tipo === tipo);
}

function Ocupante({
  zona,
  candidatos,
  catalogo,
  idioma,
  ocupado,
  aoEscolher,
}: {
  zona: Zona;
  candidatos: Equipamento[];
  catalogo: ItemCatalogo[] | null;
  idioma: Idioma;
  ocupado: boolean;
  aoEscolher: (identidade: string) => void;
}) {
  return (
    <label className="zona-ocupante">
      <span className="texto-suave">{t("zonas_ocupante")}</span>
      <select
        value={zona.identidade}
        disabled={ocupado}
        onChange={(evento) => aoEscolher(evento.target.value)}
      >
        <option value="">{t("zonas_vazia")}</option>
        {candidatos.map((equipamento) => (
          <option key={equipamento.identidade} value={equipamento.identidade}>
            {equipamento.nome || equipamento.identidade} (
            {rotuloDoTipo(itemDe(catalogo, equipamento.tipo), idioma, equipamento.tipo)})
          </option>
        ))}
      </select>
    </label>
  );
}

function Controle({
  controle,
  zona,
  valores,
  ocupado,
  aoAjustar,
}: {
  controle: ControleDeZona;
  zona: Zona;
  valores: string[];
  ocupado: boolean;
  aoAjustar: (dpid: number, valor: unknown, codigo?: string) => void;
}) {
  const [texto, setTexto] = useState("");
  const rotulo = t(`zonas_funcao_${controle.funcao}` as const);
  if (controle.especie === "escala") {
    return (
      <div className="zona-controle">
        <input
          className="valor"
          type="number"
          min={0}
          max={100}
          inputMode="numeric"
          aria-label={rotulo}
          value={texto}
          onChange={(evento) => setTexto(evento.target.value)}
        />
        <button
          type="button"
          className="botao secundario"
          disabled={ocupado}
          onClick={() => {
            const preparo = prepararVolume(texto);
            aoAjustar(controle.dpid, preparo.ok ? preparo.valor : null, preparo.ok ? undefined : preparo.codigo);
          }}
        >
          {rotulo}
        </button>
      </div>
    );
  }
  if (controle.especie === "alternar") {
    const tocandoAgora = tocando(zona);
    return (
      <div className="zona-controle">
        <button
          type="button"
          className="botao secundario"
          disabled={ocupado}
          onClick={() => aoAjustar(controle.dpid, !tocandoAgora)}
        >
          {tocandoAgora ? t("zonas_pausar") : t("zonas_tocar")}
        </button>
      </div>
    );
  }
  if (controle.especie === "escolha") {
    return (
      <div className="zona-controle">
        <select
          className="valor"
          aria-label={rotulo}
          value={texto}
          disabled={ocupado}
          onChange={(evento) => {
            setTexto(evento.target.value);
            if (evento.target.value) aoAjustar(controle.dpid, evento.target.value);
          }}
        >
          <option value="">{rotulo}</option>
          {zona.entradas.map((entrada) => (
            <option key={entrada} value={entrada}>
              {entrada}
            </option>
          ))}
        </select>
      </div>
    );
  }
  // Why: the values of the preset enum are section 8 and the daemon publishes them, so the
  // screen offers exactly what the bus takes instead of a list written here a second time.
  // Por que: os valores do enum de preset são a seção 8 e o daemon os publica, então a tela
  // oferece exatamente o que o barramento aceita em vez de uma lista escrita aqui de novo.
  return (
    <div className="zona-controle">
      <select
        className="valor"
        aria-label={rotulo}
        value={texto}
        disabled={ocupado}
        onChange={(evento) => {
          setTexto(evento.target.value);
          if (evento.target.value) aoAjustar(controle.dpid, evento.target.value);
        }}
      >
        <option value="">{rotulo}</option>
        {valores.map((preset) => (
          <option key={preset} value={preset}>
            {preset}
          </option>
        ))}
      </select>
    </div>
  );
}

function Bloco({
  zona,
  item,
  candidatos,
  catalogo,
  idioma,
  ocupado,
  valoresDe,
  aoEscolher,
  aoAjustar,
}: {
  zona: Zona;
  item: ItemCatalogo | undefined;
  candidatos: Equipamento[];
  catalogo: ItemCatalogo[] | null;
  idioma: Idioma;
  ocupado: boolean;
  valoresDe: (dpid: number) => string[];
  aoEscolher: (identidade: string) => void;
  aoAjustar: (dpid: number, valor: unknown, codigo?: string) => void;
}) {
  const controles = controlesDaZona(zona, item);
  const estado = zona.estado;
  return (
    <li className={`zona ${estado?.online ? "cartao-online" : "cartao-offline"}`}>
      <div className="zona-cabeca">
        <h3>
          {t("zonas_bloco")} {zona.zona}
        </h3>
        {zona.papel !== "" && (
          <span className="etiqueta">
            {t(zona.papel === "mestre" ? "zonas_papel_mestre" : "zonas_papel_escravo")}
          </span>
        )}
      </div>
      <Ocupante
        zona={zona}
        candidatos={candidatos}
        catalogo={catalogo}
        idioma={idioma}
        ocupado={ocupado}
        aoEscolher={aoEscolher}
      />
      {zona.identidade === "" ? (
        <p className="texto-suave">{t("zonas_bloco_livre")}</p>
      ) : (
        <>
          <p className="texto-suave">
            {zona.nome || zona.identidade} ({rotuloDoTipo(item, idioma, zona.tipo)})
          </p>
          <dl>
            <dt>{t("zonas_estado")}</dt>
            <dd>{estado?.online ? t("equipamentos_online") : t("equipamentos_offline")}</dd>
            {estado !== null && estado.volume !== null && (
              <Fragment key="volume">
                <dt>{t("estado_volume")}</dt>
                <dd>{String(estado.volume)}</dd>
              </Fragment>
            )}
            {estado !== null && estado.fonte !== null && (
              <Fragment key="fonte">
                <dt>{t("estado_fonte")}</dt>
                <dd>{estado.fonte}</dd>
              </Fragment>
            )}
            {estado !== null && estado.tocando !== null && (
              <Fragment key="tocando">
                <dt>{t("estado_tocando")}</dt>
                <dd>{estado.tocando}</dd>
              </Fragment>
            )}
          </dl>
          {controles.length > 0 && (
            <div className="zona-controles">
              {controles.map((controle) => (
                <Controle
                  key={controle.funcao}
                  controle={controle}
                  zona={zona}
                  valores={valoresDe(controle.dpid)}
                  ocupado={ocupado}
                  aoAjustar={aoAjustar}
                />
              ))}
            </div>
          )}
        </>
      )}
    </li>
  );
}

export default function Zonas({ idioma }: { idioma: Idioma }) {
  const [leitura, setLeitura] = useState<Leitura>(VAZIA);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      const [zonas, catalogo, equipamentos, snapshot] = await Promise.all([
        lerZonas(),
        lerCatalogo(),
        lerEquipamentos(),
        lerDps(),
      ]);
      setLeitura({ leitura: zonas, catalogo, equipamentos, mapa: snapshot.mapa, erro: null });
    } catch (falha) {
      setLeitura((anterior) => ({ ...anterior, erro: codigoDoErro(falha) }));
    }
  }, []);

  useEffect(() => {
    void recarregar();
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);

  async function chamar(trabalho: () => Promise<void>): Promise<void> {
    setOcupado(true);
    try {
      await trabalho();
      setErro(null);
      await recarregar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setOcupado(false);
    }
  }

  const zonas = leitura.leitura?.zonas ?? [];
  const catalogo = leitura.catalogo;
  const candidatos = (leitura.equipamentos ?? []).filter((equipamento) =>
    podeOcuparBloco(itemDe(catalogo, equipamento.tipo)),
  );
  const grupos = gruposPossiveis(zonas, catalogo ?? []);
  const valoresDe = (dpid: number): string[] => itemDoDp(leitura.mapa, dpid)?.valores ?? [];
  return (
    <section className="cartao">
      <h2>{t("zonas_titulo")}</h2>
      <p className="texto-suave">{t("zonas_intro")}</p>
      {leitura.erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(leitura.erro)}
        </p>
      )}
      {leitura.leitura === null && leitura.erro === null && (
        <p className="carregando">{t("carregando")}</p>
      )}
      {candidatos.length === 0 && leitura.equipamentos !== null && (
        <p className="texto-suave">{t("zonas_exclusivo")}</p>
      )}
      {leitura.leitura !== null && (
        <>
          <div className="zona-grupo">
            <label>
              <span className="texto-suave">{t("zonas_grupo")}</span>
              <select
                value={leitura.leitura.grupo}
                disabled={ocupado}
                onChange={(evento) => void chamar(() => definirGrupo(evento.target.value))}
              >
                {/* Why: section 14, a group only exists between speakers of the same domain, */}
                {/* so a mixed one is never even offered on the screen. */}
                {/* Por que: seção 14, um grupo só existe entre caixas do mesmo domínio, */}
                {/* então um misto nunca é sequer oferecido na tela. */}
                {grupos.map((valor) => (
                  <option key={valor} value={valor}>
                    {valor === SOLO ? t("zonas_solo") : `${t("zonas_bloco")} ${valor.slice(5)}`}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <ul className="zonas">
            {zonas.map((zona) => (
              <Bloco
                key={zona.zona}
                zona={zona}
                item={itemDe(catalogo, zona.tipo)}
                candidatos={candidatos}
                catalogo={catalogo}
                idioma={idioma}
                ocupado={ocupado}
                valoresDe={valoresDe}
                aoEscolher={(identidade) =>
                  void chamar(() =>
                    salvarZonas(comIdentidade(ordemDe(zonas), zona.zona, identidade)),
                  )
                }
                aoAjustar={(dpid, valor, codigo) => {
                  if (codigo !== undefined) {
                    setErro(codigo);
                    return;
                  }
                  void chamar(() => ajustarDp(dpid, valor));
                }}
              />
            ))}
          </ul>
        </>
      )}
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
    </section>
  );
}
