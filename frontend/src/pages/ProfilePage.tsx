import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, RARITIES, type PublicCard } from "../api";
import { useSession } from "../session";
import { useToast } from "../toast";
import { longDate, RARITY_COLOR } from "../format";
import { PageHeader } from "../components/Layout";
import { TradingCard, type CardFace } from "../components/TradingCard";

const SHOWCASE_SIZE = 5;

const face = (c: PublicCard): CardFace => ({
  player_name: c.player_name,
  player_role: c.player_role,
  player_country: c.player_country,
  rarity: c.rarity,
  serial_number: c.serial_number,
  max_supply: c.max_supply,
});

export function ProfilePage() {
  const username = useParams().username ?? "";
  const { user } = useSession();
  const { data: profile, error } = useQuery({ queryKey: ["profile", username.toLowerCase()], queryFn: () => api.profile(username) });
  const [editing, setEditing] = useState(false);

  if (error) {
    return (
      <div className="py-16">
        <h1 className="font-display text-6xl font-black uppercase">No such player</h1>
        <Link to="/" className="btn btn-ghost mt-8">Back to packs</Link>
      </div>
    );
  }
  if (!profile) return null;

  const mine = user?.username.toLowerCase() === profile.username.toLowerCase();
  const { wins, losses, draws } = profile.battles;

  return (
    <>
      <PageHeader eyebrow={`Joined ${longDate(profile.joined.slice(0, 10))}`} title={profile.username}>
        <dl className="flex flex-wrap gap-6 sm:gap-8">
          <div>
            <dt className="eyebrow !text-[10px]">Cards</dt>
            <dd className="font-display text-4xl font-black">{profile.collection.total}</dd>
          </div>
          {RARITIES.map((r) => (
            <div key={r}>
              <dt className="eyebrow !text-[10px]" style={{ color: RARITY_COLOR[r] }}>{r}</dt>
              <dd className="font-display text-4xl font-black">{profile.collection[r]}</dd>
            </div>
          ))}
          <div className="border-l border-line pl-6 sm:pl-8">
            <dt className="eyebrow !text-[10px]">Battles W–L–D</dt>
            <dd className="font-display text-4xl font-black tabular-nums">{wins}–{losses}–{draws}</dd>
          </div>
        </dl>
      </PageHeader>

      {!mine && user && (
        <div className="-mt-4 mb-10 flex flex-wrap items-center justify-between gap-4 border border-line bg-surface px-5 py-4">
          <p className="text-mute">Want something {profile.username} has? Offer them cards for it.</p>
          <Link to={`/trades/new/${encodeURIComponent(profile.username)}`} className="btn btn-primary">
            Propose a trade
          </Link>
        </div>
      )}

      <section>
        <div className="mb-5 flex items-baseline justify-between gap-4">
          <h2 className="eyebrow">Showcase</h2>
          {mine && !editing && (
            <button className="btn btn-ghost !h-9" onClick={() => setEditing(true)}>Edit showcase</button>
          )}
        </div>

        {editing ? (
          <ShowcaseEditor current={profile.showcase.map((c) => c.card_instance_id)} onDone={() => setEditing(false)} />
        ) : profile.showcase.length === 0 ? (
          <p className="border border-dashed border-line-strong px-6 py-10 text-center text-mute">
            {mine ? "Pin up to 5 of your cards to show them off here." : `${profile.username} hasn't pinned any cards yet.`}
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-5">
            {profile.showcase.map((c) => (
              <Link key={c.card_instance_id} to={`/cards/${c.card_instance_id}`} className="block">
                <TradingCard card={face(c)} size="fluid" />
              </Link>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

function ShowcaseEditor({ current, onDone }: { current: number[]; onDone: () => void }) {
  const { user } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [picked, setPicked] = useState<number[]>(current);
  const { data: cards = [] } = useQuery({ queryKey: ["collection", user?.id], queryFn: () => api.collection(user!.id), enabled: !!user });

  const save = useMutation({
    mutationFn: () => api.setShowcase(picked),
    onSuccess: () => {
      toast("Showcase saved");
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      onDone();
    },
    onError: (e: Error) => toast(e.message, "error"),
  });

  const toggle = (id: number) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < SHOWCASE_SIZE ? [...p, id] : p));

  return (
    <div className="border border-line p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-mute">Pick up to {SHOWCASE_SIZE}, in the order you want them shown. {picked.length}/{SHOWCASE_SIZE} pinned.</p>
        <div className="flex gap-2">
          <button className="btn btn-ghost !h-9" onClick={onDone}>Cancel</button>
          <button className="btn btn-primary !h-9" onClick={() => save.mutate()} disabled={save.isPending}>Save showcase</button>
        </div>
      </div>
      <div className="grid max-h-[560px] grid-cols-3 gap-4 overflow-y-auto pr-1 sm:grid-cols-4 lg:grid-cols-6">
        {cards.map((c) => {
          const position = picked.indexOf(c.id);
          const blocked = position < 0 && picked.length >= SHOWCASE_SIZE;
          return (
            <button
              key={c.id}
              onClick={() => toggle(c.id)}
              disabled={blocked}
              className={`relative text-left transition-opacity disabled:opacity-30 ${position >= 0 ? "" : "opacity-70 hover:opacity-100"}`}
              aria-pressed={position >= 0}
              aria-label={`${position >= 0 ? "Unpin" : "Pin"} ${c.player_name}`}
            >
              <TradingCard card={c} size="fluid" tilt={false} />
              {position >= 0 && (
                <span className="absolute -top-2 -right-2 flex h-7 w-7 items-center justify-center rounded-full bg-brass font-mono text-xs text-night">
                  {position + 1}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
