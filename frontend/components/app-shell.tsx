"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Camera,
  LayoutDashboard,
  Menu,
  Search,
  Settings,
  Video,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  {
    group: "Workspace",
    items: [
      { href: "/", label: "Overview", title: "Overview", icon: LayoutDashboard },
      { href: "/live", label: "Live cameras", title: "Live cameras", icon: Video },
      { href: "/events", label: "Events", title: "Reported events", icon: Activity },
      { href: "/investigate", label: "Investigate", title: "Investigate", icon: Search },
      { href: "/triggers", label: "Triggers", title: "Custom triggers", icon: Zap },
    ],
  },
  {
    group: "System",
    items: [
      { href: "/cameras", label: "Cameras", title: "Cameras", icon: Camera },
      { href: "/settings", label: "Settings", title: "Settings", icon: Settings },
    ],
  },
];

const ALL_ITEMS = NAV.flatMap((g) => g.items);

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });

  const unreachable = health.isError;
  const degraded = health.data?.status === "degraded";
  const healthLabel = health.isPending
    ? "Checking…"
    : unreachable
      ? "API unreachable"
      : degraded
        ? "Degraded"
        : "Operational";
  const healthy = !unreachable && !degraded && !health.isPending;
  const current = ALL_ITEMS.find((i) => isActive(pathname, i.href));

  return (
    <div className="grid min-h-screen lg:grid-cols-[228px_minmax(0,1fr)]">
      <aside
        className={cn(
          "sticky top-0 z-30 hidden h-screen flex-col border-r bg-sidebar px-4 py-6 lg:flex",
          menuOpen && "fixed inset-y-0 left-0 flex w-[228px]",
        )}
      >
        <div className="flex items-center gap-3 px-2.5 pb-7">
          <div className="grid size-[34px] place-items-center rounded-[10px] border border-green/45 bg-green/10 text-xl text-green">
            ◉
          </div>
          <div>
            <b className="font-heading tracking-tight">Sentinel</b>
            <small className="block font-mono text-[9px] uppercase leading-snug tracking-[0.16em] text-muted-foreground">
              Visual intelligence
            </small>
          </div>
        </div>

        {NAV.map((section) => (
          <div key={section.group} className="mb-5">
            <div className="mx-2.5 my-2 font-mono text-[10px] uppercase tracking-[0.16em] text-[#566562]">
              {section.group}
            </div>
            <nav className="grid gap-1">
              {section.items.map(({ href, label, icon: Icon }) => (
                <Link
                  key={href}
                  href={href}
                  onClick={() => setMenuOpen(false)}
                  className={cn(
                    "flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-[13px] text-sidebar-foreground transition-colors hover:bg-white/[0.035] hover:text-foreground",
                    isActive(pathname, href) && "bg-accent text-green hover:bg-accent hover:text-green",
                  )}
                >
                  <Icon className="size-4" />
                  {label}
                </Link>
              ))}
            </nav>
          </div>
        ))}

        <div className="mt-auto rounded-[14px] border bg-white/[0.015] p-3.5 text-[11px] text-muted-foreground">
          Site health
          <span
            className={cn(
              "float-right font-mono text-[10px]",
              healthy ? "text-[#77d879]" : unreachable ? "text-red" : "text-orange",
            )}
          >
            {healthLabel}
          </span>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-20 flex h-[72px] items-center gap-4 border-b bg-background/85 px-5 backdrop-blur-xl lg:px-7">
          <button
            type="button"
            aria-label="Toggle menu"
            className="text-2xl lg:hidden"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <Menu className="size-5" />
          </button>
          <div className="font-heading font-semibold tracking-tight">
            {current?.title ?? "Sentinel"}
            <span className="mt-0.5 block font-sans text-[11px] font-normal text-muted-foreground">
              Local camera intelligence
            </span>
          </div>
          <div className="ml-auto flex items-center gap-2 rounded-full border px-2.5 py-1.5 font-mono text-[10px] text-[#96a8a4]">
            <i
              className={cn(
                "size-1.5 rounded-full",
                healthy ? "bg-green shadow-[0_0_10px_var(--green)]" : unreachable ? "bg-red" : "bg-orange",
              )}
            />
            {cameras.data
              ? `${cameras.data.length} camera${cameras.data.length === 1 ? "" : "s"} configured`
              : healthLabel}
          </div>
        </header>

        {unreachable && (
          <div
            role="alert"
            className="border-b border-red/30 bg-red/10 px-7 py-2.5 text-xs text-[#ff8b86]"
          >
            The API is not reachable. Start it with{" "}
            <code className="font-mono">uvicorn services.api.main:app</code> and check that Docker is
            running.
          </div>
        )}

        <main className="page-enter px-5 pb-12 pt-6 lg:px-7">{children}</main>
      </div>
    </div>
  );
}
