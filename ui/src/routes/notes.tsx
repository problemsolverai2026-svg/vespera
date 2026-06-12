import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import { vespera, type NoteItem } from "@/lib/vespera";

export const Route = createFileRoute("/notes")({
  head: () => ({ meta: [{ title: "Notes · Vespera" }] }),
  component: NotesPage,
});

function NotesPage() {
  const [notes, setNotes] = useState<NoteItem[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = () => {
    vespera
      .notes()
      .then((r) => setNotes(Array.isArray(r) ? r : []))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load notes"));
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    const content = input.trim();
    if (!content) return;
    setLoading(true);
    setError(null);
    try {
      await vespera.addNote(content);
      setInput("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save note");
    } finally {
      setLoading(false);
    }
  };

  const del = async (id: string) => {
    try {
      await vespera.deleteNote(id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete note");
    }
  };

  return (
    <AppShell>
      <div className="space-y-4">
        <div>
          <h1 className="text-lg font-medium">Notes</h1>
          <p className="text-sm text-muted-foreground">
            Quick notes from Telegram or here. Say <span className="font-mono">note: something</span> on Telegram to add one.
          </p>
        </div>

        {/* Add note */}
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
            placeholder="Type a note and press Enter…"
            className="flex-1 rounded-lg border border-border bg-card px-3 py-2 text-sm outline-none focus:border-ring"
          />
          <button
            onClick={save}
            disabled={loading || !input.trim()}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            Save
          </button>
        </div>

        {error && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}

        {/* Notes list */}
        {notes.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            No notes yet. Add one above or say <span className="font-mono">note: something</span> on Telegram.
          </p>
        ) : (
          <div className="space-y-2">
            {notes.map((n) => (
              <div
                key={n.id}
                className="flex items-start gap-3 rounded-lg border border-border bg-card p-3"
              >
                <div className="flex-1 min-w-0">
                  <p className="whitespace-pre-wrap text-sm leading-relaxed break-words">{n.content}</p>
                  <div className="mt-1.5 flex items-center gap-3 text-xs font-mono text-muted-foreground">
                    <span>{new Date(n.created_at).toLocaleString()}</span>
                    <span className="opacity-50">{n.id.slice(0, 8)}</span>
                  </div>
                </div>
                <button
                  onClick={() => del(n.id)}
                  className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                  title="Delete note"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
