// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the state of the daemon is read by the rail of the shell and by the account screen,
// so the reading cycle is written once here and the two draw what they need from it.
// Por que: o estado do daemon é lido pelo trilho da casca e pela tela de conta, então o
// ciclo de leitura é escrito uma vez aqui e as duas desenham o que precisam dele.

import { useEffect, useState } from "react";
import { INTERVALO_MS, lerSaude, type Saude } from "./saude";

export type Fase = "verificando" | "online" | "offline";

export interface LeituraDeSaude {
  fase: Fase;
  saude: Saude | null;
  em: Date | null;
}

export function usarSaude(): LeituraDeSaude {
  const [leitura, setLeitura] = useState<LeituraDeSaude>({ fase: "verificando", saude: null, em: null });

  useEffect(() => {
    let ativo = true;

    async function verificar(): Promise<void> {
      let proxima: LeituraDeSaude;
      try {
        const saude = await lerSaude();
        proxima = { fase: saude.ok ? "online" : "offline", saude, em: new Date() };
      } catch {
        proxima = { fase: "offline", saude: null, em: new Date() };
      }
      if (ativo) setLeitura(proxima);
    }

    void verificar();
    const temporizador = window.setInterval(() => void verificar(), INTERVALO_MS);
    return () => {
      ativo = false;
      window.clearInterval(temporizador);
    };
  }, []);

  return leitura;
}
