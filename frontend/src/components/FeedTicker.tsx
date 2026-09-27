import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type FeedItem } from "../api";
import { RARITY_COLOR } from "../format";
import { UserLink } from "./UserLink";

const POLL_MS = 10_000;

function lastName(name: string) {
  const words = name.trim().split(/\s+/);
  return words.length > 1 ? words.slice(1).join(" ") : name;
}

function Item({ item }: { item: FeedItem }) {
  const special = item.serial_number === 1 ? "First print" : item.serial_number === item.max_supply ? "Last print" : null;
  return (
    <li className="flex shrink-0 items-center gap-2 font-mono text-[11px] whitespace-nowrap">
      <UserLink name={item.username} className="text-cream" />
      <span className="text-faint">pulled</span>
      <Link to={`/cards/${item.card_instance_id}`} className="group flex items-center gap-2 hover:text-cream">
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: RARITY_COLOR[item.rarity] }} />
        <span style={{ color: RARITY_COLOR[item.rarity] }}>{item.rarity}</span>
        <span className="text-cream group-hover:underline">{lastName(item.player_name)}</span>
        <span className="text-mute">#{item.serial_number}/{item.max_supply}</span>
        {special && <span className="border border-brass/50 px-1 text-[9px] tracking-[0.12em] text-brass-bright uppercase">{special}</span>}
      </Link>
    </li>
  );
}

/** Recent notable pulls, scrolling under the header. Pauses on hover. */
export function FeedTicker() {
  const { data: items = [] } = useQuery({ queryKey: ["feed"], queryFn: api.feed, refetchInterval: POLL_MS });
  if (items.length === 0) return null;
  return (
    <div className="ticker border-b border-line bg-surface/60" aria-label="Recent pulls">
      <div className="mx-auto flex max-w-7xl items-center gap-4 overflow-hidden px-4 py-2 sm:px-6">
        <span className="eyebrow shrink-0 !text-[10px] text-brass-bright">Live pulls</span>
        <div className="ticker__viewport relative min-w-0 flex-1 overflow-hidden">
          {/* Two copies so the loop is seamless. */}
          <div className="ticker__track flex w-max gap-8">
            <ul className="flex gap-8">{items.map((i) => <Item key={i.event_id} item={i} />)}</ul>
            <ul className="flex gap-8" aria-hidden>{items.map((i) => <Item key={`b${i.event_id}`} item={i} />)}</ul>
          </div>
        </div>
      </div>
    </div>
  );
}
