/**
 * ENTRADA DE MENSAGEM PARA DESENVOLVIMENTO — não use em produção.
 *
 *   ELO_ALLOW_DEV_INBOUND=1 npm run dev:inbound -- <tenantId> <conversationId> "texto"
 *
 * O tenant é argumento porque não há como descobri-lo: `conversations`
 * está sob RLS, e sem contexto de tenant a consulta não devolve linha
 * nenhuma — que é exatamente o comportamento desejado. Tenant errado não
 * entrega no escritório errado: simplesmente não encontra o atendimento.
 *
 * Faz o que o WhatsApp fará quando existir: entrega uma mensagem de
 * entrada. Serve para exercitar não lidas, realtime e visualização
 * individual sem depender da interface.
 *
 * As travas são as mesmas da rota, e vêm do MESMO módulo — duas cópias da
 * regra virariam duas regras no primeiro ajuste.
 */
import "dotenv/config";

process.env.ELO_ADMIN_TOOL = "1";

async function main(): Promise<void> {
  const { entradaDevLiberada, CANAL_DEV, REMETENTE_DEV } = await import(
    "../src/server/messages/dev-inbound"
  );

  const trava = entradaDevLiberada();
  if (!trava.liberada) {
    throw new Error(`entrada dev bloqueada: ${trava.motivo}`);
  }

  const tenantId = process.argv[2];
  const conversationId = process.argv[3];
  const texto = process.argv.slice(4).join(" ");
  if (!tenantId || !conversationId || !texto) {
    throw new Error('uso: npm run dev:inbound -- <tenantId> <conversationId> "texto"');
  }

  const { withTenant } = await import("../src/server/tenancy");
  const { disconnect } = await import("../src/server/db/client");
  // Os avisos de notificacao saem em segundo plano de proposito (a
  // mensagem nunca espera por eles). Num script curto isso significa
  // fechar a conexao com o aviso ainda no meio do caminho — dai a espera
  // explicita antes do `disconnect`.
  const { aguardarAvisos } = await import("../src/server/notifications/service");
  const { registrarMensagemRecebida } = await import("../src/server/messages/service");

  try {
    const r = await registrarMensagemRecebida(tenantId, conversationId, {
      content: texto,
      externalSenderId: REMETENTE_DEV,
      channel: CANAL_DEV,
    });

    if (!r.ok) throw new Error(r.message);

    console.log(`mensagem ${r.value.sequence} gravada (${r.value.id})`);

    const estado = await withTenant(tenantId, async (tx) =>
      tx.conversation.findUniqueOrThrow({
        where: { id: conversationId },
        select: { status: true },
      }),
    );
    console.log(`status do atendimento: ${estado.status}`);
  } finally {
    await aguardarAvisos();
    await disconnect();
  }
}

main().catch((erro: unknown) => {
  console.error(erro instanceof Error ? erro.message : String(erro));
  process.exit(1);
});
