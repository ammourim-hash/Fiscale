/**
 * Redirecionar sem trocar de host.
 *
 * `new URL("/", req.url)` parece certo e não é: o `req.url` do Next carrega
 * o host de configuração, não necessariamente aquele que o navegador
 * digitou. Na prática isso aconteceu — a pessoa entrava por
 * `127.0.0.1:3000`, o cookie era gravado nesse host, e o redirecionamento
 * a mandava para `localhost:3000`. Para o navegador são dois sites
 * diferentes: o cookie não acompanha, e ela volta para a tela de entrada
 * como se a autenticação tivesse falhado.
 *
 * Em produção o mesmo problema aparece atrás do proxy reverso, onde o Next
 * enxerga `localhost` e o mundo enxerga o domínio.
 *
 * Então o destino é montado a partir do que o CLIENTE usou: `Host`, ou o
 * `X-Forwarded-Host` que o proxy repassa.
 */
import type { NextRequest } from "next/server";

export function sameHostUrl(req: NextRequest, caminho: string): URL {
  const encaminhado = req.headers.get("x-forwarded-host");
  const host = encaminhado ?? req.headers.get("host");
  if (!host) return new URL(caminho, req.url);

  // Cabeçalho vem de fora: um valor com barra ou espaço viraria redirect
  // para outro site. Só aceita host com forma de host.
  if (!/^[a-z0-9.\-[\]:]+$/i.test(host)) return new URL(caminho, req.url);

  const proto =
    req.headers.get("x-forwarded-proto") ?? new URL(req.url).protocol.replace(":", "");

  return new URL(caminho, `${proto}://${host}`);
}
