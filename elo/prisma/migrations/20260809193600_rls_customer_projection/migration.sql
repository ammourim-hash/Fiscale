-- =====================================================================
--  Elo — RLS da projecao de clientes
--
--  As tabelas nasceram sem grant nenhum na migration anterior. Aqui elas
--  ganham acesso, e so ganham junto com a protecao — a regra do projeto.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Tudo que tem tenant_id: external_customer_references,
--    external_customer_phones e sync_runs.
-- ---------------------------------------------------------------------
SELECT elo_apply_rls();

-- ---------------------------------------------------------------------
-- 2. consumed_integration_nonces: global e SOMENTE-ESCRITA.
--
--    Mesmo desenho de consumed_exchange_tokens, pelos mesmos motivos:
--    a deteccao de repeticao sai de um unico INSERT ... ON CONFLICT DO
--    NOTHING, e sem SELECT ninguem enumera nonce alheio.
--
--    As duas permissoes minimas da faxina estao aqui desde o inicio,
--    porque ja aprendemos as duas na fase passada: DELETE com WHERE
--    exige leitura das colunas do filtro, e nesse caso as politicas de
--    SELECT tambem valem. Por isso o grant de coluna em `expires_at` e a
--    politica de leitura restrita a linhas ja expiradas.
-- ---------------------------------------------------------------------
ALTER TABLE public.consumed_integration_nonces ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.consumed_integration_nonces FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS nonce_insert       ON public.consumed_integration_nonces;
DROP POLICY IF EXISTS nonce_purge        ON public.consumed_integration_nonces;
DROP POLICY IF EXISTS nonce_read_expired ON public.consumed_integration_nonces;

CREATE POLICY nonce_insert ON public.consumed_integration_nonces FOR INSERT
  WITH CHECK (true);

CREATE POLICY nonce_read_expired ON public.consumed_integration_nonces FOR SELECT
  USING (expires_at < now());

CREATE POLICY nonce_purge ON public.consumed_integration_nonces FOR DELETE
  USING (expires_at < now());

GRANT INSERT, DELETE ON public.consumed_integration_nonces TO elo_app;
GRANT SELECT (expires_at) ON public.consumed_integration_nonces TO elo_app;
