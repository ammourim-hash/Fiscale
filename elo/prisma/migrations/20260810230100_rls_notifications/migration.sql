-- =====================================================================
--  Elo — RLS das notificacoes
--
--  Duas tabelas novas com tenant_id: notification_preferences e
--  push_subscriptions. Tratamento de sempre — ENABLE + FORCE + USING +
--  WITH CHECK, e o GRANT so depois de a politica existir.
--
--  Aqui o isolamento tem uma consequencia direta e testavel: uma
--  inscricao de push do tenant A nao existe para o tenant B. Como o envio
--  le a linha para chegar ao endpoint, o escritorio B nao consegue nem
--  descobrir que aquele aparelho existe — muito menos entregar
--  notificacao nele.
-- =====================================================================

SELECT elo_apply_rls();
