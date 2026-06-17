import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState, useCallback } from "react";
import AppShell from "@/components/AppShell";
import { vespera, API_BASE } from "@/lib/vespera";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Chat · Vespera" },
      { name: "description", content: "Local persistent AI memory chat." },
    ],
  }),
  component: ChatPage,
});

interface PhotoItem {
  id: string;
  caption: string;
  created_at: string;
}

interface Msg {
  role: "user" | "assistant";
  content: string;
  used_cloud?: boolean;
  complexity?: number;
  photos?: PhotoItem[];
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
          photos: (res as any).photos ?? undefined,
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
                  {m.photos && m.photos.length > 0 && (
                    <PhotoGrid photos={m.photos} />
                  )}
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
                      {m.complexity !== undefined && (
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

function PhotoGrid({ photos }: { photos: PhotoItem[] }) {
  const [lightbox, setLightbox] = useState<string | null>(null);

  return (
    <>
      <div className="grid grid-cols-3 gap-2 mt-2">
        {photos.map((p) => {
          const src = `${API_BASE}/api/photos/${p.id}/image`;
          return (
            <div
              key={p.id}
              className="relative cursor-pointer overflow-hidden rounded-lg border border-border aspect-square bg-muted"
              onClick={() => setLightbox(src)}
              title={p.caption || p.id.slice(0, 8)}
            >
              <img
                src={src}
                alt={p.caption || "photo"}
                className="h-full w-full object-cover transition-transform hover:scale-105"
                loading="lazy"
              />
              {p.caption && (
                <div className="absolute bottom-0 left-0 right-0 bg-black/50 px-1.5 py-0.5 text-[10px] text-white truncate">
                  {p.caption}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {lightbox && (
        <Lightbox src={lightbox} onClose={() => setLightbox(null)} />
      )}
    </>
  );
}

function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const state = useRef({ scale: 1, tx: 0, ty: 0, lastDist: 0, lastX: 0, lastY: 0, lastTap: 0, touching: false });

  const apply = () => {
    const img = imgRef.current;
    if (!img) return;
    const s = state.current;
    img.style.transform = `translate(${s.tx}px, ${s.ty}px) scale(${s.scale})`;
  };

  const clamp = () => {
    const img = imgRef.current;
    if (!img) return;
    const s = state.current;
    const maxX = Math.max(0, (img.offsetWidth * s.scale - window.innerWidth) / 2);
    const maxY = Math.max(0, (img.offsetHeight * s.scale - window.innerHeight) / 2);
    s.tx = Math.min(maxX, Math.max(-maxX, s.tx));
    s.ty = Math.min(maxY, Math.max(-maxY, s.ty));
  };

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const s = state.current;

    const onStart = (e: TouchEvent) => {
      e.preventDefault();
      s.touching = true;
      if (e.touches.length === 2) {
        const dx = e.touches[1].clientX - e.touches[0].clientX;
        const dy = e.touches[1].clientY - e.touches[0].clientY;
        s.lastDist = Math.hypot(dx, dy);
      } else if (e.touches.length === 1) {
        s.lastX = e.touches[0].clientX;
        s.lastY = e.touches[0].clientY;
      }
    };

    const onMove = (e: TouchEvent) => {
      e.preventDefault();
      if (e.touches.length === 2) {
        const dx = e.touches[1].clientX - e.touches[0].clientX;
        const dy = e.touches[1].clientY - e.touches[0].clientY;
        const dist = Math.hypot(dx, dy);
        if (s.lastDist > 0) {
          s.scale = Math.min(8, Math.max(1, s.scale * (dist / s.lastDist)));
        }
        s.lastDist = dist;
        clamp();
        apply();
      } else if (e.touches.length === 1 && s.scale > 1) {
        s.tx += e.touches[0].clientX - s.lastX;
        s.ty += e.touches[0].clientY - s.lastY;
        s.lastX = e.touches[0].clientX;
        s.lastY = e.touches[0].clientY;
        clamp();
        apply();
      }
    };

    const onEnd = (e: TouchEvent) => {
      e.preventDefault();
      if (e.touches.length === 0) {
        s.touching = false;
        // Double-tap to reset
        if (e.changedTouches.length === 1) {
          const now = Date.now();
          if (now - s.lastTap < 300) {
            s.scale = 1; s.tx = 0; s.ty = 0;
            apply();
          }
          s.lastTap = now;
        }
      }
      if (e.touches.length < 2) s.lastDist = 0;
    };

    el.addEventListener("touchstart", onStart, { passive: false });
    el.addEventListener("touchmove", onMove, { passive: false });
    el.addEventListener("touchend", onEnd, { passive: false });
    return () => {
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/90"
      style={{ touchAction: "none" }}
      onClick={(e) => {
        if (e.target === containerRef.current) {
          const s = state.current;
          if (s.scale > 1) { s.scale = 1; s.tx = 0; s.ty = 0; apply(); }
          else onClose();
        }
      }}
    >
      <img
        ref={imgRef}
        src={src}
        alt="photo"
        className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain shadow-2xl select-none"
        style={{ transformOrigin: "center center", willChange: "transform", touchAction: "none" }}
        draggable={false}
      />
      <button
        className="absolute right-4 top-4 rounded-full bg-black/60 px-3 py-1 text-sm text-white hover:bg-black/80"
        onClick={(e) => { e.stopPropagation(); onClose(); }}
      >
        ✕
      </button>
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full bg-black/50 px-3 py-1 text-xs text-white/70">
        Pinch to zoom · drag to pan · tap to close
      </div>
    </div>
  );
}
