import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { api, type Trade, type TradeCard, type TradeEligibility } from "../api";
import { useSession } from "../session";
import { useToast } from "../toast";
import { RARITY_COLOR, RARITY_RANK, shortDate } from "../format";
import { PageHeader } from "../components/Layout";
import { TradingCard } from "../components/TradingCard";
import { UserLink } from "../components/UserLink";

type Tab = "incoming" | "outgoing" | "history";

const STATUS_TONE: Record<Trade["status"], string> = {
  PENDING: "text-brass-bright",
  ACCEPTED: "text-pitch",
  DECLINED: "text-faint",
  CANCELLED: "text-faint",
  EXPIRED: "text-faint",
  INVALID: "text-leather",
};

function expiresIn(iso: string) {
  const hours = Math.max(0, (new Date(iso).getTime() - Date.now()) / 3_600_000);
  return hours >= 1 ? `${Math.floor(hours)}h left` : `${Math.max(1, Math.round(hours * 60))}m left`;
}

function CardStrip({ cards }: { cards: TradeCard[] }) {
  return (
    <div className="flex flex-wrap gap-3">
      {cards.map((c) => (
        <Link key={c.card_instance_id} to={`/cards/${c.card_instance_id}`} className="block w-[104px]">
          <TradingCard card={c} size="fluid" tilt={false} />
        </Link>
      ))}
    </div>
  );
}

/** The two sides from the viewer's point of view. */
function sides(trade: Trade) {
  const proposing = trade.role === "proposer";
  return {
    other: proposing ? trade.recipient : trade.proposer,
    give: proposing ? trade.offered : trade.requested,
    get: proposing ? trade.requested : trade.offered,
  };
}

function EligibilityBanner({ e }: { e: TradeEligibility }) {
  if (e.eligible && !e.at_daily_limit) return null;
  return (
    <div className="mb-8 border border-brass/50 bg-brass/10 px-5 py-4">
      <div className="eyebrow !text-brass-bright">{e.eligible ? "Daily limit reached" : "Not able to trade yet"}</div>
      <p className="mt-1 text-mute">
        {e.eligible
          ? `You've made ${e.trades_today} trades in the last 24 hours (the limit is ${e.rules.max_accepted_per_day}).`
          : `Trading opens once your account is ${e.rules.min_account_age_days} days old and you've finished ${e.rules.min_battles_finished} battles. You've played ${e.battles_finished} and joined ${shortDate(e.joined)}.`}
      </p>
    </div>
  );
}

export function TradesPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "incoming";
  const { data } = useQuery({ queryKey: ["trades"], queryFn: api.trades, refetchInterval: 15_000 });
  const { data: eligibility } = useQuery({ queryKey: ["trade-eligibility"], queryFn: api.tradeEligibility });
  const [confirming, setConfirming] = useState<Trade | null>(null);
  const toast = useToast();
  const queryClient = useQueryClient();

  const done = (message: string) => {
    toast(message);
    setConfirming(null);
    queryClient.invalidateQueries();
  };
  const fail = (e: Error) => {
    toast(e.message, "error");
    setConfirming(null);
    queryClient.invalidateQueries({ queryKey: ["trades"] });
  };
  const accept = useMutation({ mutationFn: (id: number) => api.acceptTrade(id), onSuccess: () => done("Trade done: the cards are yours"), onError: fail });
  const decline = useMutation({ mutationFn: (id: number) => api.declineTrade(id), onSuccess: () => done("Offer declined"), onError: fail });
  const cancel = useMutation({ mutationFn: (id: number) => api.cancelTrade(id), onSuccess: () => done("Offer cancelled"), onError: fail });

  const list = data?.[tab] ?? [];

  return (
    <>
      <PageHeader eyebrow="Card for card" title="Trades">
        <p className="max-w-sm text-mute">
          Swap 1 to {eligibility?.rules.max_cards_per_side ?? 3} cards a side with another player. Offers last{" "}
          {eligibility?.rules.expiry_hours ?? 48} hours. Start one from a player's profile.
        </p>
      </PageHeader>

      {eligibility && <EligibilityBanner e={eligibility} />}

      <div className="mb-8 flex flex-wrap gap-2">
        {(["incoming", "outgoing", "history"] as const).map((t) => (
          <button key={t} className="chip" data-active={tab === t} onClick={() => setParams({ tab: t })}>
            {t === "incoming" ? "Incoming" : t === "outgoing" ? "Outgoing" : "History"}
            {t !== "history" && data ? ` · ${data[t].length}` : ""}
          </button>
        ))}
      </div>

      {data && list.length === 0 && (
        <p className="border border-dashed border-line-strong px-6 py-10 text-center text-mute">
          {tab === "incoming" ? "No offers waiting for you." : tab === "outgoing" ? "You have no open offers." : "No finished trades yet."}
        </p>
      )}

      <div className="space-y-5">
        {list.map((trade) => {
          const { other, give, get } = sides(trade);
          const dead = trade.problems.some((p) => p.permanent);
          return (
            <article key={trade.id} className="border border-line bg-surface p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <div className="font-mono text-[11px] tracking-[0.12em] uppercase">
                  <span className={STATUS_TONE[trade.status]}>{trade.status.toLowerCase()}</span>
                  <span className="text-faint"> · trade #{trade.id} · with </span>
                  <UserLink name={other} className="text-cream" />
                </div>
                <div className="font-mono text-[11px] text-faint">
                  {trade.status === "PENDING" ? expiresIn(trade.expires_at) : shortDate(trade.resolved_at ?? trade.created_at)}
                </div>
              </div>

              <div className="mt-4 grid gap-6 md:grid-cols-[1fr_auto_1fr] md:items-center">
                <div>
                  <div className="eyebrow mb-2 !text-[10px]">You give</div>
                  <CardStrip cards={give} />
                </div>
                <div className="hidden font-display text-3xl text-faint md:block" aria-hidden>⇄</div>
                <div>
                  <div className="eyebrow mb-2 !text-[10px]">You get</div>
                  <CardStrip cards={get} />
                </div>
              </div>

              {trade.invalid_reason && <p className="mt-4 text-sm text-leather">{trade.invalid_reason}</p>}
              {trade.problems.length > 0 && (
                <ul className="mt-4 space-y-1 text-sm">
                  {trade.problems.map((p) => (
                    <li key={p.message} className={p.permanent ? "text-leather" : "text-brass-bright"}>
                      {p.message}
                      {p.permanent ? ": this trade can't go through." : ": it can't be traded until that's cleared."}
                    </li>
                  ))}
                </ul>
              )}

              {trade.status === "PENDING" && (
                <div className="mt-5 flex flex-wrap justify-end gap-3">
                  {trade.role === "recipient" ? (
                    <>
                      <button className="btn btn-ghost" disabled={decline.isPending} onClick={() => decline.mutate(trade.id)}>
                        Decline
                      </button>
                      <button className="btn btn-primary" disabled={dead} onClick={() => setConfirming(trade)}>
                        Review and accept
                      </button>
                    </>
                  ) : (
                    <button className="btn btn-danger" disabled={cancel.isPending} onClick={() => cancel.mutate(trade.id)}>
                      Cancel offer
                    </button>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>

      <ConfirmTrade
        open={!!confirming}
        title="Accept this trade?"
        other={confirming ? sides(confirming).other : ""}
        give={confirming ? sides(confirming).give : []}
        get={confirming ? sides(confirming).get : []}
        action="Accept trade"
        busy={accept.isPending}
        onConfirm={() => confirming && accept.mutate(confirming.id)}
        onClose={() => setConfirming(null)}
      />
    </>
  );
}

export function NewTradePage() {
  const { username = "" } = useParams();
  const { user } = useSession();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [mine, setMine] = useState<number[]>([]);
  const [theirs, setTheirs] = useState<number[]>([]);
  const [reviewing, setReviewing] = useState(false);

  const { data: eligibility } = useQuery({ queryKey: ["trade-eligibility"], queryFn: api.tradeEligibility });
  const { data: theirCards = [], error } = useQuery({
    queryKey: ["tradeable", username.toLowerCase()],
    queryFn: () => api.tradeableCards(username),
  });
  const { data: myCards = [] } = useQuery({
    queryKey: ["tradeable", user?.username.toLowerCase()],
    queryFn: () => api.tradeableCards(user!.username),
    enabled: !!user,
  });

  const send = useMutation({
    mutationFn: () => api.proposeTrade(username, mine, theirs),
    onSuccess: () => {
      toast(`Offer sent to ${username}`);
      queryClient.invalidateQueries({ queryKey: ["trades"] });
      navigate("/trades?tab=outgoing");
    },
    onError: (e: Error) => {
      setReviewing(false);
      toast(e.message, "error");
    },
  });

  if (error) {
    return (
      <div className="py-16">
        <h1 className="font-display text-6xl font-black uppercase">No such player</h1>
        <Link to="/trades" className="btn btn-ghost mt-8">Back to trades</Link>
      </div>
    );
  }

  const max = eligibility?.rules.max_cards_per_side ?? 3;
  const selfTrade = user?.username.toLowerCase() === username.toLowerCase();
  const pick = (setter: typeof setMine) => (id: number) =>
    setter((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < max ? [...p, id] : p));
  const byId = new Map([...myCards, ...theirCards].map((c) => [c.card_instance_id, c]));
  const cardsOf = (ids: number[]) => ids.map((id) => byId.get(id)).filter((c): c is TradeCard => !!c);
  const ready = mine.length > 0 && theirs.length > 0 && !!eligibility?.eligible && !selfTrade;

  return (
    <>
      <Link to={`/u/${encodeURIComponent(username)}`} className="eyebrow hover:text-cream">← {username}'s profile</Link>
      <div className="mt-4 mb-8 border-b border-line pb-8">
        <div className="eyebrow mb-3">New offer</div>
        <h1 className="font-display text-6xl leading-[0.85] font-black uppercase sm:text-7xl">Trade with {username}</h1>
        <p className="mt-3 max-w-xl text-mute">
          Pick 1 to {max} of their cards and 1 to {max} of yours. Nothing moves until they accept, and your cards stay
          usable meanwhile: if one is listed, destroyed or busy in a battle when they accept, the trade won't go through.
        </p>
      </div>

      {eligibility && <EligibilityBanner e={eligibility} />}
      {selfTrade && <p className="mb-8 text-leather">You can't trade with yourself.</p>}

      <div className="grid gap-10 lg:grid-cols-2">
        <Picker title={`${username}'s cards · you get`} cards={theirCards} picked={theirs} max={max} onToggle={pick(setTheirs)} />
        <Picker title="Your cards · you give" cards={myCards} picked={mine} max={max} onToggle={pick(setMine)} own />
      </div>

      <div className="sticky bottom-0 mt-8 flex flex-wrap items-center justify-between gap-4 border-t border-line bg-night/95 py-4 backdrop-blur-sm">
        <span className="font-mono text-xs text-mute">
          You give {mine.length}/{max} · you get {theirs.length}/{max}
        </span>
        <button className="btn btn-primary" disabled={!ready} onClick={() => setReviewing(true)}>
          Review offer
        </button>
      </div>

      <ConfirmTrade
        open={reviewing}
        title="Send this offer?"
        other={username}
        give={cardsOf(mine)}
        get={cardsOf(theirs)}
        action="Send offer"
        note={`${username} has ${eligibility?.rules.expiry_hours ?? 48} hours to accept. You can cancel it until then.`}
        busy={send.isPending}
        onConfirm={() => send.mutate()}
        onClose={() => setReviewing(false)}
      />
    </>
  );
}

function Picker({
  title, cards, picked, max, onToggle, own = false,
}: {
  title: string; cards: TradeCard[]; picked: number[]; max: number; onToggle: (id: number) => void; own?: boolean;
}) {
  const [search, setSearch] = useState("");
  const q = search.trim().toLowerCase();
  const visible = cards
    .filter((c) => !q || `${c.player_name} ${c.player_country} ${c.player_role} ${c.rarity}`.toLowerCase().includes(q))
    .sort((a, b) => RARITY_RANK[b.rarity] - RARITY_RANK[a.rarity] || a.player_name.localeCompare(b.player_name));

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <span className="eyebrow">{title}</span>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search"
          aria-label={`Search ${title}`}
          className="w-full border border-line bg-surface px-3 py-2 text-sm placeholder:text-faint focus:border-brass focus:outline-none sm:w-48"
        />
      </div>
      {cards.length === 0 ? (
        <p className="text-mute">No cards.</p>
      ) : (
        <div className="grid max-h-[520px] grid-cols-1 gap-2 overflow-y-auto pr-1">
          {visible.map((card) => {
            const selected = picked.includes(card.card_instance_id);
            const busy = card.listed ? "Listed" : own && card.in_battle ? "In a battle" : null;
            return (
              <button
                key={card.card_instance_id}
                onClick={() => onToggle(card.card_instance_id)}
                disabled={!selected && picked.length >= max}
                aria-pressed={selected}
                className={`flex items-center gap-3 border px-3 py-2.5 text-left transition-colors ${
                  selected ? "border-brass bg-brass/10" : "border-line hover:border-line-strong"
                } disabled:cursor-not-allowed disabled:opacity-35`}
              >
                <span className="h-9 w-1 shrink-0 rounded-full" style={{ background: RARITY_COLOR[card.rarity] }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-display text-lg leading-tight font-extrabold uppercase">
                    {card.player_name}
                    {card.edition_label && <span className="text-brass-bright"> · {card.edition_label}</span>}
                  </span>
                  <span className="font-mono text-[10px] tracking-[0.1em] text-faint uppercase">
                    {card.player_country} · {card.player_role} · {card.rarity} #{card.serial_number}/{card.max_supply}
                  </span>
                </span>
                {busy && <span className="font-mono text-[9px] tracking-[0.1em] text-brass-bright uppercase">{busy}</span>}
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ConfirmTrade({
  open, title, other, give, get, action, note, busy, onConfirm, onClose,
}: {
  open: boolean; title: string; other: string; give: TradeCard[]; get: TradeCard[]; action: string;
  note?: string; busy: boolean; onConfirm: () => void; onClose: () => void;
}) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-night/90 px-4 py-8"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => !busy && onClose()}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="trade-confirm-title"
            className="w-full max-w-3xl border border-line-strong bg-raised p-6"
            initial={{ y: 12 }}
            animate={{ y: 0 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="eyebrow">Trade with {other}</div>
            <h2 id="trade-confirm-title" className="mt-2 font-display text-3xl font-black uppercase">{title}</h2>

            <div className="mt-5 grid gap-6 sm:grid-cols-2">
              <Side label={`You give · ${give.length}`} tone="text-leather" cards={give} />
              <Side label={`You get · ${get.length}`} tone="text-pitch" cards={get} />
            </div>

            <p className="mt-5 text-sm text-mute">
              {note ?? "The cards change hands as soon as you accept, and a trade can't be undone."}
            </p>
            <div className="mt-5 flex justify-end gap-3">
              <button className="btn btn-ghost" disabled={busy} onClick={onClose}>Back</button>
              <button className="btn btn-primary" disabled={busy} onClick={onConfirm}>{busy ? "…" : action}</button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Side({ label, tone, cards }: { label: string; tone: string; cards: TradeCard[] }) {
  return (
    <div className="border border-line p-4">
      <div className={`eyebrow mb-3 !text-[11px] ${tone}`}>{label}</div>
      <div className="flex flex-wrap gap-3">
        {cards.map((c) => (
          <div key={c.card_instance_id} className="w-[96px]">
            <TradingCard card={c} size="fluid" tilt={false} />
          </div>
        ))}
      </div>
      <ul className="mt-3 space-y-0.5 font-mono text-xs">
        {cards.map((c) => (
          <li key={c.card_instance_id} className="flex justify-between gap-2">
            <span className="truncate">{c.player_name}{c.edition_label ? ` · ${c.edition_label}` : ""}</span>
            <span className="shrink-0 text-faint">{c.rarity} #{c.serial_number}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
