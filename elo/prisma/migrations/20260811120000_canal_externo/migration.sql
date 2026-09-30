-- =====================================================================
--  Elo — o outro lado da conversa (MVP 1.8.1)
--
--  UMA coluna e UM indice. Nenhuma tabela nova, nenhum modelo paralelo.
--
--  O WhatsApp entra como mais um valor de `conversations.channel`, que ja
--  e texto livre desde o MVP 1.4 justamente para isto. O que faltava era
--  saber QUEM e o cliente do lado de fora — o `wa_id`.
--
--  Por que nao ha `Channel` nem `ChannelCredential` ainda: com UM numero
--  de teste e UM tenant, elas teriam uma linha copiada de variavel de
--  ambiente e nenhum leitor de verdade. "Tabela sem leitor e divida, nao
--  progresso" (S6). Elas entram quando houver varios numeros — ou seja,
--  no Embedded Signup da fase 1.8.4.
-- =====================================================================

ALTER TABLE "conversations" ADD COLUMN "external_contact_id" TEXT;

-- UM atendimento por pessoa, por canal.
--
-- Nao e conveniencia: e a trava contra a corrida real de tres mensagens
-- seguidas no WhatsApp criando tres atendimentos. Quem decide e o indice,
-- e nao um SELECT seguido de INSERT — entre os dois cabe a segunda
-- mensagem inteira. Mesma licao de S26 (assumir) e S36 (idempotencia).
--
-- NULL nao colide com NULL no Postgres: os canais internos (INTERNAL,
-- DEV), que nao tem `external_contact_id`, ficam de fora da trava.
CREATE UNIQUE INDEX "conversations_contato_externo"
    ON "conversations"("tenant_id", "channel", "external_contact_id");

-- Nenhuma tabela nova: o RLS de `conversations` ja esta ligado, forcado e
-- com politica desde 20260810081100_rls_service_model. Acrescentar coluna
-- nao mexe nisso, e o `rls-guard` continua conferindo.
