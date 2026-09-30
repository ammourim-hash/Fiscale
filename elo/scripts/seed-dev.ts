/**
 * DADOS DE DESENVOLVIMENTO — não use em produção.
 *
 *   npm run seed:dev -- <tenantId>
 *
 * Cria etiquetas e alguns atendimentos sobre os clientes que JÁ existem na
 * projeção daquele tenant, para dar o que olhar na tela de Atendimentos.
 *
 * Três travas, porque semear produção é o tipo de acidente que só acontece
 * uma vez e não tem desfazer:
 *
 *   1. recusa com NODE_ENV=production;
 *   2. exige ELO_ALLOW_DEV_SEED=1;
 *   3. marca tudo com o canal "DEV" e a etiqueta "dados-de-teste", então
 *      o que veio daqui é reconhecível e removível.
 *
 * Nada disso vira mensagem falsa: atendimento é registro de trabalho, e
 * balão de conversa inventado é outra coisa — essa continua não existindo.
 */
import "dotenv/config";

process.env.ELO_ADMIN_TOOL = "1";

const CANAL_DEV = "DEV";
const ETIQUETA_DEV = "dados-de-teste";

async function main(): Promise<void> {
  if (process.env.NODE_ENV === "production") {
    throw new Error("seed de desenvolvimento nao roda com NODE_ENV=production");
  }
  if (process.env.ELO_ALLOW_DEV_SEED !== "1") {
    throw new Error("defina ELO_ALLOW_DEV_SEED=1 para confirmar que este banco e de teste");
  }

  const tenantId = process.argv[2];
  if (!tenantId) throw new Error("uso: npm run seed:dev -- <tenantId>");

  const { withTenant } = await import("../src/server/tenancy");
  const { disconnect } = await import("../src/server/db/client");
  // Os avisos de notificacao saem em segundo plano de proposito (a
  // mensagem nunca espera por eles). Num script curto isso significa
  // fechar a conexao com o aviso ainda no meio do caminho — dai a espera
  // explicita antes do `disconnect`.
  const { aguardarAvisos } = await import("../src/server/notifications/service");
  const { criarAtendimento } = await import("../src/server/conversations/service");

  try {
    const preparo = await withTenant(tenantId, async (tx) => {
      const tenant = await tx.tenant.findFirst({ select: { id: true, name: true } });
      if (!tenant) throw new Error("tenant nao encontrado (ou id errado)");

      const clientes = await tx.externalCustomerReference.findMany({
        where: { active: true },
        select: { id: true, displayName: true },
        orderBy: { displayName: "asc" },
        take: 4,
      });

      const departamentos = await tx.department.findMany({
        select: { id: true, name: true },
        orderBy: { name: "asc" },
      });

      const membros = await tx.membership.findMany({
        where: { active: true },
        select: { id: true, identity: { select: { name: true } } },
      });

      // Etiquetas do escritório. `upsert` para o script poder rodar duas
      // vezes sem estourar no unique.
      const etiquetas = [
        { slug: ETIQUETA_DEV, name: "Dados de teste", tone: "atencao" },
        { slug: "urgente", name: "Urgente", tone: "erro" },
        { slug: "documento-pendente", name: "Documento pendente", tone: "atencao" },
        { slug: "cobranca", name: "Cobrança", tone: "neutro" },
      ];
      const criadas = [];
      for (const e of etiquetas) {
        criadas.push(
          await tx.tag.upsert({
            where: { tenantId_slug: { tenantId, slug: e.slug } },
            create: { tenantId, slug: e.slug, name: e.name, tone: e.tone },
            update: { name: e.name, tone: e.tone },
            select: { id: true, slug: true, name: true },
          }),
        );
      }

      return { tenant, clientes, departamentos, membros, etiquetas: criadas };
    });

    if (preparo.clientes.length === 0) {
      console.log(
        "Nenhum cliente na projecao deste tenant. Sincronize o Fiscale primeiro —\n" +
          "atendimento sem cliente serve para pouco na tela.",
      );
      return;
    }

    const marcador = preparo.etiquetas.find((e) => e.slug === ETIQUETA_DEV)!;
    const fiscal = preparo.departamentos[0] ?? null;

    // Um de cada situação que a tela precisa saber mostrar.
    const cenarios: {
      cliente: (typeof preparo.clientes)[number];
      departmentId: string | null;
      assignedMembershipId: string | null;
      rotulo: string;
    }[] = [
      {
        cliente: preparo.clientes[0]!,
        departmentId: null,
        assignedMembershipId: null,
        rotulo: "Atendimento Geral, sem area e sem responsavel",
      },
      {
        cliente: preparo.clientes[1] ?? preparo.clientes[0]!,
        departmentId: fiscal?.id ?? null,
        assignedMembershipId: null,
        rotulo: "fila departamental: area definida, sem responsavel",
      },
      {
        cliente: preparo.clientes[2] ?? preparo.clientes[0]!,
        departmentId: fiscal?.id ?? null,
        assignedMembershipId: preparo.membros[0]?.id ?? null,
        rotulo: "escolhido pelo cliente, aguardando alguem assumir",
      },
    ];

    const criados: string[] = [];
    for (const c of cenarios) {
      const nova = await criarAtendimento(tenantId, {
        customerReferenceId: c.cliente.id,
        channel: CANAL_DEV,
        assignedDepartmentId: c.departmentId,
        assignedMembershipId: c.assignedMembershipId,
      });

      await withTenant(tenantId, async (tx) => {
        await tx.conversationTag.create({
          data: { tenantId, conversationId: nova.id, tagId: marcador.id },
        });
      });

      criados.push(`  ${nova.id}  ${c.cliente.displayName} — ${c.rotulo}`);
    }

    console.log(`Tenant: ${preparo.tenant.name}`);
    console.log(`Etiquetas: ${preparo.etiquetas.map((e) => e.name).join(", ")}`);
    console.log(`Atendimentos DEV criados (canal "${CANAL_DEV}"):`);
    console.log(criados.join("\n"));
    console.log(
      `\nTodos marcados com a etiqueta "${marcador.name}". Para limpar:\n` +
        `  npm run seed:dev:limpar -- ${tenantId}`,
    );
  } finally {
    await aguardarAvisos();
    await disconnect();
  }
}

main().catch((erro: unknown) => {
  console.error(erro instanceof Error ? erro.message : String(erro));
  process.exit(1);
});
