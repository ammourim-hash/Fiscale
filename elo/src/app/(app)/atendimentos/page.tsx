/**
 * Atendimentos.
 *
 * A sessão é resolvida no servidor; o `membershipId` desce para a tela
 * porque "Meus" e "assumido por mim" precisam saber quem é você — e essa
 * resposta não pode vir do navegador.
 *
 * `podeEnviar` e `entradaDev` também são decididos aqui. O servidor
 * confere de novo nas rotas: o que desce para a tela existe para ela não
 * oferecer o que vai ser recusado, e não como controle de acesso.
 *
 * ---------------------------------------------------------------------
 *  `?abrir=<id>` — o destino de uma notificação
 * ---------------------------------------------------------------------
 *  Vem de um clique na notificação do Windows ou do celular. Ele NÃO
 *  concede acesso a nada: decide qual tela abrir, e o atendimento em si é
 *  carregado pela API com sessão, tenant, RBAC e RLS. Um id de outro
 *  escritório não é encontrado, e a tela diz isso.
 *
 *  Por isso o valor é só validado como UUID aqui — não para autorizar, mas
 *  para não mandar lixo à API por causa de uma URL colada errado.
 */
import { redirect } from "next/navigation";

import { ConversationsScreen } from "@/components/conversations";
import { can } from "@/server/auth/context";
import { pageSession } from "@/server/auth/page-session";
import { entradaDevLiberada } from "@/server/messages/dev-inbound";

export const dynamic = "force-dynamic";

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export default async function Atendimentos({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sessao = await pageSession();
  if (!sessao) redirect("/entrar");

  const params = await searchParams;
  const pedido = typeof params.abrir === "string" ? params.abrir : null;

  return (
    <ConversationsScreen
      membershipId={sessao.context.membershipId}
      podeEnviar={can(sessao.context, "messages.send")}
      entradaDev={entradaDevLiberada().liberada}
      abrirInicial={pedido && UUID.test(pedido) ? pedido : null}
    />
  );
}
