// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useCallback, useEffect, useState } from "react";
import {
  codigoDoErro,
  lerDriversDeclarativos,
  lerModeloDriver,
  problemasDoErro,
  removerDriver,
  salvarDriver,
  validarDriver,
} from "./api.ts";
import {
  TRANSPORTES,
  agruparProblemas,
  analisar,
  avisoDeSalvar,
  campoLegivel,
  ehCampoDoArquivo,
  ofertaDeApagar,
  textoDoModelo,
  type Arquivo,
  type DriverDeclarativo,
  type Grupo,
  type Transporte,
} from "./declarativos.ts";
import { rotuloDoTipo, type ItemCatalogo } from "./equipamentos.ts";
import { t, traduzirErro, type Idioma } from "./i18n";

function Problemas({ grupos }: { grupos: readonly Grupo[] }) {
  return (
    <div className="problemas" role="alert">
      <p>{t("editor_problemas")}</p>
      <ul>
        {grupos.map((grupo) => (
          <li key={grupo.campo}>
            {/* Why: the campo is a path the daemon echoed from the file the integrator */}
            {/* wrote, so it is printed as the identifier it is and within a ceiling. */}
            {/* Por que: o campo é um caminho que o daemon ecoou do arquivo que o */}
            {/* integrador escreveu, então sai como o identificador que é e dentro de teto. */}
            <code>
              {ehCampoDoArquivo(grupo.campo) ? t("editor_arquivo") : campoLegivel(grupo.campo)}
            </code>{" "}
            {grupo.codigos.map((codigo) => traduzirErro(codigo)).join(" ")}
          </li>
        ))}
      </ul>
    </div>
  );
}

function LinhaDriver({
  driver,
  idioma,
  aoMudar,
}: {
  driver: DriverDeclarativo;
  idioma: Idioma;
  aoMudar: () => void;
}) {
  const [erro, setErro] = useState<string | null>(null);
  const [confirmando, setConfirmando] = useState(false);
  const [apagando, setApagando] = useState(false);
  const oferta = ofertaDeApagar(driver);
  const manifesto: ItemCatalogo = driver.manifesto;

  async function apagar(): Promise<void> {
    setApagando(true);
    try {
      await removerDriver(driver.tipo);
      setErro(null);
      aoMudar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
    } finally {
      setApagando(false);
    }
  }

  return (
    <li>
      <div className="driver-cabeca">
        <div>
          <h3>{rotuloDoTipo(manifesto, idioma, driver.tipo)}</h3>
          <p className="texto-suave">{driver.tipo}</p>
        </div>
        <p className="etiqueta">{t(`drivers_origem_${driver.origem}` as const)}</p>
      </div>
      <dl>
        <dt>{t("drivers_categoria")}</dt>
        <dd>{manifesto.categoria}</dd>
        <dt>{t("drivers_capacidades")}</dt>
        <dd>{manifesto.capacidades.join(", ") || t("drivers_sem_capacidade")}</dd>
      </dl>
      {oferta === "da_imagem" && <p className="dica">{t("drivers_apagar_imagem")}</p>}
      {oferta === "em_uso" && <p className="dica">{t("drivers_apagar_em_uso")}</p>}
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
      {oferta === "pode" && !confirmando && (
        <button type="button" className="botao secundario" onClick={() => setConfirmando(true)}>
          {t("drivers_apagar")}
        </button>
      )}
      {oferta === "pode" && confirmando && (
        <div className="confirmacao">
          <p>{t("drivers_apagar_pergunta")}</p>
          <button
            type="button"
            className="botao secundario"
            disabled={apagando}
            onClick={() => {
              setConfirmando(false);
              void apagar();
            }}
          >
            {t("drivers_apagar_confirmar")}
          </button>
          <button type="button" className="botao secundario" onClick={() => setConfirmando(false)}>
            {t("drivers_apagar_cancelar")}
          </button>
        </div>
      )}
    </li>
  );
}

function Editor({
  drivers,
  aoSalvar,
}: {
  drivers: readonly DriverDeclarativo[];
  aoSalvar: () => void;
}) {
  const [texto, setTexto] = useState("");
  const [grupos, setGrupos] = useState<Grupo[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [aceito, setAceito] = useState<Arquivo | null>(null);
  const [salvo, setSalvo] = useState(false);
  const [emCurso, setEmCurso] = useState<string | null>(null);
  const ocupado = emCurso !== null;

  // Why: Save only offers itself for a text the daemon accepted, so any edit takes the
  // validation back instead of letting a changed file ride an old answer.
  // Por que: Salvar só se oferece para um texto que o daemon aceitou, então qualquer edição
  // desfaz a validação em vez de deixar um arquivo alterado pegar carona numa resposta velha.
  function escrever(novo: string): void {
    setTexto(novo);
    setAceito(null);
    setSalvo(false);
  }

  async function chamar(marca: string, trabalho: () => Promise<void>): Promise<void> {
    setEmCurso(marca);
    try {
      await trabalho();
    } catch (falha) {
      const problemas = problemasDoErro(falha);
      setGrupos(agruparProblemas(problemas));
      // Why: a refusal that names its fields says everything on those lines; any other
      // failure (no session, no answer) is about the request and gets the general line.
      // Por que: uma recusa que nomeia os campos diz tudo naquelas linhas; qualquer outra
      // falha (sem sessão, sem resposta) é sobre a requisição e ganha a linha geral.
      setErro(problemas.length > 0 ? null : codigoDoErro(falha));
    } finally {
      setEmCurso(null);
    }
  }

  function carregar(transporte: Transporte): void {
    void chamar(transporte, async () => {
      const modelo = await lerModeloDriver(transporte);
      escrever(textoDoModelo(modelo));
      setGrupos([]);
      setErro(null);
    });
  }

  function validar(): void {
    setSalvo(false);
    const analise = analisar(texto);
    if (!analise.ok) {
      setAceito(null);
      setErro(null);
      setGrupos(agruparProblemas(analise.problemas));
      return;
    }
    void chamar("validar", async () => {
      await validarDriver(analise.arquivo.dados);
      setAceito(analise.arquivo);
      setGrupos([]);
      setErro(null);
    });
  }

  function salvar(arquivo: Arquivo): void {
    void chamar("salvar", async () => {
      await salvarDriver(arquivo.dados);
      escrever("");
      setGrupos([]);
      setErro(null);
      setSalvo(true);
      aoSalvar();
    });
  }

  const aviso = aceito === null ? null : avisoDeSalvar(aceito.tipo, drivers);
  return (
    <section className="cartao">
      <h2>{t("editor_titulo")}</h2>
      <p className="texto-suave">{t("editor_intro")}</p>
      <div className="modelos">
        {TRANSPORTES.map((transporte) => (
          <button
            key={transporte}
            type="button"
            className="botao secundario"
            disabled={ocupado}
            onClick={() => carregar(transporte)}
          >
            {t(`editor_modelo_${transporte}` as const)}
          </button>
        ))}
      </div>
      <div className="formulario">
        <label htmlFor="driver-json">{t("editor_json")}</label>
        <textarea
          id="driver-json"
          className="editor-json"
          name="json"
          rows={16}
          spellCheck={false}
          value={texto}
          onChange={(evento) => escrever(evento.target.value)}
        />
        {grupos.length > 0 && <Problemas grupos={grupos} />}
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
        {aceito !== null && (
          <p className="sucesso" role="status">
            {t("editor_validado")}
          </p>
        )}
        {salvo && (
          <p className="sucesso" role="status">
            {t("editor_salvo")}
          </p>
        )}
        {aviso !== null && <p className="dica">{t(`editor_${aviso}` as const)}</p>}
        <div className="modelos">
          <button
            type="button"
            className="botao"
            disabled={ocupado || !texto.trim()}
            onClick={validar}
          >
            {emCurso === "validar" ? t("editor_validando") : t("editor_validar")}
          </button>
          {/* Why: the panel never writes a file the daemon has not accepted, so this button */}
          {/* only exists after a validation of this very text. */}
          {/* Por que: o painel nunca grava um arquivo que o daemon não aceitou, então este */}
          {/* botão só existe depois de uma validação deste mesmo texto. */}
          {aceito !== null && (
            <button
              type="button"
              className="botao"
              disabled={ocupado}
              onClick={() => salvar(aceito)}
            >
              {emCurso === "salvar" ? t("enviando") : t("editor_salvar")}
            </button>
          )}
        </div>
        <p className="dica">{t("editor_precedencia")}</p>
      </div>
    </section>
  );
}

export default function DriversDeclarativos({ idioma }: { idioma: Idioma }) {
  const [lista, setLista] = useState<DriverDeclarativo[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      setLista(await lerDriversDeclarativos());
      setErro(null);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    }
  }, []);

  useEffect(() => {
    void recarregar();
  }, [recarregar]);

  return (
    <>
      <section className="cartao">
        <h2>{t("drivers_titulo")}</h2>
        <p className="texto-suave">{t("drivers_intro")}</p>
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
        {lista === null && erro === null && <p className="carregando">{t("carregando")}</p>}
        {lista !== null && lista.length === 0 && (
          <p className="texto-suave">{t("drivers_vazio")}</p>
        )}
        {lista !== null && lista.length > 0 && (
          <ul className="drivers">
            {lista.map((driver) => (
              <LinhaDriver
                key={driver.tipo}
                driver={driver}
                idioma={idioma}
                aoMudar={() => void recarregar()}
              />
            ))}
          </ul>
        )}
      </section>
      <Editor drivers={lista ?? []} aoSalvar={() => void recarregar()} />
    </>
  );
}
