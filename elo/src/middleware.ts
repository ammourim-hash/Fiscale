/**
 * Um middleware, com uma responsabilidade só: contar à página QUAL caminho
 * foi pedido.
 *
 * ---------------------------------------------------------------------
 *  Por que ele existe
 * ---------------------------------------------------------------------
 *  Um Server Component não sabe a própria URL. Isso é de propósito no Next
 *  — a página é uma função do caminho, não uma leitora dele —, e funciona
 *  bem até aparecer o caso deste MVP: alguém clica numa notificação às 22h,
 *  a sessão do Elo já expirou, e o layout precisa guardar PARA ONDE a
 *  pessoa queria ir antes de mandá-la ao login.
 *
 *  Sem isso, ela faz login e cai na home, e o atendimento que a notificação
 *  prometia some. A notificação vira interrupção sem serviço.
 *
 * ---------------------------------------------------------------------
 *  O que ele NÃO faz — e isto é o ponto
 * ---------------------------------------------------------------------
 *  Não autentica, não autoriza, não lê banco, não decide nada. Autenticação
 *  continua sendo `pageSession()` e `requireAuth()`, no servidor, com a
 *  sessão validada contra o banco e o RLS aberto no tenant certo.
 *
 *  Middleware roda antes de tudo e em runtime restrito; colocar
 *  autorização aqui seria criar uma segunda porta de decisão, mais fraca
 *  que a primeira e fácil de esquecer quando uma rota nova aparecer. A
 *  regra do projeto continua: quem protege o dado é a camada que toca o
 *  dado.
 *
 *  O cabeçalho abaixo é informação, não autoridade: nada é liberado por
 *  causa dele, e o valor ainda passa por `caminhoSeguro` antes de virar
 *  redirecionamento.
 */
import { NextResponse, type NextRequest } from "next/server";

/** O caminho pedido, para o layout poder guardá-lo antes do login. */
export const CABECALHO_CAMINHO = "x-elo-caminho";

export function middleware(req: NextRequest) {
  const cabecalhos = new Headers(req.headers);
  cabecalhos.set(CABECALHO_CAMINHO, req.nextUrl.pathname + req.nextUrl.search);
  return NextResponse.next({ request: { headers: cabecalhos } });
}

export const config = {
  /**
   * Só páginas.
   *
   * `/api` fica de fora porque rota de API nunca redireciona para login —
   * ela responde 401, e passar por aqui só acrescentaria trabalho a cada
   * requisição do realtime. Estáticos e o `sw.js` também: o service worker
   * precisa ser servido exatamente como está no disco.
   */
  matcher: ["/((?!api|_next/static|_next/image|icones|sw.js|manifest.webmanifest|favicon.ico).*)"],
};
