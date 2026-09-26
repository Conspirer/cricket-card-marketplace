import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { api, RARITIES, type Rarity } from "../api";
import { useSession } from "../session";
import { RARITY_COLOR, RARITY_RANK, runs } from "../format";
import { PageHeader, RarityFilter } from "../components/Layout";
import { TradingCard } from "../components/TradingCard";

type Sort = "newest" | "rarity";

export function CollectionPage() {
  const { user } = useSession();
  const [rarity, setRarity] = useState<Rarity | null>(null);
  const [sort, setSort] = useState<Sort>("rarity");

  const { data: cards = [], isLoading } = useQuery({
    queryKey: ["collection", user?.id],
    queryFn: () => api.collection(user!.id),
    enabled: !!user,
  });

  const counts = RARITIES.map((r) => [r, cards.filter((c) => c.rarity === r).length] as const);
  const visible = cards
    .filter((c) => !rarity || c.rarity === rarity)
    .sort((a, b) =>
      sort === "rarity"
        ? RARITY_RANK[b.rarity] - RARITY_RANK[a.rarity] || a.serial_number - b.serial_number
        : b.id - a.id,
    );

  return (
    <>
      <PageHeader eyebrow={user ? `${user.username} · ${cards.length} cards` : "Collection"} title="The Vault">
        <dl className="flex gap-6 sm:gap-8">
          {counts.map(([r, n]) => (
            <div key={r}>
              <dt className="eyebrow !text-[10px]" style={{ color: RARITY_COLOR[r] }}>
                {r}
              </dt>
              <dd className="font-display text-4xl font-black">{n}</dd>
            </div>
          ))}
        </dl>
      </PageHeader>

      <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <RarityFilter value={rarity} options={RARITIES} onChange={setRarity} />
        <div className="flex gap-2">
          {(["rarity", "newest"] as const).map((s) => (
            <button key={s} className="chip" data-active={sort === s} onClick={() => setSort(s)}>
              {s === "rarity" ? "Best first" : "Newest"}
            </button>
          ))}
        </div>
      </div>

      {!isLoading && visible.length === 0 ? (
        <EmptyState
          title={cards.length ? "Nothing at this rarity" : "Your vault is empty"}
          body={cards.length ? "Try another filter." : "Every collection starts with one pack."}
          cta={cards.length ? undefined : { to: "/", label: "Open a pack" }}
        />
      ) : (
        <div className="grid grid-cols-2 gap-x-5 gap-y-10 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {visible.map((card, i) => (
            <motion.div
              key={card.id}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(i * 0.03, 0.4) }}
            >
              <Link to={`/cards/${card.id}`} className="block">
                <TradingCard card={card} size="fluid" />
              </Link>
              <div className="mt-3 flex items-center justify-between font-mono text-[11px] tracking-[0.1em] uppercase">
                {card.active_listing_id ? (
                  <span className="text-brass-bright">Listed · {runs(card.listed_price!)}</span>
                ) : (
                  <span className="text-faint">In vault</span>
                )}
                <Link to={`/cards/${card.id}`} className="text-mute hover:text-cream">
                  Details →
                </Link>
              </div>
            </motion.div>
          ))}
        </div>
      )}
    </>
  );
}

export function EmptyState({
  title,
  body,
  cta,
}: {
  title: string;
  body: string;
  cta?: { to: string; label: string };
}) {
  return (
    <div className="flex flex-col items-start border border-dashed border-line-strong px-8 py-16 sm:items-center sm:text-center">
      <h2 className="font-display text-4xl font-black uppercase">{title}</h2>
      <p className="mt-2 text-mute">{body}</p>
      {cta && (
        <Link to={cta.to} className="btn btn-primary mt-6">
          {cta.label}
        </Link>
      )}
    </div>
  );
}
