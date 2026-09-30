-- =====================================================================
--  Elo — notificacoes e Web Push (MVP 1.7)
--
--  Duas tabelas, as duas tenant-owned e as duas por MEMBERSHIP:
--
--    notification_preferences  o que ESTA pessoa quer receber, NESTE
--                              escritorio. Preferencia pessoal nao mora
--                              em configuracao de tenant: quem escuta o
--                              som e le a prévia e quem decide.
--
--    push_subscriptions        um DISPOSITIVO. Notebook do escritorio,
--                              computador de casa e celular sao tres
--                              linhas; revogar uma nao toca nas outras.
--
--  O RLS vem na migration seguinte, pela `elo_apply_rls()` de sempre.
-- =====================================================================

-- CreateTable
CREATE TABLE "notification_preferences" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "membership_id" UUID NOT NULL,
    "new_messages" BOOLEAN NOT NULL DEFAULT true,
    "new_conversations" BOOLEAN NOT NULL DEFAULT true,
    "assigned_to_me" BOOLEAN NOT NULL DEFAULT true,
    "transferred_to_me" BOOLEAN NOT NULL DEFAULT true,
    "system_notices" BOOLEAN NOT NULL DEFAULT true,
    "sound_enabled" BOOLEAN NOT NULL DEFAULT true,
    -- O unico padrao restritivo da tabela. A notificacao aparece na tela
    -- BLOQUEADA do Windows e do celular, e o escritorio trata de assunto
    -- de terceiro.
    "show_preview" BOOLEAN NOT NULL DEFAULT false,
    "quiet_hours_start" INTEGER,
    "quiet_hours_end" INTEGER,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "notification_preferences_pkey" PRIMARY KEY ("id")
);

-- Uma linha por pessoa. A UNIQUE e o que faz "salvar preferencia" poder
-- ser um upsert e nunca criar a segunda linha numa corrida.
CREATE UNIQUE INDEX "notification_preferences_membership_id_key"
    ON "notification_preferences"("membership_id");

CREATE INDEX "notification_preferences_tenant_id_idx"
    ON "notification_preferences"("tenant_id");

-- Horario silencioso e minuto do dia. O CHECK existe porque 1440 e
-- "amanha", e um valor fora da faixa silenciaria para sempre sem que
-- ninguem entendesse por que.
ALTER TABLE "notification_preferences"
  ADD CONSTRAINT "notification_preferences_quiet_start_range"
  CHECK ("quiet_hours_start" IS NULL
         OR ("quiet_hours_start" >= 0 AND "quiet_hours_start" <= 1439));
ALTER TABLE "notification_preferences"
  ADD CONSTRAINT "notification_preferences_quiet_end_range"
  CHECK ("quiet_hours_end" IS NULL
         OR ("quiet_hours_end" >= 0 AND "quiet_hours_end" <= 1439));

-- AddForeignKey
ALTER TABLE "notification_preferences"
  ADD CONSTRAINT "notification_preferences_membership_id_fkey"
  FOREIGN KEY ("membership_id") REFERENCES "memberships"("id")
  ON DELETE CASCADE ON UPDATE CASCADE;

-- CreateTable
CREATE TABLE "push_subscriptions" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "membership_id" UUID NOT NULL,
    -- Segredo de capacidade: quem tem o endpoint e as duas chaves entrega
    -- notificacao naquele aparelho. Nunca sai numa resposta, nunca entra
    -- em log, nunca vai para a auditoria.
    "endpoint" TEXT NOT NULL,
    "endpoint_hash" TEXT NOT NULL,
    "p256dh" TEXT NOT NULL,
    "auth" TEXT NOT NULL,
    "device_label" TEXT,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "last_used_at" TIMESTAMPTZ(6),
    "revoked_at" TIMESTAMPTZ(6),
    "revoked_reason" TEXT,
    "failure_count" INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "push_subscriptions_pkey" PRIMARY KEY ("id")
);

-- Um aparelho, uma inscricao por escritorio: reinscrever o mesmo
-- navegador ATUALIZA a linha em vez de criar a segunda.
CREATE UNIQUE INDEX "push_subscriptions_tenant_id_endpoint_hash_key"
    ON "push_subscriptions"("tenant_id", "endpoint_hash");

CREATE INDEX "push_subscriptions_tenant_id_membership_id_idx"
    ON "push_subscriptions"("tenant_id", "membership_id");

-- Usado pela tomada de posse do endpoint (ver a funcao mais abaixo).
CREATE INDEX "push_subscriptions_endpoint_hash_idx"
    ON "push_subscriptions"("endpoint_hash");

-- AddForeignKey
ALTER TABLE "push_subscriptions"
  ADD CONSTRAINT "push_subscriptions_membership_id_fkey"
  FOREIGN KEY ("membership_id") REFERENCES "memberships"("id")
  ON DELETE CASCADE ON UPDATE CASCADE;

-- ---------------------------------------------------------------------
--  O aparelho que troca de dono — e por que NAO ha funcao especial aqui
-- ---------------------------------------------------------------------
--  O PROBLEMA: o endpoint de push pertence ao NAVEGADOR, nao a pessoa. Um
--  computador compartilhado no escritorio, com um perfil do Chrome so,
--  produz o MESMO endpoint para quem quer que entre nele. Se a Aline e o
--  Carlos usarem aquela maquina, sem nada feito as duas inscricoes ficam
--  vivas — e o aviso "Empresa ABC entrou em contato" toca no aparelho
--  enquanto quem esta ali e o outro.
--
--  DENTRO DO MESMO TENANT isso se resolve na propria tabela: a UNIQUE de
--  (tenant_id, endpoint_hash) faz a reinscricao ser um UPDATE, e o
--  `membership_id` e reescrito para quem esta entrando agora. Uma linha,
--  um dono.
--
--  ENTRE TENANTS nao ha o que fazer daqui, e a tentativa de fazer foi
--  desfeita de proposito. A primeira versao desta migration criava uma
--  funcao SECURITY DEFINER para revogar a inscricao do outro escritorio.
--  Ela NAO FUNCIONA, e a razao e a decisao S2 do MVP 1.0: `FORCE ROW
--  LEVEL SECURITY` vale tambem para o DONO das tabelas, entao nem o
--  `elo_owner` atravessa. Faze-la funcionar exigiria um papel com
--  BYPASSRLS — a porta que o MVP 1.0 fechou com cuidado, aberta para
--  sempre por causa de um caso de borda.
--
--  A SAIDA E MELHOR, e mora no navegador: ao ativar as notificacoes, se
--  ja existe uma inscricao neste navegador que NAO pertence a pessoa que
--  esta entrando, o cliente chama `unsubscribe()` — o que mata o endpoint
--  NO PROVEDOR. A partir dai qualquer linha antiga apontando para ele, em
--  qualquer escritorio, recebe 410 na primeira tentativa e e revogada
--  sozinha pela rotina de entrega.
--
--  Ou seja: o problema e resolvido na unica camada que enxerga os dois
--  lados, sem que o banco precise enxergar alem do proprio tenant. Ver
--  `ativarNotificacoes` em src/components/notificacoes.tsx.
-- ---------------------------------------------------------------------
