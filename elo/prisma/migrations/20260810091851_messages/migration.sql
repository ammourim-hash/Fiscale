-- CreateEnum
CREATE TYPE "message_direction" AS ENUM ('INBOUND', 'OUTBOUND', 'SYSTEM');

-- CreateEnum
CREATE TYPE "message_type" AS ENUM ('TEXT', 'IMAGE', 'FILE', 'AUDIO', 'VOICE');

-- CreateEnum
CREATE TYPE "message_delivery_status" AS ENUM ('QUEUED', 'SENT', 'DELIVERED', 'READ', 'FAILED');

-- AlterTable
ALTER TABLE "conversations" ADD COLUMN     "message_seq" INTEGER NOT NULL DEFAULT 0;

-- CreateTable
CREATE TABLE "messages" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "conversation_id" UUID NOT NULL,
    "sequence" INTEGER NOT NULL,
    "direction" "message_direction" NOT NULL,
    "type" "message_type" NOT NULL DEFAULT 'TEXT',
    "sender_membership_id" UUID,
    "sender_display_name" TEXT,
    "sender_department_name" TEXT,
    "external_sender_id" TEXT,
    "external_message_id" TEXT,
    "channel" TEXT NOT NULL DEFAULT 'INTERNAL',
    "content" TEXT NOT NULL,
    "client_message_id" TEXT,
    "delivery_status" "message_delivery_status" NOT NULL DEFAULT 'QUEUED',
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "edited_at" TIMESTAMPTZ(6),
    "deleted_at" TIMESTAMPTZ(6),

    CONSTRAINT "messages_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "message_views" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "message_id" UUID NOT NULL,
    "membership_id" UUID NOT NULL,
    "viewed_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "message_views_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "messages_tenant_id_conversation_id_sequence_idx" ON "messages"("tenant_id", "conversation_id", "sequence");

-- CreateIndex
CREATE INDEX "messages_tenant_id_conversation_id_direction_idx" ON "messages"("tenant_id", "conversation_id", "direction");

-- CreateIndex
CREATE UNIQUE INDEX "messages_conversation_id_sequence_key" ON "messages"("conversation_id", "sequence");

-- CreateIndex
CREATE UNIQUE INDEX "messages_idempotencia" ON "messages"("tenant_id", "sender_membership_id", "client_message_id");

-- CreateIndex
CREATE UNIQUE INDEX "messages_externo" ON "messages"("tenant_id", "external_message_id");

-- CreateIndex
CREATE INDEX "message_views_tenant_id_membership_id_idx" ON "message_views"("tenant_id", "membership_id");

-- CreateIndex
CREATE UNIQUE INDEX "message_views_message_id_membership_id_key" ON "message_views"("message_id", "membership_id");

-- AddForeignKey
ALTER TABLE "messages" ADD CONSTRAINT "messages_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "messages" ADD CONSTRAINT "messages_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "conversations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "messages" ADD CONSTRAINT "messages_sender_membership_id_fkey" FOREIGN KEY ("sender_membership_id") REFERENCES "memberships"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "message_views" ADD CONSTRAINT "message_views_message_id_fkey" FOREIGN KEY ("message_id") REFERENCES "messages"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "message_views" ADD CONSTRAINT "message_views_membership_id_fkey" FOREIGN KEY ("membership_id") REFERENCES "memberships"("id") ON DELETE CASCADE ON UPDATE CASCADE;
