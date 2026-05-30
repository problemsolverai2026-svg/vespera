import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { vespera, type ComponentInfo } from "@/lib/vespera";

export const Route = createFileRoute("/settings")({
  head: () => ({ meta: [{ title: "Settings · Vespera" }] }),
  component: SettingsPage,
});

function ComponentCard({ c, onSaved }: { c: ComponentInfo; onSaved: () => void }) {
  const [model, setModel] = useState(c.model ?? "");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const body: Partial<ComponentInfo> = { model };
      if (apiKey) body.api_key = apiKey;
      await vespera.updateComponent(c.name, body);
      setApiKey("");
      setMsg("Saved");
      onSaved();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Failed");
    } finally {
      setSaving(false);
      setTimeout(() => setMsg(null), 2500);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="font-mono text-sm">{c.name}</h3>
        {msg && <span className="text-xs text-muted-foreground">{msg}</span>}
      </div>
      {c.description && (
        <p className="mt-1 text-xs text-muted-foreground">{c.description}</p>
      )}
      <div className="mt-3 space-y-2">
        <label className="block">
          <span className="text-xs text-muted-foreground">Model</span>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
          />
        </label>
        <label className="block">
          <span className="text-xs text-muted-foreground">API key</span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={c.api_key ? "•••••••• (set)" : "not set"}
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm font-mono outline-none focus:border-ring"
          />
        </label>
      </div>
      <button
        onClick={save}
        disabled={saving}
        className="mt-3 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
      >
        {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

function SettingsPage() {
  const [comps, setComps] = useState<ComponentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    vespera
      .components()
      .then((r) => setComps(Array.isArray(r) ? r : []))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const run = async (kind: "cleanup" | "prune") => {
    setActionMsg(`${kind}…`);
    try {
      await (kind === "cleanup" ? vespera.cleanup() : vespera.prune());
      setActionMsg(`${kind} complete`);
    } catch (e) {
      setActionMsg(e instanceof Error ? e.message : "Failed");
    } finally {
      setTimeout(() => setActionMsg(null), 2500);
    }
  };

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex items-end justify-between">
          <div>
            <h1 className="text-lg font-medium">Settings</h1>
            <p className="text-sm text-muted-foreground">
              Configure components and run maintenance.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {actionMsg && (
              <span className="text-xs text-muted-foreground">{actionMsg}</span>
            )}
            <button
              onClick={() => run("cleanup")}
              className="rounded-md border border-border bg-secondary px-3 py-1.5 text-sm hover:bg-accent"
            >
              Run Cleanup
            </button>
            <button
              onClick={() => run("prune")}
              className="rounded-md border border-border bg-secondary px-3 py-1.5 text-sm hover:bg-accent"
            >
              Run Pruning
            </button>
          </div>
        </div>

        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {error && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          {comps.map((c) => (
            <ComponentCard key={c.name} c={c} onSaved={load} />
          ))}
        </div>
      </div>
    </AppShell>
  );
}
