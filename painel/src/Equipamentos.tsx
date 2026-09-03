// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { Fragment, useCallback, useEffect, useState } from "react";
import CadastroEquipamento from "./CadastroEquipamento.tsx";
import Controles from "./ControlesEquipamento.tsx";
import EditarEquipamento from "./EditarEquipamento.tsx";
import {
  autenticarEquipamento,
  codigoDoErro,
  executarAcao,
  lerCatalogo,
  lerEquipamentos,
  removerEquipamento,
} from "./api.ts";
import { LEITURA_INICIAL, aplicarCiclo, type Leitura, type Tentativa } from "./ciclo.ts";
import {
  INTERVALO_MS,
  camposVisiveis,
  linhasDoEstado,
  rotuloDoTipo,
  textoDoManifesto,
  type Equipamento,
  type ItemCatalogo,
  type Preparo,
  type ResultadoAutenticacao,
} from "./equipamentos.ts";
import { t, traduzirErro, type Idioma } from "./i18n";

function Linhas({
  equipamento,
  item,
  idioma,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  idioma: Idioma;
}) {
  const nomeDoCampo = (nome: string): string =>
    textoDoManifesto(item, idioma, `campo_${nome}`) || nome;
  return (
    <dl>
      <dt>{t("equipamentos_endereco")}</dt>
      <dd>{equipamento.ip}</dd>
      <dt>{t("equipamentos_identidade")}</dt>
      <dd>{equipamento.identidade}</dd>
      {camposVisiveis(item, equipamento.campos).map(({ nome, valor }) => (
        <Fragment key={`campo-${nome}`}>
          <dt>{nomeDoCampo(nome)}</dt>
          <dd>{valor}</dd>
        </Fragment>
      ))}
      {equipamento.segredos_definidos.map((nome) => (
        <Fragment key={`segredo-${nome}`}>
          <dt>{nomeDoCampo(nome)}</dt>
          <dd className="texto-suave">{t("segredo_definido")}</dd>
        </Fragment>
      ))}
      {linhasDoEstado(equipamento.estado).map((linha) => (
        <Fragment key={`estado-${linha.campo}`}>
          <dt>{t(`estado_${linha.campo}` as const)}</dt>
          <dd>
            {linha.especie === "logico" && t(linha.logico ? "sim" : "nao")}
            {linha.especie === "numero" && String(linha.numero)}
            {linha.especie === "texto" && linha.texto}
            {/* Why: detalhe is a code of a fixed vocabulary, so the panel translates it */}
            {/* like any other code and never prints a phrase the daemon invented. */}
            {/* Por que: detalhe é um código de vocabulário fixo, então o painel o traduz */}
            {/* como qualquer código e nunca imprime uma frase que o daemon inventou. */}
            {linha.especie === "codigo" && t(`detalhe_${linha.codigo}` as const)}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

function CartaoEquipamento({
  equipamento,
  item,
  idioma,
  aoMudar,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  idioma: Idioma;
  aoMudar: () => void;
}) {
  const [erro, setErro] = useState<string | null>(null);
  const [confirmando, setConfirmando] = useState(false);
  const [editando, setEditando] = useState(false);
  const [par, setPar] = useState<ResultadoAutenticacao | null>(null);
  const [emCurso, setEmCurso] = useState<string | null>(null);
  const ocupado = emCurso !== null;

  async function chamar(marca: string, trabalho: () => Promise<void>): Promise<void> {
    setEmCurso(marca);
    try {
      await trabalho();
      setErro(null);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setEmCurso(null);
    }
  }

  function executar(acao: string, preparo: Preparo): void {
    // Why: a value the action cannot take is refused here with the same stable code the
    // daemon would answer, so a typo costs no request.
    // Por que: um valor que a ação não aceita é recusado aqui com o mesmo código estável
    // que o daemon responderia, então um erro de digitação não custa requisição.
    if (!preparo.ok) {
      setErro(preparo.codigo);
      return;
    }
    void chamar(acao, async () => {
      await executarAcao(equipamento.identidade, acao, preparo.valor);
      aoMudar();
    });
  }

  const ajuda = textoDoManifesto(item, idioma, "auth_ajuda");
  return (
    <li className={`equipamento ${equipamento.estado.online ? "cartao-online" : "cartao-offline"}`}>
      <div className="equipamento-cabeca">
        <div>
          <h3>{equipamento.nome || equipamento.identidade}</h3>
          <p className="texto-suave">{rotuloDoTipo(item, idioma, equipamento.tipo)}</p>
        </div>
        <p className="estado-curto">
          <span className="ponto" aria-hidden="true" />
          {equipamento.estado.online ? t("equipamentos_online") : t("equipamentos_offline")}
        </p>
      </div>
      <Linhas equipamento={equipamento} item={item} idioma={idioma} />
      <Controles
        capacidades={item?.capacidades ?? []}
        estado={equipamento.estado}
        ocupado={ocupado}
        aoExecutar={executar}
      />
      {item !== undefined && item.auth !== "nenhuma" && (
        <div className="pareamento">
          <button
            type="button"
            className="botao secundario"
            disabled={ocupado}
            onClick={() =>
              void chamar("parear", async () =>
                setPar(await autenticarEquipamento(equipamento.identidade)),
              )
            }
          >
            {emCurso === "parear" ? t("pareando") : t("parear")}
          </button>
          {ajuda && <p className="dica">{ajuda}</p>}
          {par !== null && (
            <p className={par === "falhou" ? "erro" : "sucesso"} role="status">
              {t(`par_${par}` as const)}
            </p>
          )}
        </div>
      )}
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      {editando && item !== undefined && (
        <EditarEquipamento
          equipamento={equipamento}
          item={item}
          idioma={idioma}
          aoSalvar={() => {
            setEditando(false);
            aoMudar();
          }}
          aoCancelar={() => setEditando(false)}
        />
      )}
      {confirmando ? (
        // Why: section 9 keeps the panel in charge of its own answers, and a browser
        // dialog is also the one thing a kiosk tablet may refuse to show.
        // Por que: a seção 9 mantém o painel dono das próprias respostas, e um diálogo do
        // navegador é também a única coisa que um tablet de quiosque pode recusar mostrar.
        <div className="confirmacao">
          <p>{t("remover_pergunta")}</p>
          <button
            type="button"
            className="botao secundario"
            disabled={ocupado}
            onClick={() => {
              setConfirmando(false);
              void chamar("remover", async () => {
                await removerEquipamento(equipamento.identidade);
                aoMudar();
              });
            }}
          >
            {t("remover_confirmar")}
          </button>
          <button type="button" className="botao secundario" onClick={() => setConfirmando(false)}>
            {t("remover_cancelar")}
          </button>
        </div>
      ) : (
        <div className="acoes">
          {item !== undefined && (
            <button
              type="button"
              className="botao secundario"
              onClick={() => setEditando((atual) => !atual)}
            >
              {editando ? t("editar_cancelar") : t("editar")}
            </button>
          )}
          <button type="button" className="botao secundario" onClick={() => setConfirmando(true)}>
            {t("remover")}
          </button>
        </div>
      )}
    </li>
  );
}

async function tentar<T>(trabalho: () => Promise<T>): Promise<Tentativa<T>> {
  try {
    return { ok: true, valor: await trabalho() };
  } catch (falha) {
    return { ok: false, codigo: codigoDoErro(falha) };
  }
}

export default function Equipamentos({ idioma }: { idioma: Idioma }) {
  const [leitura, setLeitura] = useState<Leitura>(LEITURA_INICIAL);

  // Why: the catalog is read in the same cycle as the list, so a request that failed once
  // is tried again on the next tick instead of leaving the panel unable to register
  // anything until someone reloads the page.
  // Por que: o catálogo é lido no mesmo ciclo da lista, então uma requisição que falhou
  // uma vez é refeita no próximo ciclo em vez de deixar o painel sem conseguir cadastrar
  // nada até alguém recarregar a página.
  const recarregar = useCallback(async (): Promise<void> => {
    const [catalogo, lista] = await Promise.all([tentar(lerCatalogo), tentar(lerEquipamentos)]);
    setLeitura((anterior) => aplicarCiclo(anterior, catalogo, lista));
  }, []);

  useEffect(() => {
    void recarregar();
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);

  const { catalogo, lista, erro } = leitura;
  return (
    <>
      <section className="cartao">
        <h2>{t("equipamentos_titulo")}</h2>
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
        {lista === null && erro === null && <p className="carregando">{t("carregando")}</p>}
        {/* Why: section 6, zero equipment is a normal state of the hub and not a failure. */}
        {/* Por que: seção 6, zero equipamento é estado normal do hub e não uma falha. */}
        {lista !== null && lista.length === 0 && (
          <p className="texto-suave">{t("equipamentos_vazio")}</p>
        )}
        {lista !== null && lista.length > 0 && (
          <ul className="equipamentos">
            {lista.map((equipamento) => (
              <CartaoEquipamento
                key={equipamento.identidade}
                equipamento={equipamento}
                item={(catalogo ?? []).find((candidato) => candidato.tipo === equipamento.tipo)}
                idioma={idioma}
                aoMudar={() => void recarregar()}
              />
            ))}
          </ul>
        )}
      </section>
      <CadastroEquipamento
        catalogo={catalogo}
        idioma={idioma}
        aoCadastrar={() => void recarregar()}
      />
    </>
  );
}
