-- =====================================================================
--  Elo — midia: MessageAttachment
--
--  Escrita a mao de proposito. O `prisma migrate dev` queria DERRUBAR e
--  recriar o enum `message_type` para trocar FILE por DOCUMENT, o que
--  falharia com qualquer linha usando o valor antigo. `ALTER TYPE ...
--  RENAME VALUE` faz a mesma coisa preservando o dado — e continua
--  valendo no dia em que a tabela nao estiver mais vazia.
-- =====================================================================

-- FILE -> DOCUMENT. AUDIO e VOICE permanecem separados (ver o comentario
-- do enum no schema: o WhatsApp tambem os distingue, e deduzir depois a
-- partir do mimetype seria adivinhacao).
ALTER TYPE "message_type" RENAME VALUE 'FILE' TO 'DOCUMENT';

-- CreateEnum
CREATE TYPE "attachment_status" AS ENUM ('PENDING', 'ATTACHED');

-- CreateEnum
CREATE TYPE "scan_status" AS ENUM ('NOT_SCANNED', 'CLEAN', 'BLOCKED');

-- CreateTable
CREATE TABLE "message_attachments" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "message_id" UUID,
    "type" "message_type" NOT NULL,
    "storage_provider" TEXT NOT NULL,
    "storage_key" TEXT NOT NULL,
    "original_file_name" TEXT NOT NULL,
    "mime_type" TEXT NOT NULL,
    "size_bytes" INTEGER NOT NULL,
    "width" INTEGER,
    "height" INTEGER,
    "duration_ms" INTEGER,
    "sha256" TEXT,
    "status" "attachment_status" NOT NULL DEFAULT 'PENDING',
    "scan_status" "scan_status" NOT NULL DEFAULT 'NOT_SCANNED',
    "expires_at" TIMESTAMPTZ(6),
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "message_attachments_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "message_attachments_storage_key_key" ON "message_attachments"("storage_key");

-- CreateIndex
CREATE INDEX "message_attachments_tenant_id_message_id_idx" ON "message_attachments"("tenant_id", "message_id");

-- A faxina de orfaos varre por (status, expires_at); sem este indice ela
-- leria a tabela inteira toda vez.
CREATE INDEX "message_attachments_status_expires_at_idx" ON "message_attachments"("status", "expires_at");

-- AddForeignKey
ALTER TABLE "message_attachments" ADD CONSTRAINT "message_attachments_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "message_attachments" ADD CONSTRAINT "message_attachments_message_id_fkey" FOREIGN KEY ("message_id") REFERENCES "messages"("id") ON DELETE CASCADE ON UPDATE CASCADE;
