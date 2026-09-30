-- =====================================================================
--  Elo — permissao minima para a faxina de jti expirados
--
--  `DELETE FROM consumed_exchange_tokens WHERE expires_at < now()` falhava
--  com "permission denied". Nao era o RLS: o Postgres exige privilegio de
--  SELECT sobre as colunas citadas no WHERE, mesmo num DELETE — para
--  avaliar a condicao ele precisa ler o valor.
--
--  A resposta e o menor grant que resolve: leitura de UMA coluna,
--  `expires_at`. O `jti` continua ilegivel, entao a enumeracao de token
--  alheio — o motivo de a tabela nao ter SELECT — segue impossivel. O que
--  se pode observar sao carimbos de tempo sem nada que os identifique.
--
--  Grant de coluna nao torna `has_table_privilege(..., 'SELECT')`
--  verdadeiro, entao o teste rls-guard continua exigindo que a tabela nao
--  seja legivel — e ele agora confere tambem que a unica coluna liberada
--  e essa.
-- =====================================================================

GRANT SELECT (expires_at) ON public.consumed_exchange_tokens TO elo_app;
