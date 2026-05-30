import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { vespera, type SecuritySettings } from "@/lib/vespera";

export const Route = createFileRoute("/api-keys")({
  head: () => ({ meta: [{ title: "API Keys · Vespera" }] }),
  component: ApiKeysPage,
});

const COMPONENTS: { name: string; description: string }[] = [
  { name: "Cloud AI", description: "Routes complex prompts to a hosted LLM (Anthropic, Gemini, etc.) when local can't handle it." },
  { name: "Background Loop", description: "Idle-time worker that consolidates memories and runs reflection passes." },
  { name: "Cleanup Crew", description: "Removes duplicates and low-trust junk from the Working layer." },
  { name: "Periodic Pruning", description: "Demotes or evicts stale memories that fall below trust thresholds." },
  { name: "Handoff", description: "Transfers context between local and cloud models without losing state." },
  { name: "Web Search", description: "Fetches live results when a question needs the open web." },
  { name: "Telegram", description: "Bridges Vespera with a Telegram bot for chat from your phone." },
  { name: "TTS/Voice", description: "Generates spoken audio replies you can play back in the browser." },
];

function ApiKeyCard({ name, description }: { name: string; description: string }) {
  const [value, setValue] = useState("");
  const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      await vespera.updateComponent(name, { api_key: value });
      setMsg("Saved");
      setValue("");
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
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      <label className="mt-3 block">
        <span className="text-xs text-muted-foreground">API key</span>
        <div className="mt-1 flex gap-2">
          <input
            type={show ? "text" : "password"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="paste key…"
            className="flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-sm font-mono outline-none focus:border-ring"
          />
          <button
            type="button"
            onClick={() => setShow((s) => !s)}
            className="rounded-md border border-border bg-secondary px-2 py-1.5 text-xs hover:bg-accent"
          >
            {show ? "Hide" : "Show"}
          </button>
        </div>
      </label>
      <button
        onClick={save}
        disabled={saving || !value}
        className="mt-3 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
      >
        {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

function Row({
  label,
  children,
  onSave,
  saving,
  msg,
}: {
  label: string;
  children: React.ReactNode;
  onSave: () => void;
  saving: boolean;
  msg: string | null;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm">{label}</h3>
        {msg && <span className="text-xs text-muted-foreground">{msg}</span>}
      </div>
      <div className="mt-3">{children}</div>
      <button
        onClick={onSave}
        disabled={saving}
        className="mt-3 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40"
      >
        {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

function SecurityField<K extends keyof SecuritySettings>({
  label,
  k,
  initial,
  type = "text",
}: {
  label: string;
  k: K;
  initial: SecuritySettings;
  type?: "text" | "password" | "number" | "toggle";
}) {
  const [val, setVal] = useState<unknown>(initial[k] ?? (type === "toggle" ? false : type === "number" ? 0 : ""));
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      await vespera.updateSecurity({ [k]: val } as Partial<SecuritySettings>);
      setMsg("Saved");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Failed");
    } finally {
      setSaving(false);
      setTimeout(() => setMsg(null), 2500);
    }
  };

  return (
    <Row label={label} onSave={save} saving={saving} msg={msg}>
      {type === "toggle" ? (
        <button
          type="button"
          onClick={() => setVal(!val)}
          className={`inline-flex h-6 w-11 items-center rounded-full transition-colors ${
            val ? "bg-primary" : "bg-secondary"
          }`}
        >
          <span
            className={`inline-block h-5 w-5 transform rounded-full bg-background transition-transform ${
              val ? "translate-x-5" : "translate-x-0.5"
            }`}
          />
        </button>
      ) : type === "number" ? (
        <input
          type="number"
          value={Number(val) || 0}
          onChange={(e) => setVal(Number(e.target.value))}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm font-mono outline-none focus:border-ring"
        />
      ) : (
        <input
          type={type}
          value={String(val ?? "")}
          onChange={(e) => setVal(e.target.value)}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm font-mono outline-none focus:border-ring"
        />
      )}
    </Row>
  );
}

function ApiKeysPage() {
  const [sec, setSec] = useState<SecuritySettings | null>(null);

  useEffect(() => {
    vespera.security().then(setSec).catch(() => setSec({}));
  }, []);

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex items-end justify-between">
          <div>
            <h1 className="text-lg font-medium">API Keys</h1>
            <p className="text-sm text-muted-foreground">
              Credentials for each component. Stored locally by your Vespera daemon.
            </p>
          </div>
          <Link
            to="/resources"
            className="text-xs text-muted-foreground underline hover:text-foreground"
          >
            Where do I get these?
          </Link>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {COMPONENTS.map((c) => (
            <ApiKeyCard key={c.name} name={c.name} description={c.description} />
          ))}
        </div>

        <div className="pt-4">
          <h2 className="text-md font-medium">Security</h2>
          <p className="text-sm text-muted-foreground">
            Access controls and runtime limits for the daemon.
          </p>
          {sec === null ? (
            <p className="mt-3 text-sm text-muted-foreground">Loading…</p>
          ) : (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <SecurityField
                label="Telegram Allowed User IDs"
                k="telegram_allowed_user_ids"
                initial={sec}
              />
              <SecurityField label="Shell Execution" k="shell_execution" initial={sec} type="toggle" />
              <SecurityField label="Allowed File Paths" k="allowed_file_paths" initial={sec} />
              <SecurityField label="API Auth Token" k="api_auth_token" initial={sec} type="password" />
              <SecurityField label="Max Tokens" k="max_tokens" initial={sec} type="number" />
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
