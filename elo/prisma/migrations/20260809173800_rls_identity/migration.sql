-- =====================================================================
--  Elo — RLS das tabelas de identidade, sessao, RBAC e departamentos
--
--  As tabelas criadas na migration anterior nasceram SEM grant nenhum:
--  o elo_app nao consegue nem lê-las. Este arquivo abre o acesso, e so
--  abre junto com a protecao. Nenhum GRANT sai daqui sem RLS ligado.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Tudo que tem tenant_id: memberships, departments,
--    department_memberships, sessions, audit_logs.
--    Mesma politica de sempre — tenant_id = app_tenant_id(), USING e
--    WITH CHECK — aplicada pela funcao criada no MVP 1.0.
-- ---------------------------------------------------------------------
SELECT elo_apply_rls();

-- ---------------------------------------------------------------------
-- 2. identities e identity_providers: globais, isoladas POR ASSOCIACAO.
--
--    Nao tem tenant_id — a pessoa e uma so, mesmo atendendo dois
--    escritorios. O que as isola e o vinculo: uma identidade e visivel
--    apenas para o tenant onde ela tem membership. Sem vinculo, ela nao
--    existe para aquele tenant, nem por id conhecido.
--
--    O INSERT e permitido as cegas (WITH CHECK true). Nao e brecha:
--    inserir nao le. A linha recem-criada continua invisivel ate que um
--    membership do tenant corrente aponte para ela. Por isso a criacao
--    de identidade usa SQL sem RETURNING — ver provisionMembership().
-- ---------------------------------------------------------------------
ALTER TABLE public.identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.identities FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS identity_read   ON public.identities;
DROP POLICY IF EXISTS identity_insert ON public.identities;
DROP POLICY IF EXISTS identity_write  ON public.identities;

CREATE POLICY identity_read ON public.identities FOR SELECT
  USING (EXISTS (
    SELECT 1 FROM public.memberships m
     WHERE m.identity_id = identities.id
       AND m.tenant_id   = app_tenant_id()));

CREATE POLICY identity_insert ON public.identities FOR INSERT
  WITH CHECK (true);

CREATE POLICY identity_write ON public.identities FOR UPDATE
  USING (EXISTS (
    SELECT 1 FROM public.memberships m
     WHERE m.identity_id = identities.id
       AND m.tenant_id   = app_tenant_id()))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.memberships m
     WHERE m.identity_id = identities.id
       AND m.tenant_id   = app_tenant_id()));

-- Sem politica de DELETE: apagar pessoa e ato administrativo, nao de
-- runtime. O elo_app tambem nao recebe o privilegio abaixo.
GRANT SELECT, INSERT, UPDATE ON public.identities TO elo_app;

ALTER TABLE public.identity_providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.identity_providers FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS identity_provider_read   ON public.identity_providers;
DROP POLICY IF EXISTS identity_provider_insert ON public.identity_providers;

CREATE POLICY identity_provider_read ON public.identity_providers FOR SELECT
  USING (EXISTS (
    SELECT 1 FROM public.memberships m
     WHERE m.identity_id = identity_providers.identity_id
       AND m.tenant_id   = app_tenant_id()));

CREATE POLICY identity_provider_insert ON public.identity_providers FOR INSERT
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.memberships m
     WHERE m.identity_id = identity_providers.identity_id
       AND m.tenant_id   = app_tenant_id()));

GRANT SELECT, INSERT ON public.identity_providers TO elo_app;

-- ---------------------------------------------------------------------
-- 3. consumed_exchange_tokens: global e SOMENTE-ESCRITA.
--
--    O elo_app nao tem SELECT. Nao precisa: a deteccao de replay e
--
--        INSERT ... ON CONFLICT (jti) DO NOTHING
--
--    e a resposta esta na contagem de linhas afetadas — 1 e primeiro uso,
--    0 e replay. Um unico comando, decidido pela UNIQUE dentro do proprio
--    banco. Nao existe o intervalo entre "consultar" e "gravar" onde duas
--    requisicoes simultaneas passariam as duas.
--
--    Sem SELECT tambem nao ha como enumerar jti alheio. Note que nao ha
--    RETURNING em lugar nenhum: RETURNING exigiria privilegio de leitura.
-- ---------------------------------------------------------------------
ALTER TABLE public.consumed_exchange_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.consumed_exchange_tokens FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS consumed_insert ON public.consumed_exchange_tokens;
DROP POLICY IF EXISTS consumed_purge  ON public.consumed_exchange_tokens;

CREATE POLICY consumed_insert ON public.consumed_exchange_tokens FOR INSERT
  WITH CHECK (true);

-- Faxina de expirados. Sem politica de SELECT, mesmo aqui.
CREATE POLICY consumed_purge ON public.consumed_exchange_tokens FOR DELETE
  USING (expires_at < now());

GRANT INSERT, DELETE ON public.consumed_exchange_tokens TO elo_app;
