import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Card, type CardEvent, type PlayerThemeStats, type Sale, type StatKey } from "../api";
import { useSession } from "../session";
import { useToast } from "../toast";
import { longDate, RARITY_COLOR, runs, serial, shortDate, STAT_LABELS, statValue } from "../format";
import { TradingCard } from "../components/TradingCard";
import { UserLink } from "../components/UserLink";

const FEE_RATE = 0.05;

export function CardPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const { data: card, error } = useQuery({ queryKey: ["card", id], queryFn: () => api.card(id) });
  const { data: history = [] } = useQuery({ queryKey: ["history", id], queryFn: () => api.history(id) });
  const { data: sales = [] } = useQuery({
    queryKey: ["price-history", card?.card_definition_id],
    queryFn: () => api.priceHistory(card!.card_definition_id),
    enabled: !!card,
  });
  const { data: themeStats } = useQuery({
    queryKey: ["theme-stats", card?.player_id],
    queryFn: () => api.themeStats(card!.player_id),
    enabled: !!card,
  });

  if (error) {
    return (
      <div className="py-20">
        <div className="eyebrow mb-3">Not found</div>
        <h1 className="font-display text-6xl font-black uppercase">No such card</h1>
        <Link to="/market" className="btn btn-ghost mt-8">
          Back to market
        </Link>
      </div>
    );
  }
  if (!card) return null;

  // Every LISTED event is later closed by DELISTED or SOLD, so if the most
  // recent event is LISTED, that listing is still active.
  const last = history[history.length - 1];
  const activeListing =
    last?.event_type === "LISTED" && last.related_listing_id
      ? { id: last.related_listing_id, price: last.price! }
      : null;

  return (
    <div className="grid gap-12 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-20">
      <div className="lg:sticky lg:top-28 lg:self-start">
        <div className="mx-auto max-w-[360px]">
          <TradingCard card={card} size="fluid" />
        </div>
      </div>

      <div>
        <button onClick={() => navigate(-1)} className="eyebrow cursor-pointer hover:text-cream">
          ← Back
        </button>
        <div className="mt-6 flex items-center gap-3">
          <span className="h-2 w-2 rounded-full" style={{ background: RARITY_COLOR[card.rarity] }} />
          <span className="eyebrow" style={{ color: RARITY_COLOR[card.rarity] }}>
            {card.rarity}{card.edition_label ? ` · ${card.edition_label} edition` : ""} · {card.player_role} · {card.player_country}
          </span>
        </div>
        <h1 className="mt-3 font-display text-[clamp(3.5rem,8vw,6.5rem)] leading-[0.82] font-black uppercase">
          {card.player_name}
        </h1>

        <dl className="mt-8 grid grid-cols-3 border-y border-line">
          <Stat label="Serial" value={`#${serial(card.serial_number)}`} />
          <Stat label="Print run" value={String(card.max_supply)} />
          <Stat label="Owner" value={<UserLink name={card.owner_username} />} small />
        </dl>

        {card.burned_at ? (
          <div className="mt-8 border border-leather/50 bg-leather/10 px-5 py-4">
            <div className="eyebrow !text-leather">Destroyed</div>
            <p className="mt-1 text-mute">
              Submitted to a squad challenge on {shortDate(card.burned_at)}. It can't be listed, traded or played.
            </p>
          </div>
        ) : (
          <Actions card={card} activeListing={activeListing} />
        )}

        {themeStats && <ThemeStatsSection data={themeStats} />}

        <PriceHistory sales={sales} />

        <section className="mt-14">
          <h2 className="eyebrow mb-5">Provenance</h2>
          <ol className="relative border-l border-line-strong">
            {[...history].reverse().map((event, i) => (
              <HistoryRow key={i} event={event} first={i === 0} />
            ))}
          </ol>
        </section>
      </div>
    </div>
  );
}

function ThemeStatsSection({ data }: { data: PlayerThemeStats }) {
  if (data.hidden) {
    return (
      <section className="mt-14">
        <h2 className="eyebrow mb-3">Stats by theme</h2>
        <p className="border border-line bg-surface px-5 py-4 text-mute">
          Hidden while this player is in an active battle. Battles are won on knowing the numbers.
        </p>
      </section>
    );
  }

  return (
    <section className="mt-14">
      <h2 className="eyebrow mb-5">Stats by theme</h2>
      <div className="grid gap-4">
        {data.themes.map((t) => (
          <div key={t.theme} className="border border-line">
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line bg-surface px-4 py-2.5">
              <span className="flex items-baseline gap-2">
                <span className="font-display text-lg font-extrabold tracking-[0.05em] uppercase">{t.label}</span>
                {t.tier === "rare" && (
                  <span className="font-mono text-[10px] tracking-[0.14em] text-brass-bright uppercase">Rare theme</span>
                )}
              </span>
              <span className="font-mono text-[11px] text-faint">{t.matches} matches</span>
            </div>

            {t.matches === 0 ? (
              <p className="px-4 py-3 text-sm text-mute">Didn't play{t.tier === "rare" ? " in the covered editions" : ""}.</p>
            ) : t.stats ? (
              <dl className="grid grid-cols-3 gap-px bg-line sm:grid-cols-6">
                {(Object.entries(t.stats) as [StatKey, number | null][]).map(([stat, value]) => (
                  <div key={stat} className="bg-night px-4 py-3">
                    <dt className="font-mono text-[10px] tracking-[0.12em] text-faint uppercase">{STAT_LABELS[stat]}</dt>
                    <dd className="mt-1 font-mono text-lg tabular-nums">{statValue(stat, value)}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="px-4 py-3 text-sm text-mute">
                Not verified yet, so not shown or used in battles.
                {t.notes && <span className="mt-1 block font-mono text-[11px] text-faint">{t.notes}</span>}
              </p>
            )}

            <p className="border-t border-line px-4 py-2 font-mono text-[10px] text-faint">
              {t.source}
              {t.as_of ? ` · as of ${longDate(t.as_of)}` : ""}
            </p>
          </div>
        ))}
      </div>
      <p className="mt-4 font-mono text-[11px] text-faint">
        "–" means no data or below the minimum sample. <a href="/credits" className="underline hover:text-cream">Data sources</a>
      </p>
    </section>
  );
}

function Stat({ label, value, small }: { label: string; value: ReactNode; small?: boolean }) {
  return (
    <div className="min-w-0 border-line py-4 not-last:border-r not-first:pl-4">
      <dt className="eyebrow !text-[10px]">{label}</dt>
      <dd className={`mt-1 truncate font-display font-black ${small ? "text-2xl" : "text-3xl"}`}>{value}</dd>
    </div>
  );
}

function Actions({ card, activeListing }: { card: Card; activeListing: { id: number; price: string } | null }) {
  const { user } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [price, setPrice] = useState("");

  const mine = user?.username === card.owner_username;
  const refresh = () => queryClient.invalidateQueries();
  const fail = (err: Error) => toast(err.message, "error");

  const list = useMutation({
    mutationFn: () => api.createListing(card.id, user!.id, price),
    onSuccess: () => {
      toast(`Listed for ${runs(price)} runs`);
      setPrice("");
      refresh();
    },
    onError: fail,
  });
  const cancel = useMutation({
    mutationFn: () => api.cancelListing(activeListing!.id, user!.id),
    onSuccess: () => {
      toast("Listing cancelled");
      refresh();
    },
    onError: fail,
  });
  const buy = useMutation({
    mutationFn: () => api.buy(activeListing!.id, user!.id),
    onSuccess: () => {
      toast(`It's yours: ${card.player_name} #${serial(card.serial_number)}`);
      refresh();
    },
    onError: fail,
  });

  const priceNum = Number(price);
  const validPrice = price !== "" && priceNum > 0;
  const fee = Math.round(priceNum * FEE_RATE * 100) / 100;

  return (
    <section className="mt-10 border border-line bg-surface p-6">
      {mine && !activeListing && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (validPrice) list.mutate();
          }}
        >
          <label className="eyebrow mb-3 block" htmlFor="price">
            List for sale
          </label>
          <div className="flex flex-col gap-3 sm:flex-row">
            <div className="flex h-11 flex-1 items-center border border-line-strong bg-night focus-within:border-brass">
              <input
                id="price"
                inputMode="decimal"
                placeholder="0"
                value={price}
                onChange={(e) => setPrice(e.target.value.replace(/[^0-9.]/g, ""))}
                className="h-full min-w-0 flex-1 bg-transparent px-4 font-mono text-lg text-cream outline-none"
              />
              <span className="pr-4 font-mono text-xs tracking-[0.14em] text-faint">RUNS</span>
            </div>
            <button className="btn btn-primary" disabled={!validPrice || list.isPending}>
              List card
            </button>
          </div>
          {validPrice && (
            <p className="mt-3 font-mono text-xs text-mute">
              5% fee ({runs(fee)}) is burned on sale · you receive{" "}
              <span className="text-cream">{runs(priceNum - fee)}</span>
            </p>
          )}
        </form>
      )}

      {mine && activeListing && (
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="eyebrow">Listed for</div>
            <div className="mt-1 font-mono text-2xl text-brass-bright">{runs(activeListing.price)} RUNS</div>
          </div>
          <button className="btn btn-danger" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            Cancel listing
          </button>
        </div>
      )}

      {!mine && activeListing && (
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="eyebrow">Buy now</div>
            <div className="mt-1 font-mono text-2xl text-brass-bright">{runs(activeListing.price)} RUNS</div>
          </div>
          <button
            className="btn btn-primary"
            onClick={() => buy.mutate()}
            disabled={!user || buy.isPending || Number(user.balance) < Number(activeListing.price)}
          >
            {user && Number(user.balance) < Number(activeListing.price) ? "Not enough runs" : "Buy card"}
          </button>
        </div>
      )}

      {!mine && !activeListing && (
        <div>
          <div className="eyebrow">Not for sale</div>
          <p className="mt-2 text-mute">{card.owner_username} is holding this one.</p>
        </div>
      )}
    </section>
  );
}

function PriceHistory({ sales }: { sales: Sale[] }) {
  const ordered = [...sales].reverse();
  const prices = ordered.map((s) => Number(s.price));
  const lastSale = prices[prices.length - 1];
  const average = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : 0;

  return (
    <section className="mt-14">
      <h2 className="eyebrow mb-5">Sales · this card, all serials</h2>
      {prices.length === 0 ? (
        <p className="text-mute">No sales yet. The first one sets the price.</p>
      ) : (
        <div className="grid gap-6 sm:grid-cols-[auto_1fr] sm:items-end">
          <dl className="flex gap-8">
            <div>
              <dt className="eyebrow !text-[10px]">Last</dt>
              <dd className="font-display text-4xl font-black text-brass-bright">{runs(lastSale)}</dd>
            </div>
            <div>
              <dt className="eyebrow !text-[10px]">Avg</dt>
              <dd className="font-display text-4xl font-black">{runs(Math.round(average))}</dd>
            </div>
            <div>
              <dt className="eyebrow !text-[10px]">Sales</dt>
              <dd className="font-display text-4xl font-black">{prices.length}</dd>
            </div>
          </dl>
          {prices.length > 1 && <Sparkline values={prices} />}
        </div>
      )}
    </section>
  );
}

function Sparkline({ values }: { values: number[] }) {
  const w = 300;
  const h = 56;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values.map((v, i) => [(i / (values.length - 1)) * w, h - 4 - ((v - min) / span) * (h - 8)]);
  const path = points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lx, ly] = points[points.length - 1];

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-14 w-full" preserveAspectRatio="none" aria-label="Sale price trend">
      <path d={`${path} L${w},${h} L0,${h} Z`} fill="var(--color-brass)" opacity="0.08" />
      <path d={path} fill="none" stroke="var(--color-brass)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      <circle cx={lx} cy={ly} r="3" fill="var(--color-brass-bright)" />
    </svg>
  );
}

const EVENT_LABEL: Record<CardEvent["event_type"], string> = {
  MINTED: "Minted",
  PULLED: "Pulled",
  LISTED: "Listed",
  DELISTED: "Delisted",
  SOLD: "Sold",
  BURNED: "Destroyed",
  TRADED: "Traded",
};

function HistoryRow({ event, first }: { event: CardEvent; first: boolean }) {
  const description = (() => {
    switch (event.event_type) {
      case "PULLED":
        return (
          <>
            from pack #{event.related_pack_opening_id} by <b><UserLink name={event.to_username!} /></b>
          </>
        );
      case "MINTED":
        return (
          <>
            to <b><UserLink name={event.to_username!} /></b>
          </>
        );
      case "LISTED":
        return (
          <>
            by <b><UserLink name={event.from_username!} /></b> for {runs(event.price!)}
          </>
        );
      case "DELISTED":
        return (
          <>
            by <b><UserLink name={event.from_username!} /></b>
          </>
        );
      case "TRADED":
        return (
          <>
            <b><UserLink name={event.from_username!} /></b> → <b><UserLink name={event.to_username!} /></b> in trade #{event.related_trade_id}
          </>
        );
      case "BURNED":
        return (
          <>
            in a squad challenge by <b><UserLink name={event.from_username!} /></b>
          </>
        );
      case "SOLD":
        return (
          <>
            <b><UserLink name={event.from_username!} /></b> → <b><UserLink name={event.to_username!} /></b> for {runs(event.price!)}
          </>
        );
    }
  })();

  const tone =
    event.event_type === "SOLD" || event.event_type === "TRADED" ? "bg-brass"
    : event.event_type === "PULLED" ? "bg-pitch"
    : event.event_type === "BURNED" ? "bg-leather"
    : "bg-line-strong";

  return (
    <li className="relative pb-6 pl-6 last:pb-0">
      <span
        className={`absolute top-1.5 -left-[5px] h-2.5 w-2.5 rounded-full ring-4 ring-night ${tone} ${
          first ? "outline outline-1 outline-offset-2 outline-brass/50" : ""
        }`}
      />
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-display text-lg font-extrabold tracking-[0.08em] uppercase">
          {EVENT_LABEL[event.event_type]}
        </span>
        <span className="text-mute [&_b]:font-medium [&_b]:text-cream">{description}</span>
      </div>
      <div className="mt-0.5 font-mono text-[11px] text-faint">{shortDate(event.created_at)}</div>
    </li>
  );
}
