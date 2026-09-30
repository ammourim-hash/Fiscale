-- =====================================================================
--  Elo — RLS do modelo de atendimento
--
--  Sete tabelas novas, todas com tenant_id: conversations,
--  conversation_views, conversation_events, tags, conversation_tags,
--  internal_notes. Nenhuma excecao, nenhuma tabela global desta vez.
--
--  A funcao criada no MVP 1.0 liga tudo de uma vez e so concede acesso ao
--  elo_app junto com a politica. Atendimento do tenant A nao pode ser
--  lido, assumido, transferido, etiquetado nem anotado pelo tenant B — e
--  quem garante isso e o banco, nao o `where` da consulta.
-- =====================================================================

SELECT elo_apply_rls();
