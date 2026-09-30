-- =====================================================================
--  Elo — a faxina precisa de politica de SELECT, nao so de DELETE
--
--  Mesmo com GRANT DELETE, com o grant de coluna em `expires_at` e com a
--  politica `FOR DELETE USING (expires_at < now())`, o comando apagava
--  zero linhas.
--
--  O motivo esta no comportamento do RLS: um DELETE (ou UPDATE) cujo
--  WHERE referencia colunas da tabela precisa LER as linhas para
--  encontra-las, e nessa leitura as politicas de SELECT tambem valem.
--  Como nao havia nenhuma politica de SELECT, nenhuma linha era visivel,
--  e o DELETE nao encontrava o que apagar. Falhou fechado — o
--  comportamento correto, so que no lugar errado.
--
--  A politica abaixo abre a leitura APENAS das linhas ja expiradas, que
--  sao exatamente as que a faxina apaga. Combinada com o grant de coluna
--  da migration anterior, o que o elo_app pode observar continua sendo
--  so `expires_at` de token vencido: nunca o `jti`, nunca o de um token
--  ainda valido. A tabela segue sem servir para enumerar nada.
-- =====================================================================

DROP POLICY IF EXISTS consumed_read_expired ON public.consumed_exchange_tokens;

CREATE POLICY consumed_read_expired ON public.consumed_exchange_tokens FOR SELECT
  USING (expires_at < now());
