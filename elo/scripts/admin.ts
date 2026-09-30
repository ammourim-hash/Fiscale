/**
 * Ferramenta administrativa do Elo. Linha de comando, nunca rota.
 *
 *   npm run admin -- tenant:criar     <slug> <nome>
 *   npm run admin -- pessoa:criar     <tenantId> <papel> <email> <nome> <loginFiscale>
 *   npm run admin -- pessoa:vincular  <tenantId> <papel> <identityId>
 *   npm run admin -- depto:criar      <tenantId> <slug> <nome>
 *   npm run admin -- triagem:setores  <tenantId> [--aplicar]
 *
 * `triagem:setores` e o unico que tem ENSAIO: sem `--aplicar` ele so
 * mostra o que faria. Provisionar os tres setores da triagem e uma
 * operacao de producao, e operacao de producao se ve antes de fazer.
 *
 * `pessoa:vincular` e o caminho de quem JA existe entrando num segundo
 * escritorio: informe o identityId obtido no tenant onde a pessoa ja
 * trabalha. O tenant novo nao consegue procurar por e-mail — e isso e o
 * isolamento funcionando.
 */
import "dotenv/config";

process.env.ELO_ADMIN_TOOL = "1";

const [comando, ...args] = process.argv.slice(2);

async function main(): Promise<void> {
  const {
    provisionTenant,
    provisionMembership,
    createDepartment,
    assignDepartment,
    planejarDepartamentos,
    garantirDepartamentos,
  } = await import("../src/server/admin/provisioning");
  const { disconnect } = await import("../src/server/db/client");
  const { Role } = await import("../src/generated/prisma/enums");

  const papel = (v: string | undefined): (typeof Role)[keyof typeof Role] => {
    const alvo = (v ?? "").toUpperCase();
    if (!Object.keys(Role).includes(alvo)) {
      throw new Error(`papel invalido: ${v}. Use ${Object.keys(Role).join(" | ")}`);
    }
    return alvo as (typeof Role)[keyof typeof Role];
  };

  const exigir = (i: number, nome: string): string => {
    const v = args[i];
    if (!v) throw new Error(`falta o argumento <${nome}>`);
    return v;
  };

  try {
    switch (comando) {
      case "tenant:criar": {
        const t = await provisionTenant({
          slug: exigir(0, "slug"),
          name: exigir(1, "nome"),
        });
        console.log(JSON.stringify(t, null, 2));
        break;
      }

      case "pessoa:criar": {
        const m = await provisionMembership({
          tenantId: exigir(0, "tenantId"),
          role: papel(args[1]),
          email: exigir(2, "email"),
          name: exigir(3, "nome"),
          fiscaleUid: exigir(4, "loginFiscale"),
        });
        console.log(JSON.stringify(m, null, 2));
        break;
      }

      case "pessoa:vincular": {
        const m = await provisionMembership({
          tenantId: exigir(0, "tenantId"),
          role: papel(args[1]),
          identityId: exigir(2, "identityId"),
        });
        console.log(JSON.stringify(m, null, 2));
        break;
      }

      case "depto:criar": {
        const d = await createDepartment({
          tenantId: exigir(0, "tenantId"),
          slug: exigir(1, "slug"),
          name: exigir(2, "nome"),
        });
        console.log(JSON.stringify(d, null, 2));
        break;
      }

      case "triagem:setores": {
        // A lista sai de `SETORES`, do proprio modulo de triagem: se o
        // interpretador e o provisionamento tivessem cada um a sua copia,
        // um "contabil" viraria "contabilidade" num lado so e o cliente
        // escolheria um setor que nao existe.
        const { SETORES } = await import("../src/server/conversations/triagem");
        const desejados = SETORES.map((s) => ({ slug: s.slug, name: s.rotulo }));
        const tenantId = exigir(0, "tenantId");
        const aplicar = args.includes("--aplicar");

        const plano = await planejarDepartamentos(tenantId, desejados);

        if (plano.tenantName === null) {
          throw new Error(`tenant ${tenantId} nao encontrado neste banco`);
        }

        console.log(`banco............. ${(process.env.DATABASE_URL ?? "").replace(/\/\/[^@]*@/, "//***@")}`);
        console.log(`tenant............ ${plano.tenantName} (${plano.tenantId})`);
        console.log(
          `ja existem........ ${
            plano.existentes.map((d) => `${d.slug} ("${d.name}")`).join(", ") || "nenhum"
          }`,
        );
        console.log(
          `seriam criados.... ${
            plano.criar.map((d) => `${d.slug} ("${d.name}")`).join(", ") || "nenhum"
          }`,
        );
        console.log(
          `seriam mantidos... ${
            plano.preservar
              .map((p) =>
                p.nomeAtual === p.nomeProposto
                  ? p.slug
                  : `${p.slug} (fica "${p.nomeAtual}", NAO vira "${p.nomeProposto}")`,
              )
              .join(", ") || "nenhum"
          }`,
        );

        if (!aplicar) {
          console.log("");
          console.log("ENSAIO: nada foi gravado. Repita com --aplicar para criar.");
          break;
        }

        const r = await garantirDepartamentos(tenantId, desejados);
        console.log("");
        console.log(
          `APLICADO: criados ${r.criados.map((d) => d.slug).join(", ") || "nenhum"}` +
            ` | preservados ${r.preservados.join(", ") || "nenhum"}`,
        );
        break;
      }

      case "depto:pessoa": {
        const r = await assignDepartment({
          tenantId: exigir(0, "tenantId"),
          membershipId: exigir(1, "membershipId"),
          departmentId: exigir(2, "departmentId"),
          primary: args[3] === "--principal",
        });
        console.log(JSON.stringify(r, null, 2));
        break;
      }

      default:
        console.error(
          [
            "comandos:",
            "  tenant:criar     <slug> <nome>",
            "  pessoa:criar     <tenantId> <papel> <email> <nome> <loginFiscale>",
            "  pessoa:vincular  <tenantId> <papel> <identityId>",
            "  depto:criar      <tenantId> <slug> <nome>",
            "  depto:pessoa     <tenantId> <membershipId> <departmentId> [--principal]",
            "  triagem:setores  <tenantId> [--aplicar]   (sem --aplicar e ensaio)",
          ].join("\n"),
        );
        process.exitCode = 1;
    }
  } finally {
    await disconnect();
  }
}

main().catch((erro: unknown) => {
  console.error(erro instanceof Error ? erro.message : String(erro));
  process.exit(1);
});
