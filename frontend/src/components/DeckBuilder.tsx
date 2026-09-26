import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type CollectionCard } from "../api";
import { RARITY_COLOR } from "../format";

export const DECK_SIZE = 6;
export const CREDIT_CAP = 100;

type Props = {
  userId: number;
  submitLabel: string;
  busy?: boolean;
  onSubmit: (cardIds: number[]) => void;
};

// Names, roles, rarity and credits only: stats stay hidden until they're called.
export function DeckBuilder({ userId, submitLabel, busy, onSubmit }: Props) {
  const { data: cards = [] } = useQuery({ queryKey: ["collection", userId], queryFn: () => api.collection(userId) });
  const [picked, setPicked] = useState<number[]>([]);
  const [role, setRole] = useState<string | null>(null);

  const byId = new Map(cards.map((c) => [c.id, c]));
  const cost = picked.reduce((sum, id) => sum + (byId.get(id)?.credits ?? 0), 0);
  const roles = [...new Set(cards.map((c) => c.player_role))].sort();
  const visible = cards
    .filter((c) => !role || c.player_role === role)
    .sort((a, b) => b.credits - a.credits || a.player_name.localeCompare(b.player_name));

  function toggle(card: CollectionCard) {
    setPicked((p) =>
      p.includes(card.id) ? p.filter((id) => id !== card.id) : p.length < DECK_SIZE ? [...p, card.id] : p,
    );
  }

  const full = picked.length === DECK_SIZE;
  const over = cost > CREDIT_CAP;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-wrap gap-2">
          <button className="chip" data-active={role === null} onClick={() => setRole(null)}>All roles</button>
          {roles.map((r) => (
            <button key={r} className="chip" data-active={role === r} onClick={() => setRole(r)}>{r}</button>
          ))}
        </div>
        <div className="min-w-[220px]">
          <div className="flex justify-between font-mono text-xs">
            <span className="text-mute">{picked.length}/{DECK_SIZE} cards</span>
            <span className={over ? "text-leather" : "text-cream"}>{cost}/{CREDIT_CAP} credits</span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line">
            <div
              className={`h-full rounded-full transition-all ${over ? "bg-leather" : "bg-brass"}`}
              style={{ width: `${Math.min(100, (cost / CREDIT_CAP) * 100)}%` }}
            />
          </div>
        </div>
      </div>

      {cards.length === 0 ? (
        <p className="text-mute">No cards yet. Open a pack first.</p>
      ) : (
        <div className="grid max-h-[420px] grid-cols-1 gap-2 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((card) => {
            const selected = picked.includes(card.id);
            const blocked = !selected && (full || cost + card.credits > CREDIT_CAP);
            return (
              <button
                key={card.id}
                onClick={() => toggle(card)}
                disabled={blocked}
                className={`flex items-center gap-3 border px-3 py-2.5 text-left transition-colors ${
                  selected ? "border-brass bg-brass/10" : "border-line hover:border-line-strong"
                } disabled:cursor-not-allowed disabled:opacity-35`}
              >
                <span className="h-9 w-1 shrink-0 rounded-full" style={{ background: RARITY_COLOR[card.rarity] }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-display text-lg leading-tight font-extrabold uppercase">
                    {card.player_name}
                  </span>
                  <span className="font-mono text-[10px] tracking-[0.1em] text-faint uppercase">
                    {card.player_role} · {card.rarity} #{card.serial_number}
                  </span>
                </span>
                <span className="text-right">
                  <span className="block font-mono text-sm">{card.credits}</span>
                  <span className="font-mono text-[9px] tracking-[0.1em] text-faint uppercase">{card.player_tier} tier</span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      <div className="mt-5 flex items-center justify-between gap-4">
        <p className="font-mono text-[11px] text-faint">Credits come from the player's tier. Rarity has no effect in battles.</p>
        <button className="btn btn-primary" disabled={!full || over || busy} onClick={() => onSubmit(picked)}>
          {submitLabel}
        </button>
      </div>
    </div>
  );
}
