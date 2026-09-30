/**
 * Escalonamento periódico — a coordenação é avisada do que não andou.
 *
 *   npm run escalonar                          (os tenants configurados)
 *   npm run escalonar -- <tenantId> [<tenantId>...]   (exatamente esses)
 *
 * =====================================================================
 *  Por que existe, se o webhook já escalona
 * =====================================================================
 *  O webhook só escalona quando alguém escreve. Um caso que chega às
 *  16h50 e fica sem resposta precisa ser avisado às 17h20 mesmo que
 *  ninguém mais mande mensagem — e é justamente o escritório calado que
 *  produz o atendimento esquecido.
 *
 * =====================================================================
 *  De onde sai a lista de escritórios
 * =====================================================================
 *  De três lugares, nesta ordem de precedência:
 *
 *    1. os ARGUMENTOS da linha de comando, quando houver algum. Eles são
 *       autoridade: quem digitou um tenant quer aquele tenant, e não a
 *       configuração. Se todos forem inválidos o script FALHA, em vez de
 *       cair na configuração — um erro de digitação que vira "varredura
 *       completa" é pior do que um erro de digitação que aparece.
 *
 *    2. `ELO_WHATSAPP_NUMBERS`, via `numerosConfigurados()` — a mesma
 *       fonte que o webhook usa para saber de quem é cada mensagem. É a
 *       primária olhando para a frente: quando o canal do WhatsApp
 *       entrar, todo escritório atendido estará ali.
 *
 *    3. `ELO_INTEGRATION_PUBLIC_KEYS`, via `integrationKeys()` — o
 *       registro `kid → {tenantId, publicKey}` com que o FISCALE assina
 *       as chamadas de integração. Um escritório que entra pelo FISCALE
 *       está nesse mapa mesmo sem canal de WhatsApp nenhum, e foi por
 *       isso que a varredura não achava ninguém: o `.env` real não tem
 *       número de WhatsApp, porque o número do escritório segue no
 *       WhatsApp Business App (decisão M13).
 *
 *  As duas configurações entram em UNIÃO, sem duplicar. Nenhuma variável
 *  nova foi criada para isto de propósito: seriam três lugares dizendo a
 *  mesma coisa, e o terceiro é sempre o que fica desatualizado.
 *
 *  E por que não uma consulta ao banco: `tenants` tem RLS FORÇADO com
 *  `id = app_tenant_id()`. Sem contexto, um `SELECT * FROM tenants`
 *  devolve ZERO linhas — não é bug, é o desenho. Listar todos exigiria
 *  BYPASSRLS ou o superusuário, e o projeto fechou essa porta no MVP 1.0
 *  (ver `media/service.ts`): abri-la para uma tarefa de manutenção é
 *  abri-la para sempre. A configuração é a autoridade, como em S12/M17.
 *
 *  A consequência, dita em voz alta: escritório que não está em NENHUMA
 *  das duas configurações não é varrido por este script.
 *
 *  Cada fonte é lida dentro do seu próprio `try`: uma configuração
 *  malformada — e `integrationKeys()` LANÇA quando a chave não presta —
 *  não pode calar a outra e deixar todo mundo sem escalonamento.
 *
 * =====================================================================
 *  Idempotente por construção
 * =====================================================================
 *  Rodar de minuto em minuto é seguro: quem decide se avisa é o índice
 *  único parcial sobre o evento ESCALATED, não este script. Duas
 *  execuções sobrepostas, ou uma sobreposta ao webhook, produzem UM
 *  aviso — ver `escalonamento.ts`.
 *
 *  Não manda mensagem para cliente nenhum: usa o mesmo caminho de
 *  notificação de sempre (SSE + Web Push), pelos destinatários que o
 *  `notifications/service.ts` decide.
 */
import { pathToFileURL } from "node:url";

import "dotenv/config";

/** De onde a lista veio — o chamador precisa disso para tratar o vazio. */
export type FonteDosTenants = "ARGUMENTOS" | "CONFIGURACAO" | "NENHUMA";

export interface DescobertaDeTenants {
  /** Os tenants a varrer, sem repetição e na ordem em que apareceram. */
  tenants: string[];
  fonte: FonteDosTenants;
  /** Valores recusados por não serem uuid. Não são silenciados: viram log. */
  invalidos: string[];
  /** Fonte que não pôde ser lida, com o motivo. Uma não cala a outra. */
  avisos: string[];
}

/**
 * A lista de escritórios a varrer — ver o cabeçalho para a ordem.
 *
 * O filtro de uuid usa `assertTenantId`, a MESMA validação que abre o
 * contexto de RLS. Uma quarta cópia da expressão regular seria a cópia
 * que um dia divergiria das outras três.
 */
export async function tenantsParaVarrer(argumentos: string[]): Promise<DescobertaDeTenants> {
  // A anotação não é enfeite, e a variável separada também não: para
  // chamar uma função de ASSERÇÃO o TypeScript exige um nome declarado com
  // tipo explícito, e desestruturar não conta (TS2775).
  type AfirmaTenant = (valor: unknown) => asserts valor is string;
  const tenancy = await import("../src/server/tenancy");
  const afirmarTenant: AfirmaTenant = tenancy.assertTenantId;

  const invalidos: string[] = [];
  const avisos: string[] = [];

  const separar = (valores: string[]): string[] => {
    const validos: string[] = [];
    for (const valor of valores) {
      try {
        afirmarTenant(valor);
        validos.push(valor);
      } catch {
        invalidos.push(valor);
      }
    }
    return validos;
  };

  const semRepetir = (valores: string[]): string[] => [...new Set(valores)];

  // 1. Linha de comando: se veio ALGO, ela manda — mesmo que o que veio
  //    não preste. Cair na configuração aqui esconderia o erro de digitação.
  if (argumentos.length > 0) {
    return {
      tenants: semRepetir(separar(argumentos)),
      fonte: "ARGUMENTOS",
      invalidos,
      avisos,
    };
  }

  const daFonte = async (rotulo: string, ler: () => Promise<string[]>): Promise<string[]> => {
    try {
      return await ler();
    } catch (erro: unknown) {
      avisos.push(`${rotulo}: ${erro instanceof Error ? erro.message : String(erro)}`);
      return [];
    }
  };

  // 2. Os números do WhatsApp.
  const doWhatsapp = await daFonte("ELO_WHATSAPP_NUMBERS", async () => {
    const { numerosConfigurados } = await import("../src/server/channels/whatsapp/config");
    return numerosConfigurados().map((n) => n.tenantId);
  });

  // 3. As chaves de integração do FISCALE.
  const daIntegracao = await daFonte("ELO_INTEGRATION_PUBLIC_KEYS", async () => {
    const { integrationKeys } = await import("../src/server/integrations/signature");
    return [...integrationKeys().values()].map((c) => c.tenantId);
  });

  const tenants = semRepetir(separar([...doWhatsapp, ...daIntegracao]));

  return {
    tenants,
    fonte: tenants.length > 0 ? "CONFIGURACAO" : "NENHUMA",
    invalidos,
    avisos,
  };
}

async function main(): Promise<void> {
  const { escalonarPendentes } = await import("../src/server/conversations/escalonamento");
  const { disconnect } = await import("../src/server/db/client");

  const descoberta = await tenantsParaVarrer(process.argv.slice(2).filter(Boolean));

  // Configuração quebrada é barulho legítimo: aparece toda vez, porque é
  // defeito que alguém tem de consertar.
  for (const aviso of descoberta.avisos) {
    console.error(`escalonamento: fonte ilegivel em ${aviso}`);
  }
  if (descoberta.invalidos.length > 0) {
    console.error(
      `escalonamento: ${descoberta.invalidos.length} tenant(s) ignorado(s) por nao serem uuid`,
    );
  }

  if (descoberta.tenants.length === 0) {
    if (descoberta.fonte === "ARGUMENTOS") {
      // Pedido explícito que não rendeu tenant nenhum é erro de quem pediu.
      throw new Error("nenhum tenant valido nos argumentos");
    }
    // Não é erro: é um deploy sem escritório configurado. Sair com 0 evita
    // que uma Tarefa Agendada fique acumulando falha por nada.
    console.log("escalonamento: nenhum tenant configurado, nada a varrer");
    await disconnect();
    return;
  }

  const agora = new Date();
  let escaladas = 0;
  let falhas = 0;

  try {
    for (const tenantId of descoberta.tenants) {
      try {
        const r = await escalonarPendentes(tenantId, agora);
        escaladas += r.escaladas;
        // Silêncio quando não há nada: um log por minuto, 24 h por dia,
        // vira ruído que ninguém lê — e que esconde a linha que importa.
        if (r.escaladas > 0) {
          console.log(`${tenantId}: ${r.escaladas} escalonado(s) de ${r.examinadas} examinado(s)`);
        }
      } catch (erro: unknown) {
        falhas += 1;
        console.error(`${tenantId}: ${erro instanceof Error ? erro.message : String(erro)}`);
      }
    }
  } finally {
    await disconnect();
  }

  // Falha em UM tenant não pode ser lida como sucesso: a Tarefa Agendada
  // precisa de um código que a faça aparecer no histórico dela.
  if (falhas > 0) {
    throw new Error(`${falhas} tenant(s) falharam; ${escaladas} escalonado(s) no total`);
  }
}

/**
 * Só roda sozinho quando FOI CHAMADO — o teste importa este arquivo para
 * exercitar `tenantsParaVarrer` de verdade, e sem esta guarda o import
 * disparava uma varredura de banco no meio da suíte.
 */
const entrada = process.argv[1];
if (entrada !== undefined && pathToFileURL(entrada).href === import.meta.url) {
  main().catch((erro: unknown) => {
    console.error(erro instanceof Error ? erro.message : String(erro));
    process.exit(1);
  });
}
