/**
 * Faxina de anexos órfãos.
 *
 *   npm run storage:limpar -- <tenantId> [<tenantId>...]
 *
 * Apaga o que subiu e nunca virou mensagem depois de vencido o prazo —
 * linha e bytes. É a rede de segurança do desenho descrito em
 * `src/server/media/service.ts`: banco e storage não compartilham
 * transação, então o intervalo entre gravar o arquivo e criar a mensagem
 * pode ser interrompido, e o que sobra precisa de dono.
 *
 * O tenant é argumento porque `message_attachments` tem RLS forçado: sem
 * contexto, a consulta não enxerga linha nenhuma. A alternativa seria um
 * papel com BYPASSRLS só para a manutenção — recusada, porque abriria
 * para sempre a porta que o MVP 1.0 fechou.
 *
 * A aplicação também faxina sozinha, de forma oportunista, a cada upload
 * (no máximo uma vez a cada 15 minutos). Este script existe para rodar à
 * mão e para o dia em que houver um agendador.
 */
import "dotenv/config";

process.env.ELO_ADMIN_TOOL = "1";

async function main(): Promise<void> {
  const tenants = process.argv.slice(2).filter(Boolean);
  if (tenants.length === 0) {
    throw new Error("uso: npm run storage:limpar -- <tenantId> [<tenantId>...]");
  }

  const { limparAnexosOrfaos } = await import("../src/server/media/service");
  const { disconnect } = await import("../src/server/db/client");

  try {
    for (const tenantId of tenants) {
      const r = await limparAnexosOrfaos(tenantId);
      console.log(`${tenantId}: ${r.apagados} apagado(s), ${r.falhas} falha(s)`);
    }
  } finally {
    await disconnect();
  }
}

main().catch((erro: unknown) => {
  console.error(erro instanceof Error ? erro.message : String(erro));
  process.exit(1);
});
