import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { api, RARITIES, type Card, type PackResult, type PackType } from "../api";
import { useSession } from "../session";
import { useToast } from "../toast";
import { RARITY_COLOR, RARITY_RANK, runs, serial } from "../format";
import { Pack } from "../components/Pack";
import { CardBack } from "../components/CardBack";
import { TradingCard } from "../components/TradingCard";

type Phase = "shaking" | "tearing" | "reveal";
type Stage = { pack: PackType; phase: Phase; result?: PackResult };

const MIN_SHAKE_MS = 1100;
const TEAR_MS = 750;

export function PacksPage() {
  const { user } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data: packs = [] } = useQuery({ queryKey: ["packs"], queryFn: api.packs });
  const [stage, setStage] = useState<Stage | null>(null);

  const open = useMutation({
    mutationFn: (pack: PackType) => api.openPack(user!.id, pack.pack_type),
  });

  function openPack(pack: PackType) {
    if (!user) return;
    const startedAt = Date.now();
    setStage({ pack, phase: "shaking" });

    open.mutate(pack, {
      onSuccess: (result) => {
        queryClient.invalidateQueries({ queryKey: ["users"] });
        queryClient.invalidateQueries({ queryKey: ["collection"] });
        // Hold the shake for a beat even if the API answers instantly.
        const wait = Math.max(0, MIN_SHAKE_MS - (Date.now() - startedAt));
        setTimeout(() => {
          setStage({ pack, phase: "tearing", result });
          setTimeout(() => setStage({ pack, phase: "reveal", result }), TEAR_MS);
        }, wait);
      },
      onError: (err) => {
        setStage(null);
        toast(err.message, "error");
      },
    });
  }

  const balance = user ? Number(user.balance) : 0;

  return (
    <>
      <section className="grid gap-14 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-20">
        <div className="lg:pt-6">
          <div className="eyebrow mb-5">Series One · Now open</div>
          <h1 className="font-display text-[clamp(4rem,11vw,8.5rem)] leading-[0.88] font-black uppercase">
            Rip
            <br />
            a <span className="text-brass">pack.</span>
          </h1>
          <p className="mt-8 max-w-md text-lg leading-relaxed text-mute">
            Every card is a one-of-one instance with its own serial number. Supply is capped per card. Once a
            Legendary run is gone, it's gone.
          </p>

          <dl className="mt-10 grid max-w-md grid-cols-3 border-y border-line">
            {[
              ["Rarities", "4"],
              ["Cards / pack", "3"],
              ["Market fee", "5%"],
            ].map(([label, value]) => (
              <div key={label} className="border-line py-4 not-last:border-r not-first:pl-4">
                <dt className="eyebrow !text-[10px]">{label}</dt>
                <dd className="mt-1 font-display text-3xl font-black">{value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="grid gap-10 sm:grid-cols-2">
          {packs.map((pack) => (
            <div key={pack.pack_type} className="flex flex-col">
              <motion.button
                whileHover={{ y: -8, rotate: pack.pack_type === "premium" ? 1.5 : -1.5 }}
                transition={{ type: "spring", stiffness: 260, damping: 20 }}
                onClick={() => openPack(pack)}
                disabled={!user || balance < pack.price || !!stage}
                className="mx-auto w-full max-w-[300px] cursor-pointer disabled:cursor-not-allowed"
                aria-label={`Open ${pack.pack_type} pack`}
              >
                <Pack type={pack.pack_type} price={pack.price} cards={pack.cards} />
              </motion.button>

              <div className="mx-auto mt-8 w-full max-w-[300px]">
                <OddsTable pack={pack} />
                <button
                  className="btn btn-primary mt-5 w-full"
                  onClick={() => openPack(pack)}
                  disabled={!user || balance < pack.price || !!stage}
                >
                  {balance < pack.price ? "Not enough runs" : `Open · ${runs(pack.price)} runs`}
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <AnimatePresence>
        {stage && (
          <OpeningStage
            stage={stage}
            onClose={() => setStage(null)}
            onAgain={() => openPack(stage.pack)}
            canAfford={balance >= stage.pack.price}
          />
        )}
      </AnimatePresence>
    </>
  );
}

function OddsTable({ pack }: { pack: PackType }) {
  const total = Object.values(pack.odds).reduce((a, b) => a + b, 0);
  return (
    <div>
      <div className="eyebrow mb-3 flex justify-between">
        <span>Pull odds</span>
        <span>per card</span>
      </div>
      <div className="mb-3 flex h-1.5 overflow-hidden rounded-full bg-line">
        {RARITIES.map((r) => (
          <div key={r} style={{ width: `${(pack.odds[r] / total) * 100}%`, background: RARITY_COLOR[r] }} />
        ))}
      </div>
      <ul className="grid grid-cols-2 gap-x-6 gap-y-1.5 font-mono text-xs">
        {RARITIES.map((r) => (
          <li key={r} className="flex items-center justify-between">
            <span className="flex items-center gap-2 text-mute">
              <span className="h-2 w-2 rounded-full" style={{ background: RARITY_COLOR[r] }} />
              {r}
            </span>
            <span>{Math.round((pack.odds[r] / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function OpeningStage({
  stage,
  onClose,
  onAgain,
  canAfford,
}: {
  stage: Stage;
  onClose: () => void;
  onAgain: () => void;
  canAfford: boolean;
}) {
  // Best card last, so the reveal builds.
  const cards = [...(stage.result?.cards ?? [])].sort((a, b) => RARITY_RANK[a.rarity] - RARITY_RANK[b.rarity]);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  const allRevealed = cards.length > 0 && revealed.size === cards.length;
  const best = cards[cards.length - 1];

  const reveal = (id: number) => setRevealed((s) => new Set(s).add(id));
  const revealAll = () => setRevealed(new Set(cards.map((c) => c.id)));

  function again() {
    setRevealed(new Set());
    onAgain();
  }

  return (
    <motion.div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center overflow-y-auto bg-night/95 px-4 py-10"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <div
        className="pointer-events-none absolute inset-0"
        style={{ background: "radial-gradient(600px 400px at 50% 45%, rgb(201 165 92 / 0.12), transparent 70%)" }}
      />

      {stage.phase !== "reveal" && (
        <div className="relative w-[260px]">
          {stage.phase === "shaking" ? (
            <motion.div
              animate={{ rotate: [0, -2.5, 2.5, -2, 2, 0], x: [0, -3, 3, -2, 2, 0] }}
              transition={{ duration: 0.45, repeat: Infinity, repeatDelay: 0.15 }}
            >
              <Pack type={stage.pack.pack_type} price={stage.pack.price} cards={stage.pack.cards} live />
            </motion.div>
          ) : (
            // Tear: the crimped strip flies off the top, the body drops away.
            <div className="relative">
              <motion.div
                className="absolute inset-0"
                style={{ clipPath: "inset(0 0 86% 0)" }}
                initial={{ y: 0, rotate: 0, opacity: 1 }}
                animate={{ y: -160, x: 60, rotate: 18, opacity: 0 }}
                transition={{ duration: 0.6, ease: [0.3, 0.7, 0.3, 1] }}
              >
                <Pack type={stage.pack.pack_type} price={stage.pack.price} cards={stage.pack.cards} />
              </motion.div>
              <motion.div
                style={{ clipPath: "inset(14% 0 0 0)" }}
                initial={{ y: 0, opacity: 1 }}
                animate={{ y: 260, opacity: 0 }}
                transition={{ duration: 0.6, delay: 0.12, ease: [0.5, 0, 0.75, 0] }}
              >
                <Pack type={stage.pack.pack_type} price={stage.pack.price} cards={stage.pack.cards} />
              </motion.div>
              <motion.div
                className="absolute inset-x-0 top-[12%] h-24 rounded-full bg-brass-bright blur-3xl"
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 0.6, 0] }}
                transition={{ duration: 0.7 }}
              />
            </div>
          )}
          <div className="eyebrow mt-8 text-center">
            {stage.phase === "shaking" ? "Opening…" : " "}
          </div>
        </div>
      )}

      {stage.phase === "reveal" && (
        <div className="relative flex w-full max-w-5xl flex-col items-center">
          <div className="eyebrow mb-8">
            {allRevealed ? "Pack complete" : "Tap a card to reveal"}
          </div>

          <div className="flex flex-wrap justify-center gap-5 sm:gap-8">
            {cards.map((card, i) => (
              <RevealCard
                key={card.id}
                card={card}
                index={i}
                revealed={revealed.has(card.id)}
                onReveal={() => reveal(card.id)}
              />
            ))}
          </div>

          <div className="mt-12 flex min-h-[88px] flex-col items-center gap-5">
            {allRevealed ? (
              <motion.div
                className="flex flex-col items-center gap-5"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
              >
                {best && (
                  <p className="text-center text-mute">
                    Best pull:{" "}
                    <span className="font-semibold" style={{ color: RARITY_COLOR[best.rarity] }}>
                      {best.rarity} {best.player_name}
                    </span>{" "}
                    <span className="font-mono text-sm">
                      #{serial(best.serial_number)}/{best.max_supply}
                    </span>
                  </p>
                )}
                <div className="flex flex-wrap justify-center gap-3">
                  <button className="btn btn-primary" onClick={again} disabled={!canAfford}>
                    Open another · {runs(stage.pack.price)}
                  </button>
                  <Link to="/collection" className="btn btn-ghost" onClick={onClose}>
                    View collection
                  </Link>
                  <button className="btn btn-ghost" onClick={onClose}>
                    Done
                  </button>
                </div>
              </motion.div>
            ) : (
              <button className="btn btn-ghost" onClick={revealAll}>
                Reveal all
              </button>
            )}
          </div>
        </div>
      )}
    </motion.div>
  );
}

function RevealCard({
  card,
  index,
  revealed,
  onReveal,
}: {
  card: Card;
  index: number;
  revealed: boolean;
  onReveal: () => void;
}) {
  const special = RARITY_RANK[card.rarity] >= 2;
  const hint = RARITY_RANK[card.rarity] >= 1;

  return (
    <motion.div
      className="relative w-[min(230px,42vw)]"
      initial={{ opacity: 0, y: 80, rotate: -6 + index * 6, scale: 0.9 }}
      animate={{ opacity: 1, y: 0, rotate: 0, scale: 1 }}
      transition={{ delay: 0.12 + index * 0.14, type: "spring", stiffness: 180, damping: 20 }}
    >
      {/* Pre-reveal hint: the back glows in the rarity colour, strongest for the best pulls. */}
      {hint && !revealed && (
        <motion.div
          className="absolute -inset-3 rounded-[10%] blur-2xl"
          style={{ background: RARITY_COLOR[card.rarity] }}
          animate={{ opacity: special ? [0.25, 0.55, 0.25] : [0.1, 0.22, 0.1] }}
          transition={{ duration: 1.6, repeat: Infinity }}
        />
      )}

      <button
        className="flip relative block w-full cursor-pointer"
        onClick={onReveal}
        disabled={revealed}
        aria-label={revealed ? `${card.rarity} ${card.player_name}` : "Reveal card"}
      >
        <motion.div
          className="flip__inner"
          animate={{ rotateY: revealed ? 180 : 0 }}
          whileHover={revealed ? undefined : { y: -6 }}
          transition={{ duration: 0.8, ease: [0.2, 0.8, 0.2, 1] }}
        >
          <div className="flip__face">
            <CardBack />
          </div>
          <div className="flip__face flip__front">
            <TradingCard card={card} size="fluid" tilt={revealed} />
          </div>
        </motion.div>
      </button>

      {revealed && special && <Burst color={RARITY_COLOR[card.rarity]} big={card.rarity === "Legendary"} />}
    </motion.div>
  );
}

function Burst({ color, big }: { color: string; big: boolean }) {
  const count = big ? 26 : 16;
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
      <motion.div
        className="absolute h-full w-full rounded-full blur-3xl"
        style={{ background: color }}
        initial={{ opacity: 0.7, scale: 0.4 }}
        animate={{ opacity: 0, scale: 1.4 }}
        transition={{ duration: 1.1, delay: 0.35 }}
      />
      {Array.from({ length: count }, (_, i) => {
        const angle = (i / count) * Math.PI * 2;
        const distance = (big ? 190 : 140) + (i % 3) * 30;
        return (
          <motion.span
            key={i}
            className="absolute h-1.5 w-1.5 rounded-full"
            style={{ background: color }}
            initial={{ x: 0, y: 0, opacity: 1, scale: 1 }}
            animate={{ x: Math.cos(angle) * distance, y: Math.sin(angle) * distance, opacity: 0, scale: 0.3 }}
            transition={{ duration: 1.1, delay: 0.4, ease: [0.1, 0.8, 0.3, 1] }}
          />
        );
      })}
    </div>
  );
}
