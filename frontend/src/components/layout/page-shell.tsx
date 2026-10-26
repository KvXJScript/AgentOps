"use client";

import { useEffect, useState } from "react";
import { MenuIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Brand } from "./brand";
import { PrimaryNav } from "./primary-nav";
import { StatusIndicator, StubModelBadge } from "./status-indicator";
import { ThemeToggle } from "./theme-toggle";
import { isStubModel, useReady } from "@/hooks/use-ready";

/** On narrow screens a master/detail page swaps panes; start the new pane at the top. */
export function scrollPageToTop() {
  document.querySelector<HTMLElement>("[data-page-scroll]")?.scrollTo({ top: 0 });
}

/**
 * App frame for non-chat pages (Documents, …): the same sidebar chrome as chat
 * (brand, primary nav, status, theme) without the conversation list.
 */
export function PageShell({
  title,
  actions,
  children,
}: {
  title: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const ready = useReady();
  const stub = ready.kind === "ok" && isStubModel(ready.data.llm?.model, ready.data.llm?.provider);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMobileNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);

  const sidebar = (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex h-14 shrink-0 items-center px-4">
        <Brand />
      </div>
      <PrimaryNav onNavigate={() => setMobileNavOpen(false)} />
      <div className="flex-1" />
      <div className="flex shrink-0 items-center gap-1 border-t border-sidebar-border px-2 py-2">
        <StatusIndicator state={ready} onRefresh={ready.refresh} />
        <ThemeToggle />
      </div>
    </div>
  );

  return (
    <div className="flex h-dvh w-full">
      <aside className="hidden w-[272px] shrink-0 border-r border-sidebar-border md:block" aria-label="Sidebar">
        {sidebar}
      </aside>

      {mobileNavOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/40 backdrop-blur-[1px]"
            aria-label="Close navigation"
            onClick={() => setMobileNavOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 w-[85%] max-w-[300px] border-r shadow-xl" aria-label="Sidebar">
            {sidebar}
            <Button
              variant="ghost"
              size="icon-sm"
              className="absolute top-3 right-3"
              aria-label="Close navigation"
              onClick={() => setMobileNavOpen(false)}
            >
              <XIcon />
            </Button>
          </aside>
        </div>
      )}

      <main className="relative flex min-w-0 flex-1 flex-col bg-background">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3 md:px-5">
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            aria-label="Open navigation"
            onClick={() => setMobileNavOpen(true)}
          >
            <MenuIcon />
          </Button>
          <h1 className="min-w-0 truncate text-sm font-medium">{title}</h1>
          <div className="ml-auto flex min-w-0 items-center gap-2">
            {stub && <StubModelBadge />}
            {actions}
          </div>
        </header>
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto" data-page-scroll>
          {children}
        </div>
      </main>
    </div>
  );
}
