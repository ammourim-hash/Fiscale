import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Producao em conteiner: o Next monta em `.next/standalone` uma pasta com
  // o servidor e SO as dependencias que o rastreamento provou necessarias,
  // em vez de arrastar os ~800 MB de `node_modules` — a maior parte deles
  // ferramenta de build, que nao roda em producao.
  //
  // O que o standalone NAO copia, e por isso o Dockerfile copia a mao:
  // `.next/static` e `public/`. O servidor os serve, mas o rastreamento so
  // enxerga o que e IMPORTADO por codigo.
  //
  // Nao muda nada em desenvolvimento: `next dev` ignora esta opcao.
  output: "standalone",
  // O cliente Prisma e o driver pg carregam binario nativo: precisam ficar
  // fora do bundle do servidor.
  serverExternalPackages: ["@prisma/client", "@prisma/adapter-pg", "pg"],
  // O erro nunca deve contar onde o codigo mora.
  productionBrowserSourceMaps: false,
  poweredByHeader: false,
  // Em desenvolvimento a pagina abre em 127.0.0.1 (e nao em localhost) porque
  // o cookie de sessao vem do Fiscale, que usa 127.0.0.1 nos dois lados. O Next
  // 16 recusa servir os proprios chunks quando a origem nao esta declarada aqui,
  // e a tela fica em "Carregando..." para sempre: HTML servido, JavaScript negado.
  allowedDevOrigins: ["127.0.0.1"],
  experimental: {
    // Liga `forbidden()` — o unico jeito de uma pagina do App Router
    // responder 403 de verdade. Sem isto a alternativa seria `notFound()`,
    // que diz 404: mentir sobre a existencia da tela para quem TEM conta
    // no escritorio e so nao administra. Quem administra precisa saber que
    // a pagina existe e que o acesso foi negado; 404 mandaria essa pessoa
    // procurar um defeito que nao existe.
    //
    // O Next ainda marca como experimental. Liga tambem `unauthorized()`,
    // e nada alem disso. Se um dia incomodar, trocar as chamadas por
    // `notFound()` devolve o comportamento antigo.
    authInterrupts: true,
  },
};

export default nextConfig;
