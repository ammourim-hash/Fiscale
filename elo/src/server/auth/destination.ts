/**
 * O destino pretendido, guardado enquanto a pessoa passa pelo login.
 *
 * ---------------------------------------------------------------------
 *  O caso que este arquivo existe para resolver
 * ---------------------------------------------------------------------
 *  Sexta-feira, 18h. Chega uma notificação no Windows: "Empresa ABC entrou
 *  em contato". A pessoa clica. A sessão do Elo expirou às 12h.
 *
 *  Sem isto, ela cai na tela de entrada, vai ao Fiscale, faz login, volta
 *  para a home do Elo — e o atendimento que ela queria abrir sumiu. Ela vai
 *  ter que procurar na lista qual dos casos era, e a notificação virou uma
 *  interrupção sem serviço.
 *
 * ---------------------------------------------------------------------
 *  Por que um cookie, e não a URL
 * ---------------------------------------------------------------------
 *  A volta do Fiscale é um POST de outra origem para /api/auth/exchange.
 *  Nada do que estava na URL do Elo sobrevive a esse caminho, e fazer o
 *  destino atravessar o Fiscale exigiria mexer no FISCALE — que está fora
 *  de escopo, e por bom motivo.
 *
 *  O cookie é curto (15 min), `HttpOnly` e `SameSite=Lax`. `Lax` é o que
 *  permite que ele viaje na navegação de volta; `HttpOnly` mantém o valor
 *  fora do alcance de qualquer script.
 *
 * ---------------------------------------------------------------------
 *  O destino NÃO é autoridade
 * ---------------------------------------------------------------------
 *  O `conversationId` que vem de uma notificação não concede acesso a
 *  nada. Ele só decide para qual URL o navegador vai; ao chegar lá,
 *  sessão, tenant, RBAC e RLS continuam valendo, e um atendimento de outro
 *  escritório simplesmente não é encontrado.
 *
 *  E `caminhoSeguro` existe para que este cookie não vire um redirecionador
 *  aberto: só caminho interno, começando com uma barra só. `//evil.com` e
 *  `https://evil.com` são caminhos válidos para o navegador e viram outro
 *  site — é o defeito clássico de "voltar para onde eu estava".
 */

export const COOKIE_DESTINO = "elo_destino";

/** Quinze minutos. Dá para fazer login com calma e não fica de lembrança. */
export const VIDA_DESTINO_SEGUNDOS = 900;

/**
 * Só caminho interno. Recusa tudo o mais, em vez de "consertar".
 *
 * O `\` está na lista porque alguns navegadores o tratam como `/`, e
 * `/\evil.com` já foi um bypass real de validações escritas só para `/`.
 */
export function caminhoSeguro(valor: string | null | undefined): string | null {
  if (!valor) return null;
  if (valor.length > 512) return null;
  if (!valor.startsWith("/")) return null;
  if (valor.startsWith("//") || valor.startsWith("/\\")) return null;
  if (valor.includes("\\")) return null;
  // Controle e quebra de linha em cabeçalho `Location` é injeção de
  // resposta. Não há caminho legítimo com esses bytes.
  //
  // Escrito por codigo do caractere, e nao por classe de regex: um
  // intervalo de caracteres invisiveis dentro de uma expressao regular
  // e exatamente o tipo de coisa que sobrevive a uma edicao distraida
  // parecendo certa e sem funcionar mais.
  for (let i = 0; i < valor.length; i += 1) {
    const c = valor.charCodeAt(i);
    if (c <= 0x1f || c === 0x7f) return null;
  }
  return valor;
}
