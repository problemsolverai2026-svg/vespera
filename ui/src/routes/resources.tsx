import { createFileRoute } from "@tanstack/react-router";
import AppShell from "@/components/AppShell";

export const Route = createFileRoute("/resources")({
  head: () => ({ meta: [{ title: "Resources · Vespera" }] }),
  component: ResourcesPage,
});

interface Resource {
  name: string;
  purpose: string;
  tier: "Free" | "Free tier" | "Paid";
  url: string;
}

const RESOURCES: Resource[] = [
  { name: "Groq", purpose: "Fast hosted inference for cloud LLM fallback.", tier: "Free", url: "https://console.groq.com/keys" },
  { name: "Google Gemini", purpose: "Gemini models for reasoning and long context.", tier: "Free tier", url: "https://aistudio.google.com/app/apikey" },
  { name: "Anthropic Claude", purpose: "Claude models for high-quality reasoning.", tier: "Paid", url: "https://console.anthropic.com/settings/keys" },
  { name: "Venice AI", purpose: "Private, uncensored hosted inference.", tier: "Paid", url: "https://venice.ai/settings/api" },
  { name: "Brave Search", purpose: "Web search results for the Web Search component.", tier: "Free tier", url: "https://api.search.brave.com/app/keys" },
  { name: "Telegram BotFather", purpose: "Create a bot token for the Telegram bridge.", tier: "Free", url: "https://t.me/BotFather" },
];

const tierColor = (t: Resource["tier"]) =>
  t === "Free"
    ? "text-[var(--local)] border-[var(--local)]/40"
    : t === "Free tier"
      ? "text-foreground border-border"
      : "text-[var(--cloud)] border-[var(--cloud)]/40";

function ResourcesPage() {
  return (
    <AppShell>
      <div className="space-y-6">
        <div>
          <h1 className="text-lg font-medium">Resources</h1>
          <p className="text-sm text-muted-foreground">
            Every external API Vespera can plug into. Grab a key, paste it on the API Keys page.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {RESOURCES.map((r) => (
            <div key={r.name} className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="font-mono text-sm">{r.name}</h3>
                <span className={`rounded-full border px-2 py-0.5 text-xs ${tierColor(r.tier)}`}>
                  {r.tier}
                </span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">{r.purpose}</p>
              <a
                href={r.url}
                target="_blank"
                rel="noreferrer"
                className="mt-3 inline-block text-xs text-primary underline hover:opacity-80"
              >
                Sign up / get key →
              </a>
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
