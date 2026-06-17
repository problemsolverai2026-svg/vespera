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
  const imgRef = useRef<HTMLImageElement>(null);
  const scale = useRef(1);
  const tx = useRef(0);
  const ty = useRef(0);
  const lastDist = useRef<number | null>(null);
  const lastTouch = useRef<{ x: number; y: number } | null>(null);
  const lastTap = useRef(0);

  const applyTransform = useCallback(() => {
    if (!imgRef.current) return;
    imgRef.current.style.transform = `translate(${tx.current}px, ${ty.current}px) scale(${scale.current})`;
  }, []);

  const clampTranslation = useCallback(() => {
    if (!imgRef.current) return;
    const img = imgRef.current;
    const w = img.naturalWidth * scale.current;
    const h = img.naturalHeight * scale.current;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const maxX = Math.max(0, (w - vw) / 2);
    const maxY = Math.max(0, (h - vh) / 2);
    tx.current = Math.min(maxX, Math.max(-maxX, tx.current));
    ty.current = Math.min(maxY, Math.max(-maxY, ty.current));
  }, []);

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if (e.touches.length === 2) {
      const dx = e.touches[1].clientX - e.touches[0].clientX;
      const dy = e.touches[1].clientY - e.touches[0].clientY;
      lastDist.current = Math.hypot(dx, dy);
      lastTouch.current = null;
    } else if (e.touches.length === 1) {
      lastTouch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      lastDist.current = null;
    }
  }, []);

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    e.preventDefault();
    if (e.touches.length === 2 && lastDist.current !== null) {
      // Pinch zoom
      const dx = e.touches[1].clientX - e.touches[0].clientX;
      const dy = e.touches[1].clientY - e.touches[0].clientY;
      const dist = Math.hypot(dx, dy);
      const delta = dist / lastDist.current;
      scale.current = Math.min(8, Math.max(1, scale.current * delta));
      lastDist.current = dist;
      clampTranslation();
      applyTransform();
    } else if (e.touches.length === 1 && lastTouch.current && scale.current > 1) {
      // Pan when zoomed
      const dx = e.touches[0].clientX - lastTouch.current.x;
      const dy = e.touches[0].clientY - lastTouch.current.y;
      tx.current += dx;
      ty.current += dy;
      lastTouch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      clampTranslation();
      applyTransform();
    }
  }, [applyTransform, clampTranslation]);

  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    // Double-tap to reset zoom
    if (e.changedTouches.length === 1 && scale.current === 1) {
      const now = Date.now();
      if (now - lastTap.current < 300) {
        scale.current = 1;
        tx.current = 0;
        ty.current = 0;
        applyTransform();
      }
      lastTap.current = now;
    }
    if (e.touches.length < 2) lastDist.current = null;
    if (e.touches.length < 1) lastTouch.current = null;
  }, [applyTransform]);

  const handleBackdropTap = useCallback(() => {
    if (scale.current > 1) {
      // First tap when zoomed: reset zoom instead of closing
      scale.current = 1;
      tx.current = 0;
      ty.current = 0;
      applyTransform();
    } else {
      onClose();
    }
  }, [applyTransform, onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/90"
      onClick={handleBackdropTap}
      style={{ touchAction: "none" }}
    >
      <img
        ref={imgRef}
        src={src}
        alt="photo"
        className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain shadow-2xl select-none"
        style={{ transformOrigin: "center center", willChange: "transform", touchAction: "none" }}
        onClick={(e) => e.stopPropagation()}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
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
