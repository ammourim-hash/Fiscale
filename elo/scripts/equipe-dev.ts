/**
 * Cenário de Admin > Equipe no `elo_dev`. Nunca no `elo`.
 *
 *     npx tsx --env-file=.env.dev scripts/equipe-dev.ts
 *
 * Monta um escritório inventado com as três áreas e gente suficiente para
 * a tela ter o que mostrar: um setor coberto, um descoberto e alguém de
 * férias — que é o caso em que "vinculados" e "elegíveis" divergem, e o
 * único que prova que a cobertura não é um `count`.
 *
 * A trava é explícita: se a URL não apontar para `elo_dev`, ele para.
 * Sem fallback — o fallback seria justamente o acidente a evitar.
 */
import { provisionTenant } from "@/server/admin/provisioning";
import { criarPessoa } from "@/server/team/service";
import { withTenant } from "@/server/tenancy";
import type { AuthContext } from "@/server/auth/context";
import type { Role } from "@/generated/prisma/enums";

const url = process.env.DATABASE_URL ?? "";
if (!/\/elo_dev(\?|$)/.test(url)) {
  console.error("RECUSADO: DATABASE_URL nao aponta para elo_dev.");
  process.exit(1);
}

const marca = new Date().toISOString().slice(11, 19).replace(/:/g, "");

async function main(): Promise<void> {
  const tenant = await provisionTenant({
    slug: `equipe-${marca}`,
    name: `Escritorio de ensaio ${marca}`,
  });

  const areas = new Map<string, string>();
  await withTenant(tenant.id, async (tx) => {
    for (const [slug, name] of [
      ["fiscal", "Fiscal"],
      ["contabil", "Contábil"],
      ["dp", "DP"],
    ]) {
      const d = await tx.department.create({
        data: { tenantId: tenant.id, slug: slug!, name: name! },
        select: { id: true },
      });
      areas.set(slug!, d.id);
    }
  });

  // A dona do escritório precisa existir antes para que o resto seja
  // criado sob a autorização dela — o mesmo caminho da tela.
  const donaId = await withTenant(tenant.id, async (tx) => {
    const identityId = crypto.randomUUID();
    await tx.$executeRaw`
      INSERT INTO identities (id, email, name, active, created_at)
      VALUES (${identityId}::uuid, ${`dona.${marca}@ensaio.teste`}, ${"Dona do Escritorio"}, true, now())
    `;
    const m = await tx.membership.create({
      data: { tenantId: tenant.id, identityId, role: "OWNER" as Role },
      select: { id: true },
    });
    await tx.identityProvider.create({
      data: { identityId, provider: "fiscale", providerUid: `dona.${marca}` },
    });
    return m.id;
  });

  const ctx: AuthContext = {
    sessionId: "00000000-0000-4000-8000-000000000001",
    tenantId: tenant.id,
    membershipId: donaId,
    identityId: "",
    email: `dona.${marca}@ensaio.teste`,
    name: "Dona do Escritorio",
    role: "OWNER" as Role,
  };

  const pessoas: [string, Role, string[], boolean][] = [
    ["Ana do Fiscal", "AGENT" as Role, ["fiscal"], true],
    ["Bruno do Fiscal", "AGENT" as Role, ["fiscal"], true],
    ["Clara do Fiscal", "AGENT" as Role, ["fiscal"], true],
    // De férias: conta como vínculo e NÃO conta como cobertura.
    ["Davi de Ferias", "AGENT" as Role, ["dp"], false],
    ["Elisa do DP", "AGENT" as Role, ["dp"], true],
    // Coordenação nas três áreas — sem regra especial: é encontrada pelas
    // três filas porque pertence às três.
    ["Fabio Coordenacao", "MANAGER" as Role, ["fiscal", "contabil", "dp"], true],
  ];

  let n = 0;
  for (const [nome, papel, slugs, disponivel] of pessoas) {
    n += 1;
    await criarPessoa(ctx, {
      name: nome,
      email: `p${n}.${marca}@ensaio.teste`,
      fiscaleUid: `p${n}.${marca}`,
      role: papel,
      availableForAssignment: disponivel,
      departmentIds: slugs.map((s) => areas.get(s)!),
      primaryDepartmentId: areas.get(slugs[0]!)!,
    });
  }

  console.log(JSON.stringify({ tenantId: tenant.id, donaUid: `dona.${marca}` }, null, 2));
}

main()
  .then(() => process.exit(0))
  .catch((e: unknown) => {
    console.error(e);
    process.exit(1);
  });
