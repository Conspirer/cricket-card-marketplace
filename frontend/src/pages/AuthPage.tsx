import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useToast } from "../toast";

type Mode = "login" | "register";

export function AuthPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>("register");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = mode === "register" ? await api.register(username, password) : await api.login(username, password);
      toast(mode === "register" ? `Welcome, ${user.username}. 10,000 Runs to start.` : `Welcome back, ${user.username}.`);
      await queryClient.invalidateQueries();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-12 lg:grid-cols-[minmax(0,6fr)_minmax(0,5fr)] lg:items-center lg:gap-20">
      <div>
        <div className="eyebrow mb-5">Series One · Friends only</div>
        <h1 className="font-display text-[clamp(4rem,11vw,8rem)] leading-[0.86] font-black uppercase">
          Take
          <br />
          <span className="text-brass">guard.</span>
        </h1>
        <p className="mt-8 max-w-md text-lg leading-relaxed text-mute">
          Make an account to open packs, trade cards and battle your friends. You start with 10,000 Runs. Runs are virtual
          and have no cash value.
        </p>
      </div>

      <form onSubmit={submit} className="border border-line bg-surface p-6 sm:p-8" noValidate>
        <div className="mb-6 grid grid-cols-2 border border-line">
          {(["register", "login"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => { setMode(m); setError(null); }}
              className={`h-11 font-display text-base font-extrabold tracking-[0.08em] uppercase transition-colors ${
                mode === m ? "bg-cream text-night" : "text-mute hover:text-cream"
              }`}
            >
              {m === "register" ? "Create account" : "Log in"}
            </button>
          ))}
        </div>

        <label className="eyebrow mb-2 block" htmlFor="username">Username</label>
        <input
          id="username"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mb-5 h-11 w-full border border-line-strong bg-night px-4 font-mono text-cream outline-none focus:border-brass"
        />
        <label className="eyebrow mb-2 block" htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete={mode === "register" ? "new-password" : "current-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="h-11 w-full border border-line-strong bg-night px-4 font-mono text-cream outline-none focus:border-brass"
        />
        {mode === "register" && (
          <p className="mt-2 font-mono text-[11px] text-faint">3–20 letters, digits or _ · password at least 8 characters</p>
        )}

        {error && <p className="mt-4 border border-leather/60 bg-leather/10 px-3 py-2 text-sm text-[#e88a7d]">{error}</p>}

        <button className="btn btn-primary mt-6 w-full" disabled={busy || !username || !password}>
          {busy ? "…" : mode === "register" ? "Create account" : "Log in"}
        </button>
      </form>
    </div>
  );
}
