import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import AppShell from "@/components/AppShell";

export const Route = createFileRoute("/phone-setup")({
  head: () => ({ meta: [{ title: "Phone Setup · Vespera" }] }),
  component: PhoneSetupPage,
});

const port = import.meta.env.VITE_API_PORT ?? "5055";

function PhoneSetupPage() {
  const [os, setOs] = useState<"iphone" | "android">("iphone");

  // Detect the app URL from the current page's host (works for Tailscale too)
  const host = typeof window !== "undefined" ? window.location.hostname : "YOUR-COMPUTER-IP";
  const appUrl = `http://${host}:${port}/app`;

  return (
    <AppShell>
      <div className="max-w-xl space-y-6">
        <div>
          <h1 className="text-lg font-medium">Install on Your Phone</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Vespera works as a phone app without going through any app store. Follow the steps below.
          </p>
        </div>

        {/* Step 0 — prerequisite */}
        <div className="rounded-lg border border-border bg-card p-4 space-y-2">
          <p className="text-sm font-medium">Before you start</p>
          <p className="text-sm text-muted-foreground">
            Your phone and this computer need to be on the same Wi-Fi network, <em>or</em> you need to have{" "}
            <a href="https://tailscale.com" target="_blank" rel="noreferrer" className="text-primary underline">
              Tailscale
            </a>{" "}
            installed on both.
          </p>
          <div className="rounded-md bg-secondary/60 px-3 py-2 font-mono text-sm break-all">
            {appUrl}
          </div>
          <p className="text-xs text-muted-foreground">
            This is your Vespera address. You'll type this into your phone's browser in a moment.
          </p>
        </div>

        {/* OS toggle */}
        <div className="flex gap-2">
          <button
            onClick={() => setOs("iphone")}
            className={`flex-1 rounded-lg border py-2 text-sm font-medium transition-colors ${
              os === "iphone"
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            🍎 iPhone / iPad
          </button>
          <button
            onClick={() => setOs("android")}
            className={`flex-1 rounded-lg border py-2 text-sm font-medium transition-colors ${
              os === "android"
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            🤖 Android
          </button>
        </div>

        {/* Steps */}
        {os === "iphone" ? (
          <ol className="space-y-3">
            <Step n={1} title="Open Safari">
              It <strong>must</strong> be Safari — not Chrome or any other browser. Safari is the
              one with the compass icon.
            </Step>
            <Step n={2} title="Type the address">
              Tap the address bar at the top and type exactly:{" "}
              <span className="font-mono text-primary break-all">{appUrl}</span>
              <br />
              Then tap <strong>Go</strong> on your keyboard.
            </Step>
            <Step n={3} title="Tap the Share button">
              At the bottom of the screen, tap the button that looks like a box with an arrow
              pointing up out of it (⬆️).
            </Step>
            <Step n={4} title='Tap "Add to Home Screen"'>
              Scroll down the list that appears and tap <strong>Add to Home Screen</strong>.
            </Step>
            <Step n={5} title="Tap Add">
              In the top right corner, tap <strong>Add</strong>.
            </Step>
            <Step n={6} title="Done!">
              Vespera now appears on your home screen like any other app. Tap it to open.
            </Step>
          </ol>
        ) : (
          <ol className="space-y-3">
            <Step n={1} title="Open Chrome">
              Use Chrome (the colorful circle icon). If you don't have it, download it free from
              the Play Store first.
            </Step>
            <Step n={2} title="Type the address">
              Tap the address bar and type:{" "}
              <span className="font-mono text-primary break-all">{appUrl}</span>
              <br />
              Then tap <strong>Go</strong>.
            </Step>
            <Step n={3} title="Tap the three-dot menu">
              In the top right corner of Chrome, tap the three vertical dots (⋮).
            </Step>
            <Step n={4} title='Tap "Add to Home Screen"'>
              Find and tap <strong>Add to Home screen</strong> in the menu.
            </Step>
            <Step n={5} title="Tap Add">
              Confirm by tapping <strong>Add</strong>.
            </Step>
            <Step n={6} title="Done!">
              Vespera now appears on your home screen. Tap it to open just like any other app.
            </Step>
          </ol>
        )}

        {/* Troubleshooting */}
        <div className="rounded-lg border border-border bg-card/40 p-4 space-y-2">
          <p className="text-sm font-medium text-muted-foreground">Can't connect?</p>
          <ul className="text-sm text-muted-foreground space-y-1 list-disc list-inside">
            <li>Make sure Vespera is running on your computer.</li>
            <li>Make sure your phone and computer are on the same Wi-Fi.</li>
            <li>If you're away from home, you'll need Tailscale installed on both devices.</li>
            <li>Double-check the address — one wrong character and it won't load.</li>
          </ul>
        </div>
      </div>
    </AppShell>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="flex-shrink-0 h-6 w-6 rounded-full bg-primary/20 text-primary text-xs font-bold flex items-center justify-center mt-0.5">
        {n}
      </span>
      <div>
        <p className="text-sm font-medium">{title}</p>
        <p className="text-sm text-muted-foreground mt-0.5 leading-relaxed">{children}</p>
      </div>
    </li>
  );
}
