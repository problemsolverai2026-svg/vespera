import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { vespera, type MemoryItem, type StatusResponse, type MemoryStats } from "@/lib/vespera";

export const Route = createFileRoute("/memory")({
  head: () => ({ meta: [{ title: "Memory · Vespera" }] }),
  component: MemoryPage,
});

const LAYERS = ["working", "recent", "validated", "core"] as const;
type Layer = (typeof LAYERS)[number];

function MemoryPage() {
  const [layer, setLayer] = useState<Layer>("working");
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [memStats, setMemStats] = useState<MemoryStats>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    vespera.status().then((s) => setMemStats(s.memory ?? {})).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    vespera
      .memories(layer)
      .then((r) => setItems(Array.isArray(r) ? r : []))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }, [layer]);

  return (
    <AppShell>
      <div className="space-y-4">
        <div>
          <h1 className="text-lg font-medium">Memory Layers</h1>
          <p className="text-sm text-muted-foreground">
            Persistent memory tiers from working to core.
          </p>
        </div>

        <div className="flex gap-1 rounded-lg border border-border bg-card/40 p-1">
          {LAYERS.map((l) => {
            const count = (memStats[l] as number | undefined) ?? null;
            return (
              <button
                key={l}
                onClick={() => setLayer(l)}
                className={`flex-1 rounded-md px-3 py-2 text-sm capitalize transition-colors ${
                  layer === l
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {l}
                {count !== null && (
                  <span className="ml-2 font-mono text-xs text-muted-foreground">{count}</span>
                )}
              </button>
            );
          })}
        </div>

        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {error && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}
        {!loading && !error && items.length === 0 && (
          <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            No memories in this layer.
          </p>
        )}

        <div className="space-y-2">
          {items.map((m, i) => (
            <div
              key={m.id ?? i}
              className="rounded-lg border border-border bg-card p-3"
            >
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{m.content}</p>
              <div className="mt-2 flex items-center gap-3 text-xs font-mono text-muted-foreground">
                {m.trust_score !== undefined && (
                  <span>
                    trust{" "}
                    <span className="text-foreground">
                      {Number(m.trust_score).toFixed(2)}
                    </span>
                  </span>
                )}
                {m.created_at && (
                  <span>{new Date(m.created_at).toLocaleString()}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
