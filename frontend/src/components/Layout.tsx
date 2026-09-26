import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useSession } from "../session";
import { runs } from "../format";
import { BallSeam } from "./CardBack";

const NAV = [
  { to: "/", label: "Packs" },
  { to: "/collection", label: "Collection" },
  { to: "/market", label: "Market" },
  { to: "/battles", label: "Battles" },
];

export function Layout() {
  const { user } = useSession();

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-line bg-night/90 backdrop-blur-sm">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:gap-8 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2.5 text-cream">
            <BallSeam className="h-6 w-6 text-leather" />
            <span className="hidden font-display text-2xl font-black tracking-[0.18em] min-[420px]:inline">CREASE</span>
          </NavLink>

          <nav className="flex h-full items-stretch gap-1 sm:gap-2">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `relative flex items-center px-2 font-display text-[15px] font-bold tracking-[0.12em] uppercase transition-colors sm:px-3 ${
                    isActive
                      ? "text-cream after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:bg-brass sm:after:inset-x-3"
                      : "text-mute hover:text-cream"
                  }`
                }
              >
                {item.label}
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
            <UserPicker />
          </div>
        </div>
        {/* Phones: balance and user picker drop to their own row. */}
        <div className="flex items-center justify-between border-t border-line px-4 py-2 sm:hidden">
          <span className="font-mono text-xs text-brass-bright">{user ? `${runs(user.balance)} RUNS` : ""}</span>
          <UserPicker />
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 pt-10 pb-24 sm:px-6 sm:pt-14">
        <Outlet />
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

function UserPicker() {
  const { user, users, setUserId } = useSession();
  return (
    <label className="flex items-center gap-2">
      <span className="eyebrow hidden !text-[10px] md:inline">Playing as</span>
      <select
        value={user?.id ?? ""}
        onChange={(e) => setUserId(Number(e.target.value))}
        className="h-9 max-w-[150px] cursor-pointer truncate border border-line-strong bg-surface px-2 font-mono text-xs text-cream outline-none focus:border-brass"
      >
        {users.map((u) => (
          <option key={u.id} value={u.id}>
            {u.username}
          </option>
        ))}
      </select>
    </label>
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
