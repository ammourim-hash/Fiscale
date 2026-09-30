/**
 * Admin > Equipe.
 *
 * A porta é dupla, e as duas fecham sozinhas:
 *
 *   - aqui, `users.manage` decide se a página abre. Quem não administra a
 *     equipe leva **403**, e não 404: dizer "não existe" a alguém que TEM
 *     conta neste escritório o manda procurar um defeito que não existe.
 *     Negar o acesso é a informação certa;
 *   - nas rotas de `/api/team`, `requirePermission` decide cada operação.
 *
 * A segunda é a que vale. Esta primeira só evita que alguém veja uma tela
 * que vai falhar em tudo que tentar — e é por isso que ela cobra
 * `users.manage`, e não `users.read`: quem só lê não teria o que fazer
 * aqui além de receber 403 em cada botão.
 *
 * O `GET /api/team` continua em `users.read`, de propósito: a lista é
 * leitura, e um dia pode aparecer noutra tela.
 */
import { forbidden, redirect } from "next/navigation";

import { Equipe } from "@/components/equipe";
import { pageSession } from "@/server/auth/page-session";

export const dynamic = "force-dynamic";

export default async function AdminEquipe() {
  const sessao = await pageSession();
  if (!sessao) redirect("/entrar");
  if (!sessao.permissions.includes("users.manage")) forbidden();

  return (
    <div className="pagina">
      <header className="pagina-topo">
        <h1>Equipe</h1>
        <p className="pagina-sub">
          As pessoas deste escritório, os papéis, as áreas e quem está de fato disponível para
          receber atendimento.
        </p>
      </header>

      <Equipe />
    </div>
  );
}
