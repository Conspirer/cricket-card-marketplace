import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { api, RARITIES, type MarketListing, type Rarity } from "../api";
import { useSession } from "../session";
import { useToast } from "../toast";
import { runs } from "../format";
import { PageHeader, RarityFilter } from "../components/Layout";
import { TradingCard } from "../components/TradingCard";
import { EmptyState } from "./CollectionPage";

export function MarketPage() {
  const [rarity, setRarity] = useState<Rarity | null>(null);
  const { data: listings = [], isLoading } = useQuery({
    queryKey: ["market", rarity],
    queryFn: () => api.market(rarity ?? undefined),
  });

  const floor = listings.length ? Math.min(...listings.map((l) => Number(l.price))) : null;

  return (
    <>
      <PageHeader eyebrow="Fixed-price listings · 5% fee burned on every sale" title="The Market">
        <dl className="flex gap-8">
          <div>
            <dt className="eyebrow !text-[10px]">Listed</dt>
            <dd className="font-display text-4xl font-black">{listings.length}</dd>
          </div>
          <div>
            <dt className="eyebrow !text-[10px]">Floor</dt>
            <dd className="font-display text-4xl font-black text-brass-bright">
              {floor === null ? "—" : runs(floor)}
            </dd>
          </div>
        </dl>
      </PageHeader>

      <div className="mb-8">
        <RarityFilter value={rarity} options={RARITIES} onChange={setRarity} />
      </div>

      {!isLoading && listings.length === 0 ? (
        <EmptyState
          title="No listings"
          body="Nothing for sale right now. List a card from your collection to get the market moving."
          cta={{ to: "/collection", label: "Go to collection" }}
        />
      ) : (
        <div className="grid grid-cols-2 gap-x-5 gap-y-12 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {listings.map((listing, i) => (
            <motion.div
              key={listing.listing_id}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(i * 0.03, 0.4) }}
            >
              <ListingTile listing={listing} />
            </motion.div>
          ))}
        </div>
      )}
    </>
  );
}

function ListingTile({ listing }: { listing: MarketListing }) {
  const { user } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const mine = user?.id === listing.seller_id;
  const affordable = user ? Number(user.balance) >= Number(listing.price) : false;

  const buy = useMutation({
    mutationFn: () => api.buy(listing.listing_id, user!.id),
    onSuccess: () => {
      toast(`You bought ${listing.player_name} #${listing.serial_number} for ${runs(listing.price)} runs`);
      queryClient.invalidateQueries();
    },
    onError: (err) => {
      toast(err.message, "error");
      setConfirming(false);
      queryClient.invalidateQueries({ queryKey: ["market"] });
    },
  });

  return (
    <div>
      <Link to={`/cards/${listing.card_instance_id}`} className="block">
        <TradingCard card={listing} size="fluid" />
      </Link>
      <div className="mt-4 flex items-end justify-between gap-2">
        <div>
          <div className="font-mono text-lg leading-none text-cream">{runs(listing.price)}</div>
          <div className="mt-1.5 truncate font-mono text-[10px] tracking-[0.12em] text-faint uppercase">
            by {mine ? "you" : listing.seller_username}
          </div>
        </div>
        {mine ? (
          <span className="font-mono text-[10px] tracking-[0.12em] text-mute uppercase">Your listing</span>
        ) : confirming ? (
          <div className="flex gap-1.5">
            <button className="btn btn-ghost !h-9 !px-3 !text-sm" onClick={() => setConfirming(false)}>
              ✕
            </button>
            <button
              className="btn btn-primary !h-9 !px-3 !text-sm"
              onClick={() => buy.mutate()}
              disabled={buy.isPending}
            >
              {buy.isPending ? "…" : "Confirm"}
            </button>
          </div>
        ) : (
          <button
            className="btn btn-primary !h-9 !px-4 !text-sm"
            onClick={() => setConfirming(true)}
            disabled={!user || !affordable}
            title={affordable ? undefined : "Not enough runs"}
          >
            Buy
          </button>
        )}
      </div>
    </div>
  );
}
