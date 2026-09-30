import { CustomerBrowser } from "@/components/customers";
import { formatSyncedAt } from "@/lib/format";
import { pageSession } from "@/server/auth/page-session";
import { projectionStatus } from "@/server/customers/lookup";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function Clientes({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const sessao = await pageSession();
  if (!sessao) redirect("/entrar");

  const { q } = await searchParams;
  const projecao = await projectionStatus(sessao.context.tenantId);

  return (
    <div className="pagina">
      <header className="pagina-topo">
        <h1>Clientes sincronizados com o FISCALE</h1>
        <p className="pagina-sub">
          Última sincronização com o FISCALE:{" "}
          <strong>{formatSyncedAt(projecao.lastSyncAt)}</strong>
        </p>
      </header>

      <CustomerBrowser termoInicial={q ?? ""} />
    </div>
  );
}
