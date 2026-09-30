/**
 * De onde a varredura tira a lista de escritórios.
 *
 * O defeito que motivou este arquivo: `npm run escalonar` saía com
 * "nenhum tenant configurado, nada a varrer" e código 0 no ambiente real
 * — silenciosamente, para sempre. A lista vinha só de
 * `ELO_WHATSAPP_NUMBERS`, e o `.env` real não tem número de WhatsApp,
 * porque o número do escritório segue no WhatsApp Business App (M13).
 * Uma Tarefa Agendada de minuto em minuto teria rodado 1.440 vezes por
 * dia sem varrer nada, e o silêncio era proposital o suficiente para
 * ninguém notar.
 *
 * Estes testes importam o PRÓPRIO script — não uma cópia da regra. É por
 * isso que ele tem a guarda de `import.meta.url` no fim: sem ela, este
 * import dispararia uma varredura de banco no meio da suíte.
 */
import { generateKeyPairSync, randomUUID } from "node:crypto";

import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { tenantsParaVarrer } from "../scripts/escalonamento";
import { escalonarPendentes } from "@/server/conversations/escalonamento";
import { limparCacheDeNumeros } from "@/server/channels/whatsapp/config";
import { resetIntegrationKeyCache } from "@/server/integrations/signature";

import { fecharPrisma } from "./helpers";

/**
 * O tenant do Escritorio Modelo, o escritório real desta instalação.
 *
 * Está aqui de propósito, e não é segredo — é um identificador opaco, do
 * mesmo escritório que assina as chamadas do FISCALE. O último caso deste
 * arquivo existe para que uma mudança de configuração que volte a
 * esconder este tenant apareça como teste vermelho, e não como
 * atendimento esquecido.
 */
const TENANT_REAL = "14f56987-676c-471d-a88d-da842d59d897";

/** Uma chave Ed25519 de verdade: `integrationKeys()` recusa o que não for. */
function chavePublicaEd25519(): string {
  const { publicKey } = generateKeyPairSync("ed25519");
  return publicKey.export({ format: "der", type: "spki" }).toString("base64");
}

function registroDeChaves(tenants: string[]): string {
  const bruto: Record<string, { tenantId: string; publicKey: string }> = {};
  tenants.forEach((tenantId, i) => {
    bruto[`int-teste-${i}`] = { tenantId, publicKey: chavePublicaEd25519() };
  });
  return JSON.stringify(bruto);
}

function registroDeNumeros(tenants: string[]): string {
  const bruto: Record<string, { tenantId: string }> = {};
  tenants.forEach((tenantId, i) => {
    bruto[`55819900000${i}`] = { tenantId };
  });
  return JSON.stringify(bruto);
}

/**
 * O `.env` real é carregado pelo `tests/setup.ts`, que só sobrescreve as
 * URLs do banco. Então `ELO_INTEGRATION_PUBLIC_KEYS` chega aqui com o
 * valor de produção — e precisa voltar intacto depois de cada caso, ou o
 * último teste mediria a configuração inventada pelo penúltimo.
 */
const INTEGRACAO_ORIGINAL = process.env.ELO_INTEGRATION_PUBLIC_KEYS;
const NUMEROS_ORIGINAL = process.env.ELO_WHATSAPP_NUMBERS;

function restaurarAmbiente(): void {
  if (INTEGRACAO_ORIGINAL === undefined) delete process.env.ELO_INTEGRATION_PUBLIC_KEYS;
  else process.env.ELO_INTEGRATION_PUBLIC_KEYS = INTEGRACAO_ORIGINAL;

  if (NUMEROS_ORIGINAL === undefined) delete process.env.ELO_WHATSAPP_NUMBERS;
  else process.env.ELO_WHATSAPP_NUMBERS = NUMEROS_ORIGINAL;

  limparCacheDeNumeros();
  resetIntegrationKeyCache();
}

/** Nenhuma das duas fontes: o ponto de partida de cada caso. */
function ambienteVazio(): void {
  delete process.env.ELO_WHATSAPP_NUMBERS;
  process.env.ELO_INTEGRATION_PUBLIC_KEYS = "{}";
  limparCacheDeNumeros();
  resetIntegrationKeyCache();
}

beforeAll(() => {
  ambienteVazio();
});

afterEach(() => {
  ambienteVazio();
});

afterAll(async () => {
  restaurarAmbiente();
  await fecharPrisma();
});

/* ══ 1. cada fonte, sozinha ═════════════════════════════════════════ */

describe("cada configuração sozinha basta", () => {
  it("só ELO_WHATSAPP_NUMBERS: acha o tenant", async () => {
    const t = randomUUID();
    process.env.ELO_WHATSAPP_NUMBERS = registroDeNumeros([t]);
    limparCacheDeNumeros();

    const d = await tenantsParaVarrer([]);

    expect(d.tenants).toEqual([t]);
    expect(d.fonte).toBe("CONFIGURACAO");
    expect(d.avisos).toEqual([]);
  });

  it("só ELO_INTEGRATION_PUBLIC_KEYS: acha o tenant", async () => {
    // O caso do ambiente real: FISCALE configurado, WhatsApp não.
    const t = randomUUID();
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = registroDeChaves([t]);
    resetIntegrationKeyCache();

    const d = await tenantsParaVarrer([]);

    expect(d.tenants).toEqual([t]);
    expect(d.fonte).toBe("CONFIGURACAO");
    expect(d.avisos).toEqual([]);
  });
});

/* ══ 2. a união ═════════════════════════════════════════════════════ */

describe("as duas fontes entram em união", () => {
  it("tenants diferentes: soma os dois", async () => {
    const doWa = randomUUID();
    const daInt = randomUUID();
    process.env.ELO_WHATSAPP_NUMBERS = registroDeNumeros([doWa]);
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = registroDeChaves([daInt]);
    limparCacheDeNumeros();
    resetIntegrationKeyCache();

    const d = await tenantsParaVarrer([]);

    expect(d.tenants).toHaveLength(2);
    expect(d.tenants).toContain(doWa);
    expect(d.tenants).toContain(daInt);
  });

  it("o MESMO tenant nas duas fontes aparece UMA vez", async () => {
    // O caso normal depois que o WhatsApp entrar: o escritório estará nas
    // duas. Varrer duas vezes não duplicaria evento (o índice único
    // parcial segura), mas dobraria o trabalho e mentiria na contagem.
    const t = randomUUID();
    process.env.ELO_WHATSAPP_NUMBERS = registroDeNumeros([t]);
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = registroDeChaves([t]);
    limparCacheDeNumeros();
    resetIntegrationKeyCache();

    const d = await tenantsParaVarrer([]);

    expect(d.tenants).toEqual([t]);
  });

  it("dois números do MESMO escritório também dão um só", async () => {
    const t = randomUUID();
    process.env.ELO_WHATSAPP_NUMBERS = JSON.stringify({
      "558199000001": { tenantId: t },
      "558199000002": { tenantId: t },
    });
    limparCacheDeNumeros();

    expect((await tenantsParaVarrer([])).tenants).toEqual([t]);
  });
});

/* ══ 3. o que não é uuid ════════════════════════════════════════════ */

describe("só uuid entra", () => {
  it("tenant que não é uuid é descartado, e o bom passa", async () => {
    const bom = randomUUID();
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = registroDeChaves([bom, "escritorio-modelo"]);
    resetIntegrationKeyCache();

    const d = await tenantsParaVarrer([]);

    expect(d.tenants).toEqual([bom]);
    expect(d.invalidos).toEqual(["escritorio-modelo"]);
  });

  it("descartado NÃO é silenciado: sai na lista de inválidos", async () => {
    // Sem isto o `main` não teria o que registrar, e uma configuração
    // errada seria indistinguível de um escritório que não existe.
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = registroDeChaves(["nao-e-uuid"]);
    resetIntegrationKeyCache();

    const d = await tenantsParaVarrer([]);

    expect(d.tenants).toEqual([]);
    expect(d.invalidos).toEqual(["nao-e-uuid"]);
    expect(d.fonte).toBe("NENHUMA");
  });
});

/* ══ 4. a linha de comando manda ════════════════════════════════════ */

describe("argumento explícito tem precedência", () => {
  it("o argumento vence a configuração", async () => {
    const naConfig = randomUUID();
    const pedido = randomUUID();
    process.env.ELO_WHATSAPP_NUMBERS = registroDeNumeros([naConfig]);
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = registroDeChaves([naConfig]);
    limparCacheDeNumeros();
    resetIntegrationKeyCache();

    const d = await tenantsParaVarrer([pedido]);

    expect(d.tenants).toEqual([pedido]);
    expect(d.fonte).toBe("ARGUMENTOS");
    expect(d.tenants).not.toContain(naConfig);
  });

  it("vários argumentos, sem repetir", async () => {
    const a = randomUUID();
    const b = randomUUID();

    expect((await tenantsParaVarrer([a, b, a])).tenants).toEqual([a, b]);
  });

  it("argumento inválido NÃO faz cair na configuração", async () => {
    // A armadilha: um tenant digitado errado viraria "varre todo mundo".
    // Quem pediu explicitamente tem de receber erro, não uma varredura
    // mais ampla do que pediu.
    const naConfig = randomUUID();
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = registroDeChaves([naConfig]);
    resetIntegrationKeyCache();

    const d = await tenantsParaVarrer(["14f56987-nao-é-uuid"]);

    expect(d.tenants).toEqual([]);
    expect(d.fonte).toBe("ARGUMENTOS");
    expect(d.tenants).not.toContain(naConfig);
  });
});

/* ══ 5. configuração que não presta ═════════════════════════════════ */

describe("uma fonte ruim não cala a outra", () => {
  it("chave de integração quebrada: avisa e mantém o do WhatsApp", async () => {
    // `integrationKeys()` LANÇA quando a chave não é Ed25519 válida. Se
    // essa exceção subisse, um escritório com chave torta deixaria TODOS
    // os outros sem escalonamento.
    const doWa = randomUUID();
    process.env.ELO_WHATSAPP_NUMBERS = registroDeNumeros([doWa]);
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = JSON.stringify({
      torta: { tenantId: randomUUID(), publicKey: "isto-nao-e-uma-chave" },
    });
    limparCacheDeNumeros();
    resetIntegrationKeyCache();

    const d = await tenantsParaVarrer([]);

    expect(d.tenants).toEqual([doWa]);
    expect(d.avisos).toHaveLength(1);
    expect(d.avisos[0]).toContain("ELO_INTEGRATION_PUBLIC_KEYS");
  });

  it("JSON inválido nos números não derruba a integração", async () => {
    const daInt = randomUUID();
    process.env.ELO_WHATSAPP_NUMBERS = "{isto nao e json";
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = registroDeChaves([daInt]);
    limparCacheDeNumeros();
    resetIntegrationKeyCache();

    // `mapa()` já engole JSON quebrado por conta própria — o que este
    // caso trava é que o engolir dele não tira a outra fonte do caminho.
    expect((await tenantsParaVarrer([])).tenants).toEqual([daInt]);
  });

  it("nenhuma das fontes: a saída de sempre", async () => {
    const d = await tenantsParaVarrer([]);

    expect(d.tenants).toEqual([]);
    expect(d.fonte).toBe("NENHUMA");
    expect(d.invalidos).toEqual([]);
    expect(d.avisos).toEqual([]);
  });
});

/* ══ 6. tenant configurado que não existe no banco ══════════════════ */

describe("tenant antigo na configuração", () => {
  it("uuid válido sem escritório no banco varre zero, sem erro", async () => {
    // O `.env` real tem um tenant residual de ambiente antigo. Ele é uuid
    // legítimo, então passa no filtro — e a varredura dele tem de sair
    // limpa, porque `withTenant` só valida FORMATO e o RLS simplesmente
    // não devolve linha. Se isto lançasse, a Tarefa Agendada acumularia
    // uma falha por minuto por causa de um resíduo de configuração.
    const fantasma = randomUUID();
    process.env.ELO_INTEGRATION_PUBLIC_KEYS = registroDeChaves([fantasma]);
    resetIntegrationKeyCache();

    const d = await tenantsParaVarrer([]);
    expect(d.tenants).toEqual([fantasma]);

    const r = await escalonarPendentes(fantasma, new Date());

    expect(r.escaladas).toBe(0);
    expect(r.examinadas).toBe(0);
  });
});

/* ══ 7. a configuração real desta instalação ════════════════════════ */

describe("a configuração real", () => {
  it("encontra o tenant do Escritorio Modelo pelo `.env` de hoje", async () => {
    // Não é um caso inventado: restaura o ambiente como o `setup.ts` o
    // entregou — isto é, o `.env` real — e cobra o resultado.
    //
    // Se este teste falhar, a leitura certa NÃO é "conserte o teste": é
    // que a configuração deixou de nomear o escritório e a varredura
    // voltou a rodar em branco.
    restaurarAmbiente();

    const d = await tenantsParaVarrer([]);

    expect(d.tenants).toContain(TENANT_REAL);
    expect(d.fonte).toBe("CONFIGURACAO");
    expect(d.avisos).toEqual([]);
  });
});
