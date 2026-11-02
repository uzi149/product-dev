"use client";

import { useParams } from "next/navigation";
import { PageHeader } from "@/components/page-header";
import { TriggerBuilder } from "@/components/triggers/trigger-builder";

export default function TriggerPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <>
      <PageHeader eyebrow="Custom triggers" title="Trigger" />
      <TriggerBuilder triggerId={id} />
    </>
  );
}
