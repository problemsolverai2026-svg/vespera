import { Link, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { vespera, type StatusResponse } from "@/lib/vespera";

const nav = [
  { to: "/", label: "Chat" },
  { to: "/memory", label: "Memory" },
  { to: "/api-keys", label: "API Keys" },
  { to: "/models", label: "Models" },
  { to: "/resources", label: "Resources" },
  { to: "/settings", label: "Settings" },
] as const;

function StatusBar() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [online, setOnline] = useState<boolean>(true);

  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      try {
        const s = await vespera.status();
        if (mounted) {
          setStatus(s);
          setOnline(true);
        }
      } catch {
        if (mounted) setOnline(false);
      }
    };
    tick();
    const id = setInterval(tick, 4000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, []);

  const layers: Array<[string, keyof StatusResponse]> = [
    ["Working", "working"],
    ["Recent", "recent"],
    ["Validated", "validated"],
    ["Core", "core"],
  ];

  return (
    <footer className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-card/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-2 text-xs">
        <div className="flex items-center gap-2 text-muted-foreground">
          <span
            className={`h-2 w-2 rounded-full ${online ? "bg-[var(--local)]" : "bg-destructive"}`}
          />
          <span>{online ? "Connected" : "Offline"} · localhost:5055</span>
        </div>
        <div className="flex items-center gap-4 font-mono text-muted-foreground">
          {layers.map(([label, key]) => (
              <span key={label}>
              <span className="text-muted-foreground/70">{label}</span>{" "}
              <span className="text-foreground">{(status?.[key] as number | undefined) ?? "—"}</span>
            </span>
          ))}
        </div>
      </div>
    </footer>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const path = useRouterState({ select: (s) => s.location.pathname });
  return (
    <div className="min-h-screen pb-12">
      <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <Link to="/" className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-primary" />
            <span className="font-mono text-sm tracking-tight">vespera</span>
          </Link>
          <nav className="flex items-center gap-1">
            {nav.map((n) => {
              const active = n.to === "/" ? path === "/" : path.startsWith(n.to);
              return (
                <Link
                  key={n.to}
                  to={n.to}
                  className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                    active
                      ? "bg-secondary text-foreground"
                      : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
                  }`}
                >
                  {n.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      <StatusBar />
    </div>
  );
}
