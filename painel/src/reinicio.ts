// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// After a restart the daemon answers /health about seven seconds later on the reference
// board; the panel waits for it to disappear and then to answer again before reloading.

import { lerSaude } from "./saude.ts";

const ESPERA_MS = 2_000;
const TENTATIVAS = 30;

export async function esperarVoltarERecarregar(): Promise<void> {
  let sumiu = false;
  for (let tentativa = 0; tentativa < TENTATIVAS; tentativa += 1) {
    await new Promise((resolver) => window.setTimeout(resolver, ESPERA_MS));
    try {
      await lerSaude();
      if (sumiu) {
        window.location.reload();
        return;
      }
    } catch {
      sumiu = true;
    }
  }
  window.location.reload();
}
