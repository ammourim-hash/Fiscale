/**
 * Ensaio da triagem no `elo_dev`. Nunca no `elo`.
 *
 * Roda o caminho REAL — `processar()` do webhook, os mesmos serviços, a
 * mesma decisão — contra dados inventados, e imprime o que ficou. Existe
 * para conferir a olho o que os testes já travam, e para produzir as
 * conversas que a tela vai mostrar.
 *
 *     npx tsx --env-file=.env.dev scripts/triagem-dev.ts
 *
 * A trava é explícita: se a URL não apontar para `elo_dev`, ele para. Sem
 * fallback — o fallback seria justamente o acidente a evitar.
 */
import { createDepartment, provisionMembership, provisionTenant } from "@/server/admin/provisioning";
import { processar } from "@/server/channels/whatsapp/inbound";
import { CANAL_WHATSAPP } from "@/server/channels/whatsapp/constantes";
import { SETORES } from "@/server/conversations/triagem";
import { withTenant } from "@/server/tenancy";

const url = process.env.DATABASE_URL ?? "";
if (!/\/elo_dev(\?|$)/.test(url)) {
  console.error("RECUSADO: DATABASE_URL nao aponta para elo_dev.");
  process.exit(1);
}

const NUMERO = "555444333222111";
const marca = new Date().toISOString().slice(11, 19).replace(/:/g, "");

async function main(): Promise<void> {
  const tenant = await provisionTenant({
    slug: `triagem-${marca}`,
    name: `Ensaio de triagem ${marca}`,
  });
  console.log(`tenant: ${tenant.id}`);

  const areas = new Map<string, string>();
  for (const s of SETORES) {
    const d = await createDepartment({ tenantId: tenant.id, slug: s.slug, name: s.rotulo });
    areas.set(s.slug, d.id);
  }

  // Uma pessoa por area, para o aviso ter para quem ir.
  for (const s of SETORES) {
    const m = await provisionMembership({
      tenantId: tenant.id,
      role: "AGENT",
      // A identidade e global (e-mail unico no banco inteiro), entao o
      // ensaio precisa de um endereco novo a cada execucao.
      email: `${s.slug}.${marca}@ensaio.teste`,
      name: `Pessoa do ${s.rotulo}`,
      fiscaleUid: `${s.slug}.${marca}`,
    });
    await withTenant(tenant.id, async (tx) => {
      await tx.departmentMembership.create({
        data: {
          tenantId: tenant.id,
          departmentId: areas.get(s.slug)!,
          membershipId: m.membershipId,
        },
      });
    });
  }

  const tenantPorNumero = (n: string) => (n === NUMERO ? tenant.id : null);
  let wa = 0;

  async function cenario(nome: string, falas: string[]): Promise<void> {
    wa += 1;
    const de = `558177${String(wa).padStart(7, "0")}`;
    let n = 0;
    for (const fala of falas) {
      n += 1;
      await processar(
        [
          {
            tipo: "MENSAGEM",
            phoneNumberId: NUMERO,
            de,
            externalMessageId: `wamid.${marca}.${wa}.${n}`,
            timestamp: Math.floor(Date.now() / 1000),
            texto: fala,
            nomeDoPerfil: nome,
          },
        ],
        tenantPorNumero,
      );
    }

    const c = await withTenant(tenant.id, async (tx) =>
      tx.conversation.findFirstOrThrow({
        where: { channel: CANAL_WHATSAPP, externalContactId: de },
        select: {
          id: true,
          status: true,
          assignedMembershipId: true,
          department: { select: { name: true } },
          messages: { select: { direction: true, senderMembershipId: true } },
          events: { select: { type: true, actorMembershipId: true, metadata: true } },
        },
      }),
    );

    const triado = c.events.find((e) => e.type === "TRIAGED");
    const automaticas = c.messages.filter(
      (m) => m.direction === "OUTBOUND" && m.senderMembershipId === null,
    ).length;

    console.log(
      [
        `\n${nome}`,
        `  falas.............. ${falas.map((f) => JSON.stringify(f)).join(", ")}`,
        `  status............. ${c.status}`,
        `  setor.............. ${c.department?.name ?? "— (Sem setor)"}`,
        `  responsavel........ ${c.assignedMembershipId ?? "nenhum"}`,
        `  TRIAGED............ ${triado ? `sim, ator=${triado.actorMembershipId ?? "NULO"} ${JSON.stringify(triado.metadata)}` : "nao"}`,
        `  msgs automaticas... ${automaticas}`,
      ].join("\n"),
    );
  }

  // A PRIMEIRA mensagem ja sendo escolha: tria na hora, sem esperar uma
  // segunda e sem mandar nada de volta.
  await cenario("1a mensagem \"1\"", ["1"]);
  await cenario("1a mensagem \"Fiscal\"", ["Fiscal"]);
  await cenario("1a mensagem \"2\"", ["2"]);
  await cenario("1a mensagem \"3\"", ["3"]);
  // Errou e se corrigiu sozinho: nao leva orientacao nenhuma.
  await cenario("1a invalida, 2a valida", ["bom dia", "3"]);

  await cenario("Fiscal por numero", ["bom dia", "1"]);
  await cenario("Contabil por nome", ["oi", "CONTÁBIL"]);
  await cenario("DP por extenso", ["ola", "departamento pessoal"]);
  await cenario("Sem setor (so uma fala)", ["bom dia"]);
  await cenario("Invalida (duas opcoes)", ["oi", "fiscal e contabil"]);
  await cenario("Invalida (texto livre)", ["oi", "minha guia nao chegou", "e ai?"]);

  const contagem = await withTenant(tenant.id, async (tx) => ({
    semSetor: await tx.conversation.count({
      where: { status: "NEW", assignedDepartmentId: null },
    }),
    comSetor: await tx.conversation.count({
      where: { status: "NEW", assignedDepartmentId: { not: null } },
    }),
  }));
  console.log(`\nfila "Sem setor": ${contagem.semSetor}  |  em fila de setor: ${contagem.comSetor}`);
}

main()
  .then(() => process.exit(0))
  .catch((e: unknown) => {
    console.error(e);
    process.exit(1);
  });
