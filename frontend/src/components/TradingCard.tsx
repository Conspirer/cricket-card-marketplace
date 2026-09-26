import { useRef, type PointerEvent } from "react";
import type { Card } from "../api";
import { countryCode, serial } from "../format";

export type CardFace = Pick<
  Card,
  "serial_number" | "rarity" | "player_name" | "player_role" | "player_country" | "max_supply"
>;

// Surname = last word plus any lowercase particles before it ("de Kock").
function splitName(name: string) {
  const words = name.trim().split(/\s+/);
  if (words.length === 1) return { first: "", last: words[0] };
  let start = words.length - 1;
  while (start > 1 && /^[a-z]/.test(words[start - 1])) start--;
  return { first: words.slice(0, start).join(" "), last: words.slice(start).join(" ") };
}

type Props = {
  card: CardFace;
  size?: "sm" | "md" | "lg" | "fluid";
  tilt?: boolean;
};

// Only call a serial out when it's genuinely scarce relative to its print run,
// otherwise small runs would badge nearly every card.
function serialBadge(serialNumber: number, maxSupply: number) {
  if (serialNumber === 1) return "First print";
  if (serialNumber <= 10 && maxSupply >= 100) return "Low serial";
  return null;
}

export function TradingCard({ card, size = "md", tilt = true }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  // Written straight to CSS variables (not React state) so tilt tracking
  // doesn't re-render the card on every pointer move.
  function onMove(e: PointerEvent) {
    const el = ref.current;
    if (!el || !tilt) return;
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    el.style.setProperty("--mx", `${x * 100}%`);
    el.style.setProperty("--my", `${y * 100}%`);
    el.style.setProperty("--rx", `${(0.5 - y) * 16}deg`);
    el.style.setProperty("--ry", `${(x - 0.5) * 20}deg`);
    el.dataset.active = "";
  }

  function onLeave() {
    const el = ref.current;
    if (!el) return;
    el.style.setProperty("--rx", "0deg");
    el.style.setProperty("--ry", "0deg");
    delete el.dataset.active;
  }

  const { first, last } = splitName(card.player_name);
  const badge = serialBadge(card.serial_number, card.max_supply);
  const lastSize = last.length > 8 ? "15cqw" : last.length > 6 ? "18cqw" : "21cqw";

  return (
    <div
      ref={ref}
      className={`tc tc--${size} tc--${card.rarity.toLowerCase()}`}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
    >
      <div className="tc__body">
        <div className="tc__face">
          <StumpsArt />
          <div className="tc__top">
            <span className="tc__rarity">{card.rarity}</span>
            <span className="tc__country">{countryCode(card.player_country)}</span>
          </div>
          {badge && <span className="tc__low">{badge}</span>}

          <div className="tc__name">
            {first && <span className="tc__first">{first}</span>}
            <span className="tc__last" style={{ fontSize: lastSize }}>
              {last}
            </span>
          </div>
          <div className="tc__foot">
            <span>{card.player_role}</span>
            <span className="tc__serial">
              <b>#</b>{serial(card.serial_number)}/{card.max_supply}
            </span>
          </div>
        </div>
        <div className="tc__holo" />
        <div className="tc__glare" />
      </div>
    </div>
  );
}

// Line-art wicket and creases, drawn in the card's rarity colour.
function StumpsArt() {
  return (
    <svg className="tc__art" viewBox="0 0 100 140" preserveAspectRatio="xMidYMid slice" aria-hidden>
      <defs>
        <radialGradient id="tc-halo" cx="50%" cy="38%" r="45%">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.35" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="100" height="140" fill="url(#tc-halo)" />
      <g stroke="currentColor" fill="none" strokeWidth="0.5" opacity="0.8">
        <line x1="-5" y1="64" x2="105" y2="64" />
        <line x1="-5" y1="71" x2="105" y2="71" />
        <line x1="22" y1="64" x2="22" y2="140" />
        <line x1="78" y1="64" x2="78" y2="140" />
      </g>
      <g fill="currentColor" opacity="0.75">
        <rect x="41" y="16" width="2.6" height="55" rx="1.1" />
        <rect x="48.7" y="16" width="2.6" height="55" rx="1.1" />
        <rect x="56.4" y="16" width="2.6" height="55" rx="1.1" />
        <rect x="41.5" y="13.6" width="8.8" height="1.6" rx="0.8" />
        <rect x="50" y="13.6" width="8.8" height="1.6" rx="0.8" />
      </g>
    </svg>
  );
}
