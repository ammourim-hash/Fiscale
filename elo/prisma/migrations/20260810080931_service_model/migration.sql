-- CreateEnum
CREATE TYPE "conversation_status" AS ENUM ('NEW', 'IN_PROGRESS', 'WAITING_CUSTOMER', 'WAITING_OFFICE', 'RESOLVED');

-- CreateEnum
CREATE TYPE "conversation_event_type" AS ENUM ('CREATED', 'VIEWED', 'ASSIGNED', 'UNASSIGNED', 'TRANSFERRED', 'STATUS_CHANGED', 'TAG_ADDED', 'TAG_REMOVED', 'NOTE_CREATED', 'NOTE_EDITED', 'RESOLVED', 'REOPENED');

-- AlterTable
ALTER TABLE "memberships" ADD COLUMN     "available_for_assignment" BOOLEAN NOT NULL DEFAULT true,
ADD COLUMN     "visible_to_customers" BOOLEAN NOT NULL DEFAULT false;

-- CreateTable
CREATE TABLE "conversations" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "customer_reference_id" UUID,
    "channel" TEXT NOT NULL DEFAULT 'WHATSAPP',
    "status" "conversation_status" NOT NULL DEFAULT 'NEW',
    "assigned_membership_id" UUID,
    "assigned_department_id" UUID,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,
    "first_viewed_at" TIMESTAMPTZ(6),
    "first_assigned_at" TIMESTAMPTZ(6),
    "first_response_at" TIMESTAMPTZ(6),
    "resolved_at" TIMESTAMPTZ(6),
    "last_activity_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "version" INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "conversations_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "conversation_views" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "conversation_id" UUID NOT NULL,
    "membership_id" UUID NOT NULL,
    "first_viewed_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "last_viewed_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "view_count" INTEGER NOT NULL DEFAULT 1,

    CONSTRAINT "conversation_views_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "conversation_events" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "conversation_id" UUID NOT NULL,
    "type" "conversation_event_type" NOT NULL,
    "actor_membership_id" UUID,
    "from_membership_id" UUID,
    "to_membership_id" UUID,
    "from_department_id" UUID,
    "to_department_id" UUID,
    "from_status" "conversation_status",
    "to_status" "conversation_status",
    "metadata" JSONB,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "conversation_events_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "tags" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "slug" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "tone" TEXT NOT NULL DEFAULT 'neutro',
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "tags_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "conversation_tags" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "conversation_id" UUID NOT NULL,
    "tag_id" UUID NOT NULL,
    "added_by_membership_id" UUID,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "conversation_tags_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "internal_notes" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "conversation_id" UUID NOT NULL,
    "author_membership_id" UUID NOT NULL,
    "content" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "internal_notes_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "conversations_tenant_id_status_last_activity_at_idx" ON "conversations"("tenant_id", "status", "last_activity_at");

-- CreateIndex
CREATE INDEX "conversations_tenant_id_assigned_membership_id_last_activit_idx" ON "conversations"("tenant_id", "assigned_membership_id", "last_activity_at");

-- CreateIndex
CREATE INDEX "conversations_tenant_id_assigned_department_id_last_activit_idx" ON "conversations"("tenant_id", "assigned_department_id", "last_activity_at");

-- CreateIndex
CREATE INDEX "conversation_views_tenant_id_membership_id_idx" ON "conversation_views"("tenant_id", "membership_id");

-- CreateIndex
CREATE UNIQUE INDEX "conversation_views_conversation_id_membership_id_key" ON "conversation_views"("conversation_id", "membership_id");

-- CreateIndex
CREATE INDEX "conversation_events_tenant_id_conversation_id_created_at_idx" ON "conversation_events"("tenant_id", "conversation_id", "created_at");

-- CreateIndex
CREATE INDEX "tags_tenant_id_idx" ON "tags"("tenant_id");

-- CreateIndex
CREATE UNIQUE INDEX "tags_tenant_id_slug_key" ON "tags"("tenant_id", "slug");

-- CreateIndex
CREATE INDEX "conversation_tags_tenant_id_tag_id_idx" ON "conversation_tags"("tenant_id", "tag_id");

-- CreateIndex
CREATE UNIQUE INDEX "conversation_tags_conversation_id_tag_id_key" ON "conversation_tags"("conversation_id", "tag_id");

-- CreateIndex
CREATE INDEX "internal_notes_tenant_id_conversation_id_created_at_idx" ON "internal_notes"("tenant_id", "conversation_id", "created_at");

-- AddForeignKey
ALTER TABLE "conversations" ADD CONSTRAINT "conversations_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "tenants"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "conversations" ADD CONSTRAINT "conversations_customer_reference_id_fkey" FOREIGN KEY ("customer_reference_id") REFERENCES "external_customer_references"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "conversations" ADD CONSTRAINT "conversations_assigned_membership_id_fkey" FOREIGN KEY ("assigned_membership_id") REFERENCES "memberships"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "conversations" ADD CONSTRAINT "conversations_assigned_department_id_fkey" FOREIGN KEY ("assigned_department_id") REFERENCES "departments"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "conversation_views" ADD CONSTRAINT "conversation_views_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "conversations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "conversation_views" ADD CONSTRAINT "conversation_views_membership_id_fkey" FOREIGN KEY ("membership_id") REFERENCES "memberships"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "conversation_events" ADD CONSTRAINT "conversation_events_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "conversations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "conversation_tags" ADD CONSTRAINT "conversation_tags_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "conversations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "conversation_tags" ADD CONSTRAINT "conversation_tags_tag_id_fkey" FOREIGN KEY ("tag_id") REFERENCES "tags"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "internal_notes" ADD CONSTRAINT "internal_notes_conversation_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "conversations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "internal_notes" ADD CONSTRAINT "internal_notes_author_membership_id_fkey" FOREIGN KEY ("author_membership_id") REFERENCES "memberships"("id") ON DELETE CASCADE ON UPDATE CASCADE;
