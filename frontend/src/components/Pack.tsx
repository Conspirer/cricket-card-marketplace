import { BallSeam } from "./CardBack";

// Crimped top and bottom edges, like a heat-sealed foil wrapper.
const TEETH = 18;
const DEPTH = 1.6;
const CRIMP = (() => {
  const top = Array.from({ length: TEETH + 1 }, (_, i) => `${(i / TEETH) * 100}% ${i % 2 ? DEPTH : 0}%`);
  const bottom = Array.from(
    { length: TEETH + 1 },
    (_, i) => `${100 - (i / TEETH) * 100}% ${i % 2 ? 100 - DEPTH : 100}%`,
  );
  return `polygon(${[...top, ...bottom].join(", ")})`;
})();

type Props = { type: "standard" | "premium"; price: number; cards: number; live?: boolean };

export function Pack({ type, price, cards, live }: Props) {
  const premium = type === "premium";

  return (
    <div className={`pack pack--${type}`} data-live={live} style={{ clipPath: CRIMP }}>
      <div className="pack__foil">
        <div className="pack__crimp top-0" />
        <div className="pack__crimp bottom-0" />

        <div
          className="absolute top-1/2 left-[7cqw] -translate-x-1/2 -translate-y-1/2 -rotate-90 font-mono text-[3.4cqw] tracking-[0.4em] whitespace-nowrap opacity-50"
        >
          CREASE · SERIES ONE · CREASE
        </div>

        <div className="absolute inset-x-[14cqw] top-[16%] bottom-[12%] flex flex-col">
          <BallSeam className={`w-[26cqw] ${premium ? "text-brass" : "text-cream/80"}`} />
          <div className="mt-auto font-display leading-[0.84] font-black uppercase">
            <div className="mb-[3cqw] text-[6.5cqw] tracking-[0.22em] opacity-70">{premium ? "Limited" : "Everyday"}</div>
            <div className="text-[30cqw]">{premium ? "Test" : "Club"}</div>
            <div className="text-[14cqw] tracking-[0.04em]">Pack</div>
          </div>
          <div className="mt-[7cqw] flex items-baseline justify-between border-t border-current/30 pt-[3.5cqw] font-mono text-[4cqw] tracking-[0.14em] uppercase">
            <span>{cards} cards</span>
            <span>{price} runs</span>
          </div>
        </div>
      </div>
    </div>
  );
}
