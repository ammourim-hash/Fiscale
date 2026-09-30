# ══════════════════════════════════════════════════════════════════════
#  ELO — imagem de producao
# ══════════════════════════════════════════════════════════════════════
#  Tres estagios, e o unico que vira imagem e o ultimo. Nem o codigo-fonte
#  nem as ferramentas de build chegam ao servidor.
#
#  O QUE ESTA IMAGEM NAO FAZ, DE PROPOSITO
#
#  Nao roda migration. `prisma migrate deploy` e operacao separada, feita
#  com o papel elo_owner e com backup antes. No ENTRYPOINT ela rodaria a
#  cada reinicio — e dois conteineres subindo juntos migrariam ao mesmo
#  tempo o mesmo banco.
#
#  Nao carrega `.env`. A configuracao entra por variavel de ambiente na
#  hora de subir. Segredo dentro de imagem sobrevive ao `rm`, porque a
#  camada anterior continua la.
#
#  A PORTA 3000 e interna. Quem publica 80/443 e o proxy reverso; este
#  conteiner nao deve publicar porta no host em producao.
# ══════════════════════════════════════════════════════════════════════

# Node 24 e a versao em uso no desenvolvimento; `engines` pede >=20.9.0.
# Alpine porque o Prisma 7 deste projeto usa driver adapter (@prisma/adapter-pg)
# e nao baixa engine nativo — entao nao ha a dor de musl de sempre.
ARG NODE_IMAGE=node:24-alpine

# ─── 1. dependencias ──────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS deps
WORKDIR /app

# So os manifestos: assim esta camada so e refeita quando uma dependencia
# muda, e nao a cada linha de codigo editada.
COPY package.json package-lock.json ./
# `npm ci` e nao `npm install`: instala EXATAMENTE o lockfile.
RUN npm ci --no-audit --no-fund

# ─── 2. build ─────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Valores de FACHADA, validos so dentro deste estagio.
#
# `src/env.ts` valida o ambiente com zod no momento do import, e
# `prisma.config.ts` le DATABASE_MIGRATION_URL. Sem isto o build para por
# falta de configuracao — mesmo sem tocar em banco nenhum.
#
# Nao sao segredos e nao alcancam a imagem final: este estagio inteiro
# fica para tras. O banco real so e conhecido em tempo de execucao.
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    DATABASE_URL="postgresql://build:build@127.0.0.1:5432/build?schema=public" \
    DATABASE_MIGRATION_URL="postgresql://build:build@127.0.0.1:5432/build?schema=public"

# O cliente do Prisma e GERADO, nao versionado (`src/generated/` esta no
# .gitignore). Tem de nascer aqui, antes do build que o importa.
RUN npx prisma generate

RUN npm run build

# ─── 3. runtime ───────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# `0.0.0.0` dentro do conteiner NAO e exposicao: sem `ports:` publicando no
# host, esse endereco so existe na rede interna do Docker, que e onde o
# proxy reverso fala com ele. Escutar so em 127.0.0.1 aqui deixaria o
# proxy sem conseguir alcancar a aplicacao.

# O usuario `node` (uid 1000) ja vem na imagem oficial. Root so para copiar.
COPY --from=builder --chown=node:node /app/public ./public
# O standalone traz servidor + node_modules rastreados; `static` e `public`
# ficam de fora porque nao sao importados por codigo.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

# O diretorio de anexos precisa EXISTIR e ser do `node` antes do volume.
#
# Medido, nao suposto: um volume nomeado montado sobre um caminho que nao
# existe na imagem nasce `root:root`, e o processo (uid 1000) leva
# "Permission denied" no primeiro upload — falha silenciosa que so
# apareceria com cliente mandando anexo. Quando o caminho existe, o Docker
# copia conteudo E dono para o volume vazio, e a gravacao funciona.
#
# Fica vazio de proposito: o `.dockerignore` barra `.storage`, entao nada
# do disco de desenvolvimento entra aqui. Quem povoa e o volume.
RUN mkdir -p /app/.storage && chown node:node /app/.storage

USER node

EXPOSE 3000

# Saude pela propria rota do produto: distingue "aplicacao viva" de
# "aplicacao viva e banco respondendo" (503 em degraded).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

# O standalone gera o seu proprio servidor. `next start` nao vale aqui: ele
# precisaria do Next completo em node_modules, que e justamente o que o
# standalone deixou de copiar.
CMD ["node", "server.js"]
