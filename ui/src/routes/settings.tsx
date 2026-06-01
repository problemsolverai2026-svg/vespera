import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { vespera, type ComponentInfo } from "@/lib/vespera";

interface AppSetting {
  key: string;
  label: string;
  description: string;
  type: "number" | "float";
  value: number;
  default: number;
}

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

  const [settings, setSettings] = useState<AppSetting[]>([]);
  const [settingVals, setSettingVals] = useState<Record<string, number>>({});
  const [settingSaving, setSettingSaving] = useState(false);
  const [settingMsg, setSettingMsg] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    vespera
      .components()
      .then((r: any) => {
        const arr = Array.isArray(r) ? r : Object.values(r ?? {});
        setComps(arr as ComponentInfo[]);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed"))
      .finally(() => setLoading(false));
  };

  const loadSettings = () => {
    fetch("/api/settings")
      .then((r) => r.json())
      .then((r: any) => {
        const list: AppSetting[] = r.settings ?? [];
        setSettings(list);
        const vals: Record<string, number> = {};
        list.forEach((s) => (vals[s.key] = s.value));
        setSettingVals(vals);
      })
      .catch(() => {});
  };

  const saveSettings = async () => {
    setSettingSaving(true);
    setSettingMsg(null);
    try {
      const body: Record<string, number> = {};
      settings.forEach((s) => (body[s.key] = settingVals[s.key] ?? s.value));
      const r = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error("Save failed");
      setSettingMsg("Saved");
    } catch (e) {
      setSettingMsg(e instanceof Error ? e.message : "Failed");
    } finally {
      setSettingSaving(false);
      setTimeout(() => setSettingMsg(null), 2500);
    }
  };

  useEffect(() => { load(); loadSettings(); }, []);

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

        {settings.length > 0 && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-md font-medium">System Settings</h2>
                <p className="text-sm text-muted-foreground">Tune Vespera's behaviour.</p>
              </div>
              <div className="flex items-center gap-2">
                {settingMsg && <span className="text-xs text-muted-foreground">{settingMsg}</span>}
                <button
                  onClick={saveSettings}
                  disabled={settingSaving}
                  className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
                >
                  {settingSaving ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {settings.map((s) => (
                <div key={s.key} className="rounded-lg border border-border bg-card p-4">
                  <label className="block">
                    <div className="flex items-baseline justify-between">
                      <span className="font-mono text-sm">{s.label}</span>
                      <span className="text-xs text-muted-foreground">default: {s.default}</span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{s.description}</p>
                    <input
                      type="number"
                      step={s.type === "float" ? 0.05 : 1}
                      min={s.type === "float" ? 0 : 1}
                      max={s.type === "float" ? 1 : undefined}
                      value={settingVals[s.key] ?? s.value}
                      onChange={(e) =>
                        setSettingVals((prev) => ({
                          ...prev,
                          [s.key]: s.type === "float" ? parseFloat(e.target.value) : parseInt(e.target.value, 10),
                        }))
                      }
                      className="mt-2 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
                    />
                  </label>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
