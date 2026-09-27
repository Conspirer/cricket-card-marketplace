import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { api, type CollectionCard, type Sbc, type SbcResult } from "../api";
import { useSession } from "../session";
import { useToast } from "../toast";
import { RARITY_COLOR, RARITY_RANK, runs, shortDate } from "../format";
import { PageHeader } from "../components/Layout";
import { TradingCard } from "../components/TradingCard";

function status(c: Sbc) {
  if (c.completed >= c.max_completions) return { label: "Completed", tone: "text-pitch" };
  if (!c.open) return { label: "Closed", tone: "text-faint" };
  return { label: c.ends_at ? `Open until ${shortDate(c.ends_at)}` : "Open", tone: "text-brass-bright" };
}

function RewardLine({ reward }: { reward: Sbc["reward"] }) {
  return (
    <ul className="space-y-1.5">
      {reward.cards.map((r) => (
        <li key={r.edition_key} className="flex gap-2.5">
          <span className="mt-1 h-9 w-1 shrink-0 rounded-full" style={{ background: RARITY_COLOR[r.rarity] }} />
          <span>
            <span className="block font-display text-lg leading-tight font-extrabold uppercase">
              {r.player_name} · {r.edition_label}
            </span>
            <span className="font-mono text-[10px] tracking-[0.1em] text-faint uppercase">
              {r.rarity} edition · {r.max_supply - r.minted_count}/{r.max_supply} left
            </span>
          </span>
        </li>
      ))}
      {reward.runs > 0 && (
        <li className="font-display text-lg font-extrabold uppercase">
          {runs(reward.runs)} <span className="text-mute">runs</span>
        </li>
      )}
    </ul>
  );
}

export function SbcsPage() {
  const { data: challenges = [], isLoading } = useQuery({ queryKey: ["sbcs"], queryFn: api.sbcs });

  return (
    <>
      <PageHeader eyebrow="Squad building challenges" title="Challenges">
        <p className="max-w-sm text-mute">
          Hand in a squad that meets the brief. The cards you submit are destroyed; the reward is yours to keep.
        </p>
      </PageHeader>

      {!isLoading && challenges.length === 0 && <p className="text-mute">No challenges right now.</p>}

      <div className="grid gap-5 md:grid-cols-2">
        {challenges.map((c) => {
          const s = status(c);
          return (
            <Link
              key={c.slug}
              to={`/sbc/${c.slug}`}
              className="group flex flex-col border border-line bg-surface p-6 transition-colors hover:border-brass"
            >
              <div className="flex items-baseline justify-between gap-4">
                <span className={`font-mono text-[11px] tracking-[0.12em] uppercase ${s.tone}`}>{s.label}</span>
                <span className="font-mono text-[11px] tracking-[0.12em] text-faint uppercase">{c.card_count} cards</span>
              </div>
              <h2 className="mt-2 font-display text-4xl font-black uppercase group-hover:text-brass-bright">{c.title}</h2>
              <p className="mt-1 text-mute">{c.description}</p>
              <ul className="mt-4 space-y-1 text-sm">
                {c.requirements.map((r) => (
                  <li key={r} className="flex gap-2">
                    <span className="text-faint">·</span>
                    {r}
                  </li>
                ))}
              </ul>
              <div className="mt-5 border-t border-line pt-4">
                <div className="eyebrow mb-2 !text-[10px]">Reward</div>
                <RewardLine reward={c.reward} />
              </div>
            </Link>
          );
        })}
      </div>
    </>
  );
}

export function SbcPage() {
  const { slug = "" } = useParams();
  const { user } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [picked, setPicked] = useState<number[]>([]);
  const [search, setSearch] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<SbcResult | null>(null);

  const { data: challenge, error } = useQuery({ queryKey: ["sbc", slug], queryFn: () => api.sbc(slug) });
  const { data: cards = [] } = useQuery({
    queryKey: ["collection", user?.id],
    queryFn: () => api.collection(user!.id),
    enabled: !!user,
  });
  // Live checklist from the server: the same rules the submission is judged by.
  const { data: check, isPlaceholderData: stale } = useQuery({
    queryKey: ["sbc-check", slug, picked],
    queryFn: () => api.checkSbc(slug, picked),
    enabled: picked.length > 0,
    placeholderData: keepPreviousData,
  });

  const submit = useMutation({
    mutationFn: () => api.submitSbc(slug, picked),
    onSuccess: (r) => {
      setConfirming(false);
      setPicked([]);
      setResult(r);
      toast(`${challenge!.title} complete`);
      queryClient.invalidateQueries();
    },
    onError: (e: Error) => {
      setConfirming(false);
      toast(e.message, "error");
      queryClient.invalidateQueries({ queryKey: ["sbc-check"] });
    },
  });

  if (error) {
    return (
      <div className="py-20">
        <h1 className="font-display text-6xl font-black uppercase">No such challenge</h1>
        <Link to="/sbc" className="btn btn-ghost mt-8">All challenges</Link>
      </div>
    );
  }
  if (!challenge) return null;

  const byId = new Map(cards.map((c) => [c.id, c]));
  const pickedCards = picked.map((id) => byId.get(id)).filter((c): c is CollectionCard => !!c);
  const pickedPlayers = new Set(pickedCards.map((c) => c.player_id));
  const full = picked.length >= challenge.card_count;
  const done = challenge.completed >= challenge.max_completions;
  const q = search.trim().toLowerCase();
  const visible = cards
    .filter((c) => !q || `${c.player_name} ${c.player_country} ${c.player_role} ${c.rarity}`.toLowerCase().includes(q))
    .sort((a, b) => RARITY_RANK[a.rarity] - RARITY_RANK[b.rarity] || a.player_name.localeCompare(b.player_name));

  function unavailable(card: CollectionCard) {
    if (card.active_listing_id) return "Listed";
    if (card.in_battle) return "In a battle";
    return null;
  }

  function toggle(id: number) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  }

  const checklist =
    picked.length > 0 && check
      ? check.checklist
      : challenge.requirements.map((rule) => ({ rule, ok: false, progress: "" }));
  const ready = picked.length > 0 && !stale && !!check?.ready && !done && challenge.open;

  return (
    <>
      <Link to="/sbc" className="eyebrow hover:text-cream">← All challenges</Link>
      <div className="mt-4 mb-10 border-b border-line pb-8">
        <div className="eyebrow mb-3">{status(challenge).label}</div>
        <h1 className="font-display text-6xl leading-[0.85] font-black uppercase sm:text-7xl">{challenge.title}</h1>
        <p className="mt-3 max-w-xl text-mute">{challenge.description}</p>
      </div>

      <div className="grid gap-10 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="space-y-8 lg:sticky lg:top-24 lg:self-start">
          <section>
            <div className="eyebrow mb-3">Reward</div>
            <div className="flex flex-wrap items-start gap-5">
              {challenge.reward.cards.map((r) => (
                <div key={r.edition_key} className="w-[150px]">
                  <TradingCard
                    size="fluid"
                    tilt={false}
                    card={{ ...r, serial_number: Math.min(r.minted_count + 1, r.max_supply) }}
                  />
                </div>
              ))}
              <div className="min-w-0 flex-1">
                <RewardLine reward={challenge.reward} />
              </div>
            </div>
          </section>

          <section>
            <div className="mb-3 flex items-baseline justify-between">
              <span className="eyebrow">Requirements</span>
              <span className="font-mono text-[11px] text-faint">checked by the server</span>
            </div>
            <ul className="divide-y divide-line border-y border-line">
              {checklist.map((item) => (
                <li key={item.rule} className="flex items-center gap-3 py-2.5">
                  <span
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] ${
                      item.ok ? "border-pitch bg-pitch text-night" : "border-line-strong text-transparent"
                    }`}
                    aria-label={item.ok ? "met" : "not met"}
                  >
                    ✓
                  </span>
                  <span className={`flex-1 ${item.ok ? "text-cream" : "text-mute"}`}>{item.rule}</span>
                  <span className="font-mono text-xs text-faint">{item.progress}</span>
                </li>
              ))}
            </ul>
            {check && Object.keys(check.blocked).length > 0 && (
              <p className="mt-3 text-sm text-leather">
                {Object.entries(check.blocked).map(([id, why]) => `#${id} is ${why}`).join("; ")}
              </p>
            )}
          </section>

          {done ? (
            <p className="border border-pitch/50 bg-pitch/10 px-4 py-3 text-cream">You've completed this challenge.</p>
          ) : (
            <button className="btn btn-primary w-full" disabled={!ready} onClick={() => setConfirming(true)}>
              Submit {picked.length}/{challenge.card_count} cards
            </button>
          )}

          {result && (
            <div className="border border-brass/60 bg-brass/10 px-4 py-3">
              <div className="eyebrow !text-brass-bright">Reward claimed</div>
              <ul className="mt-1 space-y-1">
                {result.reward_cards.map((id) => (
                  <li key={id}>
                    <Link to={`/cards/${id}`} className="text-cream underline decoration-brass underline-offset-4">
                      See your new card →
                    </Link>
                  </li>
                ))}
                {result.reward_runs > 0 && <li>+{runs(result.reward_runs)} runs</li>}
              </ul>
            </div>
          )}
        </div>

        <section>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <span className="eyebrow">Your cards · pick {challenge.card_count}</span>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, country, role"
              className="w-full border border-line bg-surface px-3 py-2 text-sm placeholder:text-faint focus:border-brass focus:outline-none sm:w-64"
            />
          </div>
          {cards.length === 0 ? (
            <p className="text-mute">No cards yet. Open a pack first.</p>
          ) : (
            <div className="grid max-h-[640px] grid-cols-1 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
              {visible.map((card) => {
                const selected = picked.includes(card.id);
                const reason =
                  unavailable(card) ??
                  (!selected && pickedPlayers.has(card.player_id) ? "Same player" : null);
                const disabled = !selected && (!!reason || full || done);
                return (
                  <button
                    key={card.id}
                    onClick={() => toggle(card.id)}
                    disabled={disabled}
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
                        {card.player_country} · {card.player_role} · {card.rarity} #{card.serial_number}
                      </span>
                    </span>
                    {reason && <span className="font-mono text-[9px] tracking-[0.1em] text-faint uppercase">{reason}</span>}
                  </button>
                );
              })}
            </div>
          )}
        </section>
      </div>

      <AnimatePresence>
        {confirming && (
          <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center bg-night/90 px-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => !submit.isPending && setConfirming(false)}
          >
            <motion.div
              role="dialog"
              aria-modal="true"
              aria-labelledby="sbc-confirm-title"
              className="w-full max-w-md border border-leather/60 bg-raised p-6"
              initial={{ y: 12 }}
              animate={{ y: 0 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="eyebrow !text-leather">This can't be undone</div>
              <h2 id="sbc-confirm-title" className="mt-2 font-display text-3xl font-black uppercase">
                Destroy {pickedCards.length} cards?
              </h2>
              <p className="mt-2 text-mute">
                These cards will be permanently destroyed. They leave your collection for good and can't be sold,
                traded or played again.
              </p>
              <ul className="mt-4 space-y-1 border-y border-line py-3 font-mono text-sm">
                {pickedCards.map((c) => (
                  <li key={c.id} className="flex justify-between">
                    <span>{c.player_name}</span>
                    <span className="text-faint">{c.rarity} #{c.serial_number}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-5 flex justify-end gap-3">
                <button className="btn btn-ghost" disabled={submit.isPending} onClick={() => setConfirming(false)}>
                  Keep my cards
                </button>
                <button className="btn btn-danger" disabled={submit.isPending} onClick={() => submit.mutate()}>
                  {submit.isPending ? "Submitting…" : "Destroy and claim"}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
