"use client";

import { PageHeader } from "@/components/page-header";
import { TriggerBuilder } from "@/components/triggers/trigger-builder";

export default function NewTriggerPage() {
  return (
    <>
      <PageHeader
        eyebrow="Custom triggers"
        title="New trigger"
        description="Describe what should be reported in plain English, choose the cameras, then test it on stored footage before you enable it."
      />
      <TriggerBuilder triggerId={null} />
    </>
  );
}
