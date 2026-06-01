import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { vespera, type OllamaModel } from "@/lib/vespera";

export const Route = createFileRoute("/models")({
  head: () => ({ meta: [{ title: "Models · Vespera" }] }),
  component: ModelsPage,
});

const ASSIGNABLE = ["Background Loop", "Cleanup Crew", "Periodic Pruning", "Handoff"];

function formatSize(bytes?: number) {
  if (!bytes && bytes !== 0) return "—";
  const gb = bytes / 1e9;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  return `${(bytes / 1e6).toFixed(0)} MB`;
}

function AssignCard({ name, models }: { name: string; models: OllamaModel[] }) {
  const [model, setModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      await vespera.updateComponent(name, { model });
      setMsg("Saved");
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
        <h3 className="font-mono text-sm">{name}</h3>
        {msg && <span className="text-xs text-muted-foreground">{msg}</span>}
      </div>
      <div className="mt-3 flex gap-2">
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
        >
          <option value="">— select model —</option>
          {models.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>
        <button
          onClick={save}
          disabled={saving || !model}
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

function ModelsPage() {
  const [models, setModels] = useState<OllamaModel[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    vespera
      .models()
      .then((r: any) => setModels(Array.isArray(r) ? r : Array.isArray(r?.models) ? r.models : []))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <AppShell>
      <div className="space-y-6">
        <div>
          <h1 className="text-lg font-medium">Model Selector</h1>
          <p className="text-sm text-muted-foreground">Downloaded Ollama models on this machine.</p>
        </div>

        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {error && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {models.map((m) => (
            <button
              key={m.name}
              onClick={() => setSelected(m.name)}
              className={`rounded-lg border p-4 text-left transition-colors ${
                selected === m.name
                  ? "border-primary bg-secondary"
                  : "border-border bg-card hover:bg-secondary/60"
              }`}
            >
              <div className="font-mono text-sm">{m.name}</div>
              <div className="mt-1 text-xs text-muted-foreground">{formatSize(m.size)}</div>
            </button>
          ))}
          {!loading && models.length === 0 && (
            <p className="text-sm text-muted-foreground">No models found.</p>
          )}
        </div>

        <div className="pt-2">
          <h2 className="text-md font-medium">Assignments</h2>
          <p className="text-sm text-muted-foreground">Pick a model for each background component.</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {ASSIGNABLE.map((name) => (
              <AssignCard key={name} name={name} models={models} />
            ))}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
