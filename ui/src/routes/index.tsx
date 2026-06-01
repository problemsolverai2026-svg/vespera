import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import { vespera } from "@/lib/vespera";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Chat · Vespera" },
      { name: "description", content: "Local persistent AI memory chat." },
    ],
  }),
  component: ChatPage,
});

interface Msg {
  role: "user" | "assistant";
  content: string;
  used_cloud?: boolean;
  complexity?: number;
}

function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    vespera.conversations(50).then((history: any[]) => {
      setMessages(
        [...history].reverse().map((m) => ({
          role: m.role,
          content: m.content,
          used_cloud: !!m.used_cloud,
          complexity: m.complexity ?? 0,
        }))
      );
    }).catch(() => { /* backend not running or no history yet */ });
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setError(null);
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setLoading(true);
    try {
      const res = await vespera.chat(text);
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: res.response ?? "(empty response)",
          used_cloud: (res as any).handled_by === "cloud",
          complexity: res.complexity,
        },
      ]);
      if (res.audio) {
        try {
          const src = /^https?:|^data:|^blob:/i.test(res.audio)
            ? res.audio
            : `${(await import("@/lib/vespera")).API_BASE}${res.audio.startsWith("/") ? "" : "/"}${res.audio}`;
          const audio = new Audio(src);
          audio.play().catch(() => {});
        } catch {
          /* ignore audio errors */
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AppShell>
      <div className="flex h-[calc(100vh-10rem)] flex-col gap-3">
        {/* Legend */}
        <div className="flex items-center gap-3 px-1 text-xs text-muted-foreground">
          <span className="font-medium">Key:</span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-full bg-[var(--local)]" />
            <span>Local — free</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-full bg-[var(--cloud)]" />
            <span>Cloud — costs money</span>
          </span>
        </div>

        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto rounded-lg border border-border bg-card/40 p-4"
        >
          {messages.length === 0 && (
            <div className="flex h-full items-center justify-center text-center text-sm text-muted-foreground">
              <div>
                <p className="font-mono text-foreground">vespera</p>
                <p className="mt-1">Local persistent AI memory. Say something.</p>
              </div>
            </div>
          )}
          <div className="space-y-4">
            {messages.map((m, i) => (
              <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
                <div
                  className={
                    m.role === "user"
                      ? "max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                      : "max-w-[85%] space-y-2"
                  }
                >
                  <p className="whitespace-pre-wrap text-sm leading-relaxed">{m.content}</p>
                  {m.role === "assistant" && (
                    <div className="flex items-center gap-2 text-xs">
                      {m.used_cloud ? (
                        <span className="flex items-center gap-1 rounded-full px-2 py-0.5 font-medium"
                          style={{ background: "color-mix(in oklch, var(--cloud) 18%, transparent)", color: "var(--cloud)", border: "1px solid color-mix(in oklch, var(--cloud) 35%, transparent)" }}>
                          <span className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--cloud)]" />
                          cloud
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 rounded-full px-2 py-0.5 font-medium"
                          style={{ background: "color-mix(in oklch, var(--local) 18%, transparent)", color: "var(--local)", border: "1px solid color-mix(in oklch, var(--local) 35%, transparent)" }}>
                          <span className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--local)]" />
                          local
                        </span>
                      )}
                      {m.complexity !== undefined && m.complexity > 0 && (
                        <span className="font-mono text-muted-foreground">
                          {Number(m.complexity).toFixed(2)}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {loading && (
              <p className="font-mono text-xs text-muted-foreground">thinking…</p>
            )}
            {error && (
              <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error}
              </p>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Message vespera…"
            rows={2}
            className="flex-1 resize-none rounded-lg border border-border bg-card px-3 py-2 text-sm outline-none focus:border-ring"
          />
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            className="self-end rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </div>
    </AppShell>
  );
}
