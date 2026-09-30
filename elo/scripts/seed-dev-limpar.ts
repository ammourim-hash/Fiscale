/**
 * Remove o que o seed de desenvolvimento criou.
 *
 *   npm run seed:dev:limpar -- <tenantId>
 *
 * Apaga apenas atendimentos do canal "DEV". Nada que tenha vindo do
 * WhatsApp ou de uso real é tocado — o filtro é pelo canal, não por data
 * nem por "os últimos N".
 */
import "dotenv/config";

process.env.ELO_ADMIN_TOOL = "1";

async function main(): Promise<void> {
  if (process.env.NODE_ENV === "production") {
    throw new Error("nao roda com NODE_ENV=production");
  }

  const tenantId = process.argv[2];
  if (!tenantId) throw new Error("uso: npm run seed:dev:limpar -- <tenantId>");

  const { withTenant } = await import("../src/server/tenancy");
  const { disconnect } = await import("../src/server/db/client");

  try {
    const apagados = await withTenant(tenantId, async (tx) => {
      // Eventos, visualizações, notas e etiquetas caem por cascata.
      const r = await tx.conversation.deleteMany({ where: { channel: "DEV" } });
      return r.count;
    });
    console.log(`${apagados} atendimento(s) de desenvolvimento removido(s).`);
  } finally {
    await disconnect();
  }
}

main().catch((erro: unknown) => {
  console.error(erro instanceof Error ? erro.message : String(erro));
  process.exit(1);
});
