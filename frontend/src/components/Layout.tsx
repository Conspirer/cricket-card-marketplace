import { useEffect, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useSession } from "../session";
import { runs } from "../format";
import { BallSeam } from "./CardBack";
import { AuthPage } from "../pages/AuthPage";
import { FeedTicker } from "./FeedTicker";
import { UserLink } from "./UserLink";
import { api } from "../api";
import { useQuery } from "@tanstack/react-query";

const NAV = [
  { to: "/", label: "Packs" },
  { to: "/collection", label: "Collection" },
  { to: "/market", label: "Market" },
  { to: "/battles", label: "Battles" },
  { to: "/sbc", label: "SBCs" },
  { to: "/trades", label: "Trades" },
];

export function Layout() {
  const { user, loading } = useSession();
  const { pathname } = useLocation();
  // Everything except the credits page needs an account.
  const needsLogin = !loading && !user && pathname !== "/credits";

  // Incoming offers badge on the nav.
  const { data: trades } = useQuery({ queryKey: ["trades"], queryFn: api.trades, enabled: !!user, refetchInterval: 30_000 });
  const incoming = trades?.incoming.length ?? 0;

  // Playtest logging: one row per user per day, counted server-side.
  useEffect(() => {
    if (user) api.visit().catch(() => {});
  }, [user, pathname]);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-line bg-night/90 backdrop-blur-sm">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:gap-8 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2.5 text-cream">
            <BallSeam className="h-6 w-6 text-leather" />
            <span className="hidden font-display text-2xl font-black tracking-[0.18em] min-[420px]:inline">CREASE</span>
          </NavLink>

          {/* Scrolls sideways on phones rather than wrapping. */}
          <nav className={`flex h-full min-w-0 items-stretch gap-1 overflow-x-auto [scrollbar-width:none] sm:gap-2 ${user ? "" : "invisible"}`}>
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `relative flex shrink-0 items-center gap-1.5 px-2 font-display text-[15px] font-bold tracking-[0.12em] uppercase transition-colors sm:px-3 ${
                    isActive
                      ? "text-cream after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:bg-brass sm:after:inset-x-3"
                      : "text-mute hover:text-cream"
                  }`
                }
              >
                {item.label}
                {item.to === "/trades" && incoming > 0 && (
                  <span className="rounded-full bg-leather px-1.5 font-mono text-[10px] leading-4 tracking-normal text-cream" aria-label={`${incoming} incoming offers`}>
                    {incoming}
                  </span>
                )}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto hidden items-center gap-5 sm:flex">
            {user && (
              <div className="text-right">
                <div className="eyebrow !text-[10px]">Balance</div>
                <div className="font-mono text-sm text-brass-bright">{runs(user.balance)} RUNS</div>
              </div>
            )}
            {user && <UserMenu />}
          </div>
        </div>
        {/* Phones: balance and account drop to their own row. */}
        {user && <FeedTicker />}
        {user && (
          <div className="flex items-center justify-between border-t border-line px-4 py-2 sm:hidden">
            <span className="font-mono text-xs text-brass-bright">{runs(user.balance)} RUNS</span>
            <UserMenu />
          </div>
        )}
      </header>

      <main className="mx-auto max-w-7xl px-4 pt-10 pb-24 sm:px-6 sm:pt-14">
        {loading ? null : needsLogin ? <AuthPage /> : <Outlet />}
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-7xl flex-wrap justify-between gap-2 px-4 py-5 font-mono text-[11px] text-faint sm:px-6">
          <span>Runs are virtual and have no cash value.</span>
          <NavLink to="/credits" className="hover:text-cream">Data: Cricsheet · Wikipedia · Wikidata</NavLink>
        </div>
      </footer>
    </div>
  );
}

function UserMenu() {
  const { user, logout } = useSession();
  return (
    <div className="flex items-center gap-3">
      {user && (
        <UserLink name={user.username} className="max-w-[140px] truncate font-display text-lg font-extrabold tracking-[0.04em] uppercase" />
      )}
      <button
        onClick={() => logout()}
        className="font-mono text-[11px] tracking-[0.12em] text-faint uppercase hover:text-cream"
      >
        Log out
      </button>
    </div>
  );
}

export function PageHeader({ eyebrow, title, children }: { eyebrow: string; title: string; children?: ReactNode }) {
  return (
    <div className="mb-10 flex flex-col gap-6 border-b border-line pb-8 md:flex-row md:items-end md:justify-between">
      <div>
        <div className="eyebrow mb-3">{eyebrow}</div>
        <h1 className="font-display text-6xl leading-[0.85] font-black uppercase sm:text-7xl">{title}</h1>
      </div>
      {children}
    </div>
  );
}

export function RarityFilter<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T | null;
  options: T[];
  onChange: (v: T | null) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <button className="chip" data-active={value === null} onClick={() => onChange(null)}>
        All
      </button>
      {options.map((o) => (
        <button key={o} className="chip" data-active={value === o} onClick={() => onChange(o)}>
          {o}
        </button>
      ))}
    </div>
  );
}
