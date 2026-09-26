import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { api, type BattleCard, type BattleRound, type BattleView, type StatKey, type ThemeMeta } from "../api";
import { useSession } from "../session";
import { useToast } from "../toast";
import { STAT_LABELS, statValue } from "../format";
import { TradingCard, type CardFace } from "../components/TradingCard";
import { useCountdown } from "../useCountdown";

const PHASE_SECONDS = { CARD_PICK: 30, STAT_CALL: 15, REVEAL: 6 } as const;

const face = (c: BattleCard): CardFace => ({
  player_name: c.name,
  player_role: c.role,
  player_country: c.country,
  rarity: c.rarity,
  serial_number: c.serial_number,
  max_supply: c.max_supply,
});

export function BattlePage() {
  const id = Number(useParams().id);
  const { user } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const key = ["battle", id, user?.id];

  const { data: view, dataUpdatedAt, error } = useQuery({
    queryKey: key,
    queryFn: () => api.battle(id, user!.id),
    enabled: !!user,
    refetchInterval: (q) => (["ACTIVE", "PENDING"].includes(q.state.data?.status ?? "ACTIVE") ? 1000 : false),
  });

  const onDone = (v: BattleView) => queryClient.setQueryData(key, v);
  const onError = (e: Error) => {
    toast(e.message, "error");
    queryClient.invalidateQueries({ queryKey: key });
  };
  const pick = useMutation({ mutationFn: (cardId: number) => api.pick(id, user!.id, view!.current_round, cardId), onSuccess: onDone, onError });
  const call = useMutation({ mutationFn: (stat: StatKey) => api.call(id, user!.id, view!.current_round, stat), onSuccess: onDone, onError });
  const forfeit = useMutation({ mutationFn: () => api.forfeit(id, user!.id), onSuccess: onDone, onError });

  if (error) {
    return (
      <div className="py-16">
        <h1 className="font-display text-5xl font-black uppercase">Can't open this battle</h1>
        <p className="mt-3 text-mute">{error.message}. Switch "Playing as" to one of its two players.</p>
        <Link to="/battles" className="btn btn-ghost mt-6">Back to battles</Link>
      </div>
    );
  }
  if (!view) return null;

  const finished = ["FINISHED", "FORFEIT"].includes(view.status);
  const last = view.rounds[view.rounds.length - 1];

  return (
    <div className="mx-auto max-w-5xl">
      <ScoreBar view={view} onForfeit={() => forfeit.mutate()} />

      {view.status === "PENDING" && (
        <Notice title="Waiting for your opponent" body={`${view.opponent.username} hasn't accepted yet.`} />
      )}
      {["DECLINED", "EXPIRED"].includes(view.status) && (
        <Notice title={`Challenge ${view.status.toLowerCase()}`} body="No battle was played." />
      )}

      {view.status === "ACTIVE" && view.current && (
        <>
          <ThemeBanner theme={view.current.theme} round={view.current.round} suddenDeath={view.current.sudden_death} />
          <PhaseTimer view={view} fetchedAt={dataUpdatedAt} />
          {view.phase === "CARD_PICK" ? (
            <PickPhase view={view} onPick={(c) => pick.mutate(c)} busy={pick.isPending} />
          ) : (
            <CallPhase view={view} onCall={(s) => call.mutate(s)} busy={call.isPending} />
          )}
        </>
      )}

      {view.status === "ACTIVE" && view.phase === "REVEAL" && last && (
        <>
          <ThemeBanner theme={last.theme} round={last.round} suddenDeath={last.round === 7} />
          <PhaseTimer view={view} fetchedAt={dataUpdatedAt} label="Next round in" />
          <RevealPanel round={last} opponent={view.opponent.username} animate />
        </>
      )}

      {finished && <ResultScreen view={view} />}
    </div>
  );
}

// ---------------------------------------------------------------------------

function ScoreBar({ view, onForfeit }: { view: BattleView; onForfeit: () => void }) {
  const slots = Array.from({ length: Math.max(6, view.rounds.length) }, (_, i) => view.rounds[i]);
  return (
    <div className="mb-8 flex flex-wrap items-center justify-between gap-6 border-b border-line pb-6">
      <div className="flex items-baseline gap-4">
        <span className="font-display text-2xl font-black uppercase">{view.you}</span>
        <span className="font-display text-6xl leading-none font-black tabular-nums">
          {view.score.you}<span className="px-2 text-faint">–</span>{view.score.them}
        </span>
        <span className="font-display text-2xl font-black text-mute uppercase">{view.opponent.username}</span>
      </div>
      <div className="flex items-center gap-4">
        <ol className="flex gap-1.5" aria-label="Round results">
          {slots.map((r, i) => (
            <li
              key={i}
              title={r ? `Round ${r.round}: ${r.result === "draw" ? "drawn" : r.result === "you" ? "won" : "lost"}` : `Round ${i + 1}`}
              className={`flex h-7 w-7 items-center justify-center rounded-full border font-mono text-[10px] ${
                !r ? "border-line text-faint"
                : r.result === "you" ? "border-pitch bg-pitch/25 text-cream"
                : r.result === "them" ? "border-leather bg-leather/25 text-cream"
                : "border-line-strong bg-line text-mute"
              }`}
            >
              {i === 6 ? "SD" : i + 1}
            </li>
          ))}
        </ol>
        {view.status === "ACTIVE" && (
          <button className="font-mono text-[11px] tracking-[0.1em] text-faint uppercase hover:text-leather" onClick={onForfeit}>
            Forfeit
          </button>
        )}
      </div>
    </div>
  );
}

function ThemeBanner({ theme, round, suddenDeath }: { theme: ThemeMeta; round: number; suddenDeath: boolean }) {
  const rare = theme.tier === "rare";
  return (
    <motion.div
      key={`${round}-${theme.key}`}
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      className={`relative mb-4 overflow-hidden border px-6 py-5 ${rare ? "theme-rare border-brass" : "border-line bg-surface"}`}
    >
      <div className="eyebrow">{suddenDeath ? "Sudden death · any card" : `Round ${round} of 6`}</div>
      <div className="mt-1 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className={`font-display text-5xl leading-none font-black uppercase sm:text-6xl ${rare ? "text-brass-bright" : ""}`}>
          {theme.label}
        </h1>
        {rare && <span className="font-mono text-[11px] tracking-[0.2em] text-brass-bright uppercase">Rare theme</span>}
      </div>
    </motion.div>
  );
}

function PhaseTimer({ view, fetchedAt, label }: { view: BattleView; fetchedAt: number; label?: string }) {
  const left = useCountdown(view.phase_deadline, view.server_now, fetchedAt);
  const total = view.phase ? PHASE_SECONDS[view.phase] : 1;
  const text = label ?? (view.phase === "CARD_PICK" ? "Pick a card" : "Stat call");
  return (
    <div className="mb-8">
      <div className="mb-1.5 flex justify-between font-mono text-[11px] tracking-[0.1em] text-mute uppercase">
        <span>{text}</span>
        <span className={left < 5 && view.phase !== "REVEAL" ? "text-leather" : ""}>{Math.ceil(left)}s</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-line">
        <div
          className={`h-full rounded-full ${left < 5 && view.phase !== "REVEAL" ? "bg-leather" : "bg-brass"}`}
          style={{ width: `${Math.min(100, (left / total) * 100)}%`, transition: "width 0.2s linear" }}
        />
      </div>
    </div>
  );
}

function PickPhase({ view, onPick, busy }: { view: BattleView; onPick: (id: number) => void; busy: boolean }) {
  const current = view.current!;
  if (current.your_pick) {
    return (
      <div className="flex flex-col items-center gap-6 py-4 text-center">
        <div className="w-[200px]"><TradingCard card={face(current.your_pick)} size="fluid" /></div>
        <p className="text-mute">
          Locked in. {current.their_pick_made ? "Revealing…" : `Waiting for ${view.opponent.username} to pick…`}
        </p>
      </div>
    );
  }
  return (
    <div>
      <p className="mb-4 text-mute">
        {current.caller === "you" ? "You call this round." : `${view.opponent.username} calls this round.`} Pick the card you
        think is strongest in <span className="text-cream">{current.theme.label}</span>.
      </p>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        {view.hand.map((card) => {
          const disabled = busy || (card.used && !current.sudden_death);
          return (
            <motion.button
              key={card.card_id}
              whileHover={disabled ? undefined : { y: -6 }}
              onClick={() => onPick(card.card_id)}
              disabled={disabled}
              className="cursor-pointer text-left disabled:cursor-not-allowed disabled:opacity-25"
              aria-label={`Play ${card.name}`}
            >
              <TradingCard card={face(card)} size="fluid" tilt={!disabled} />
              <div className="mt-2 font-mono text-[10px] tracking-[0.1em] text-faint uppercase">
                {card.used && !current.sudden_death ? "Played" : `${card.credits} credits`}
              </div>
            </motion.button>
          );
        })}
      </div>
    </div>
  );
}

function CallPhase({ view, onCall, busy }: { view: BattleView; onCall: (s: StatKey) => void; busy: boolean }) {
  const current = view.current!;
  return (
    <div className="grid items-center gap-6 md:grid-cols-[1fr_auto_1fr]">
      <CardSlot card={current.your_pick} label="You" />
      <div className="flex min-w-[240px] flex-col items-center gap-3">
        {current.caller === "you" ? (
          <>
            <p className="eyebrow">Call a stat</p>
            <div className="grid w-full gap-2">
              {current.theme.stats.map((s) => (
                <button key={s.key} className="btn btn-ghost w-full justify-between !px-4" disabled={busy} onClick={() => onCall(s.key)}>
                  <span>{s.label}</span>
                  <span className="font-mono text-[10px] tracking-[0.08em] text-faint normal-case">
                    {s.lower_wins ? "lower wins" : "higher wins"}
                  </span>
                </button>
              ))}
            </div>
          </>
        ) : (
          <p className="py-10 text-center text-mute">{view.opponent.username} is calling a stat…</p>
        )}
      </div>
      <CardSlot card={current.their_pick} label={view.opponent.username} />
    </div>
  );
}

function CardSlot({ card, label, highlight }: { card: BattleCard | null; label: string; highlight?: "win" | "lose" | "draw" }) {
  return (
    <div className="flex flex-col items-center gap-3">
      <span className="eyebrow">{label}</span>
      <div
        className={`w-[210px] rounded-[14px] transition-shadow ${
          highlight === "win" ? "shadow-[0_0_40px_-6px_var(--color-brass)]" : highlight === "lose" ? "opacity-60" : ""
        }`}
      >
        {card ? <TradingCard card={face(card)} size="fluid" /> : <div className="aspect-[5/7] rounded-[14px] border border-dashed border-line" />}
      </div>
      {card && <span className="text-center text-sm">{card.name} · <span className="text-mute">{card.role}</span></span>}
    </div>
  );
}

function FlipNumber({ value, stat, delay, tone }: { value: number | null; stat: StatKey; delay: number; tone: "win" | "lose" | "draw" }) {
  return (
    <motion.span
      initial={{ rotateX: 90, opacity: 0 }}
      animate={{ rotateX: 0, opacity: 1 }}
      transition={{ delay, duration: 0.5, ease: [0.2, 0.8, 0.2, 1] }}
      className={`inline-block font-display text-6xl leading-none font-black tabular-nums ${
        tone === "win" ? "text-brass-bright" : tone === "lose" ? "text-mute" : "text-cream"
      }`}
      style={{ transformOrigin: "50% 50%" }}
    >
      {value == null ? "No data" : statValue(stat, value)}
    </motion.span>
  );
}

function RevealPanel({ round, opponent, animate }: { round: BattleRound; opponent: string; animate?: boolean }) {
  const you = round.result === "you" ? "win" : round.result === "them" ? "lose" : "draw";
  const them = round.result === "them" ? "win" : round.result === "you" ? "lose" : "draw";
  const lowerWins = round.theme.stats.find((s) => s.key === round.stat)?.lower_wins;
  return (
    <div>
      <div className="grid items-center gap-6 md:grid-cols-[1fr_auto_1fr]">
        <CardSlot card={round.your_card} label="You" highlight={you} />
        <div className="flex min-w-[260px] flex-col items-center gap-2 text-center">
          <span className="eyebrow">
            {round.caller === "you" ? "You called" : `${opponent} called`}
            {round.call_timed_out ? " (timed out, random)" : ""}
          </span>
          <span className="font-display text-3xl font-black uppercase">{STAT_LABELS[round.stat]}</span>
          <span className="font-mono text-[10px] text-faint">{lowerWins ? "lower wins" : "higher wins"}</span>
          <div className="mt-3 flex items-center gap-5">
            <FlipNumber value={round.your_value} stat={round.stat} delay={animate ? 0.2 : 0} tone={you} />
            <span className="text-faint">v</span>
            <FlipNumber value={round.their_value} stat={round.stat} delay={animate ? 0.7 : 0} tone={them} />
          </div>
          <motion.span
            initial={animate ? { opacity: 0 } : false}
            animate={{ opacity: 1 }}
            transition={{ delay: animate ? 1.3 : 0 }}
            className={`mt-3 font-display text-2xl font-black uppercase ${
              round.result === "you" ? "text-pitch" : round.result === "them" ? "text-leather" : "text-mute"
            }`}
          >
            {round.result === "you" ? "Round to you" : round.result === "them" ? `Round to ${opponent}` : "Drawn"}
          </motion.span>
        </div>
        <CardSlot card={round.their_card} label={opponent} highlight={them} />
      </div>
      <FullStats round={round} opponent={opponent} />
    </div>
  );
}

function FullStats({ round, opponent }: { round: BattleRound; opponent: string }) {
  return (
    <div className="mt-8 overflow-x-auto border border-line">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line bg-surface">
            <th className="px-4 py-2 text-left font-mono text-[10px] font-normal tracking-[0.14em] text-faint uppercase">
              {round.theme.label}
            </th>
            <th className="px-4 py-2 text-right font-mono text-[10px] font-normal tracking-[0.14em] text-faint uppercase">{round.your_card.name}</th>
            <th className="px-4 py-2 text-right font-mono text-[10px] font-normal tracking-[0.14em] text-faint uppercase">{round.their_card.name} ({opponent})</th>
          </tr>
        </thead>
        <tbody>
          {round.theme.stats.map((s) => (
            <tr key={s.key} className={`border-b border-line last:border-0 ${s.key === round.stat ? "bg-brass/10" : ""}`}>
              <td className="px-4 py-2">
                {s.label} {s.key === round.stat && <span className="ml-1 font-mono text-[10px] text-brass-bright uppercase">called</span>}
              </td>
              <td className="px-4 py-2 text-right font-mono tabular-nums">{statValue(s.key, round.your_card_stats[s.key])}</td>
              <td className="px-4 py-2 text-right font-mono tabular-nums">{statValue(s.key, round.their_card_stats[s.key])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResultScreen({ view }: { view: BattleView }) {
  const title = view.winner === "you" ? "You win" : view.winner === "them" ? `${view.opponent.username} wins` : "Drawn";
  return (
    <div>
      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        className={`mb-10 border px-6 py-8 text-center ${view.winner === "you" ? "border-brass bg-brass/10" : "border-line bg-surface"}`}
      >
        <div className="eyebrow">{view.status === "FORFEIT" ? "Ended by forfeit" : "Final"}</div>
        <h1 className="mt-2 font-display text-7xl leading-none font-black uppercase">{title}</h1>
        <p className="mt-3 font-mono text-sm text-mute">
          {view.score.you}–{view.score.them}{view.score.draws ? ` · ${view.score.draws} drawn` : ""}
        </p>
        <Link to="/battles" className="btn btn-ghost mt-6">Back to battles</Link>
      </motion.div>

      <div className="grid gap-12">
        <AnimatePresence>
          {view.rounds.map((r) => (
            <section key={r.round}>
              <h2 className="eyebrow mb-4">
                {r.round === 7 ? "Sudden death" : `Round ${r.round}`} · {r.theme.label}
                {r.your_pick_timed_out ? " · your pick timed out" : ""}
                {r.their_pick_timed_out ? " · their pick timed out" : ""}
              </h2>
              <RevealPanel round={r} opponent={view.opponent.username} />
            </section>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

function Notice({ title, body }: { title: string; body: string }) {
  return (
    <div className="border border-line bg-surface px-6 py-10 text-center">
      <h1 className="font-display text-4xl font-black uppercase">{title}</h1>
      <p className="mt-2 text-mute">{body}</p>
      <Link to="/battles" className="btn btn-ghost mt-6">Back to battles</Link>
    </div>
  );
}
