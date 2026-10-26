"use client";

import { RotateCw } from "lucide-react";
import { useEffect } from "react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";

/** Shown in place of a page that crashed while rendering, so the sidebar stays usable. */
export default function PageError({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <>
      <PageHeader
        eyebrow="Error"
        title="This page stopped working"
        description="Something on the page failed. Try again, or open another page from the sidebar."
      />
      <div role="alert" className="rounded-2xl border border-red/30 bg-red/10 p-4">
        <p className="font-mono text-xs text-[#ff8b86]">
          {error instanceof Error ? error.message : String(error)}
        </p>
        <Button className="mt-3" onClick={retry}>
          <RotateCw /> Try again
        </Button>
      </div>
    </>
  );
}
