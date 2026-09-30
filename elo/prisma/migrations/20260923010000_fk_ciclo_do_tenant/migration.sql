-- =====================================================================
--  Elo — as tres chaves estrangeiras que faltavam para o tenant
--
--  Tres `ALTER TABLE`. Nenhuma tabela, nenhuma coluna, nenhum indice,
--  nenhum dado, nenhuma politica de RLS.
--
--  POR QUE: `memberships`, `departments`, `contacts`, `conversations`,
--  `messages` e `message_attachments` ja apontavam para `tenants` com
--  ON DELETE CASCADE. Estas tres tinham a coluna `tenant_id` e nenhuma
--  chave — o modelo Prisma nunca declarou a relacao, e sem relacao o
--  Prisma nao emite a FK. Resultado: apagar um tenant deixava cliente,
--  sincronizacao e etiqueta para tras, invisiveis (o RLS filtra por um
--  tenant que ja nao existe, entao so o superusuario os enxergava).
--
--  Em 23/09/2026 foram removidos 7.424 residuos assim do banco de
--  trabalho. Isto aqui e o que impede que voltem.
--
--  ON UPDATE CASCADE e o mesmo padrao das seis FKs existentes.
--
--  INDICES: nenhum e criado. As tres tabelas ja tem indice com
--  `tenant_id` na primeira coluna
--  (`audit_logs` nao entra aqui; ver abaixo), entao a cascata nao vira
--  varredura sequencial.
--
--  `external_customer_phones` NAO precisa de FK propria: ja cascateia de
--  `external_customer_references`. Com esta migration a corrente fica
--  inteira: tenants -> referencias -> telefones.
--  `conversation_tags` idem, ja cascateia de `tags` e de `conversations`.
--
--  AUDIT_LOGS FICA DE FORA, DE PROPOSITO. `auth/exchange.ts` grava
--  AUTH_EXCHANGE_FAILED com o tenant que veio no claim mesmo quando ele
--  nao existe (TENANT_NOT_FOUND) — e e essa a tentativa que mais
--  interessa guardar. Com FK, o INSERT seria recusado e a trilha
--  perderia exatamente o caso que justifica existir. A limpeza de
--  auditoria de tenant apagado sai por expurgo com politica de retencao,
--  nunca por cascata.
--
--  SEGURANCA DA APLICACAO: nao ha linha orfa em nenhuma das tres, entao
--  a validacao da constraint passa na hora. Se um dia houver, o ALTER
--  falha — e falhar e o comportamento certo: ninguem quer descobrir a
--  perda depois.
-- =====================================================================

ALTER TABLE "external_customer_references"
    ADD CONSTRAINT "external_customer_references_tenant_id_fkey"
    FOREIGN KEY ("tenant_id") REFERENCES "tenants"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "sync_runs"
    ADD CONSTRAINT "sync_runs_tenant_id_fkey"
    FOREIGN KEY ("tenant_id") REFERENCES "tenants"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "tags"
    ADD CONSTRAINT "tags_tenant_id_fkey"
    FOREIGN KEY ("tenant_id") REFERENCES "tenants"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;

-- Sem `SELECT elo_apply_rls()`: nenhuma tabela nova e nenhuma coluna
-- nova. As politicas e os grants das tres continuam os mesmos, e o teste
-- rls-guard segue valendo como fiscal.
