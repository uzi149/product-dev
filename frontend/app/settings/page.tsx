"use client";

import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/page-header";
import { api } from "@/lib/api";

/** The main configuration values, read-only: they are changed in `.env` (step 5.5). */
export default function SettingsPage() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  return (
    <>
      <PageHeader
        eyebrow="System"
        title="Settings"
        description="The main configuration values, read-only. Change them in the .env file and restart the API and workers."
      />
      {settings.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load the settings: {settings.error.message}
        </p>
      )}
      {!settings.data && !settings.isError && <p className="text-sm text-muted-foreground">Loading…</p>}
      <div className="grid gap-3 lg:grid-cols-2">
        {settings.data?.map((section) => (
          <section key={section.title} aria-label={section.title} className="rounded-2xl border bg-card p-4">
            <h2 className="mb-2 text-sm font-semibold">{section.title}</h2>
            <dl className="grid gap-1.5">
              {section.items.map((item) => (
                <div key={item.label} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-3 text-sm">
                  <dt className="text-muted-foreground">{item.label}</dt>
                  <dd className="text-right">
                    {item.value}
                    <span className="ml-2 font-mono text-[10px] text-muted-foreground">{item.env}</span>
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </>
  );
}
