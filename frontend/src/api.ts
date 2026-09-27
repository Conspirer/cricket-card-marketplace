const BASE = import.meta.env.VITE_API_URL ?? "/api";

export type Rarity = "Common" | "Rare" | "Epic" | "Legendary";
export const RARITIES: Rarity[] = ["Common", "Rare", "Epic", "Legendary"];

export type ThemeKey = "TEST" | "ODI" | "T20I" | "IPL" | "ODI_WC" | "T20_WC" | "CT";
export type StatKey =
  | "runs" | "batting_average" | "strike_rate" | "hundreds" | "sixes"
  | "wickets" | "bowling_average" | "economy" | "catches";

export type ThemeStats = {
  theme: ThemeKey;
  label: string;
  tier: "common" | "rare";
  matches: number;
  stats: Partial<Record<StatKey, number | null>> | null; // null when unverified
  source: string;
  verified: boolean;
  as_of: string | null;
  notes: string | null;
};

export type PlayerThemeStats = { hidden: boolean; themes: ThemeStats[] };

export type User = { id: number; username: string; balance: string };

// A card as shown publicly (feed, showcases): identity only.
export type PublicCard = {
  card_instance_id: number;
  card_definition_id: number;
  serial_number: number;
  rarity: Rarity;
  max_supply: number;
  player_name: string;
  player_role: string;
  player_country: string;
};

export type FeedItem = PublicCard & { event_id: number; created_at: string; username: string };

export type Profile = {
  username: string;
  joined: string;
  collection: { total: number } & Record<Rarity, number>;
  battles: { wins: number; losses: number; draws: number };
  showcase: (PublicCard & { position: number })[];
};

export type Card = {
  id: number;
  card_definition_id: number;
  serial_number: number;
  rarity: Rarity;
  player_name: string;
  player_role: string;
  player_country: string;
  max_supply: number;
  owner_username: string;
  player_id: number;
  edition_label?: string | null; // set on SBC reward editions
  burned_at?: string | null; // destroyed in an SBC
};

export type CollectionCard = Card & {
  active_listing_id: number | null;
  listed_price: string | null;
  player_tier: Rarity;
  credits: number;
  in_battle: boolean;
};

// ---- Battles --------------------------------------------------------------

export type BattleStatus = "PENDING" | "ACTIVE" | "FINISHED" | "DECLINED" | "EXPIRED" | "FORFEIT";
export type Who = "you" | "them";

export type BattleCard = {
  card_id: number;
  name: string;
  role: string;
  country: string;
  rarity: Rarity;
  serial_number: number;
  max_supply: number;
  credits: number;
};

export type ThemeMeta = {
  key: ThemeKey;
  label: string;
  tier: "common" | "rare";
  stats: { key: StatKey; label: string; lower_wins: boolean }[];
};

export type BattleCall = {
  stat: StatKey;
  your_value: number | null;
  their_value: number | null;
  point: Who | null; // whose card won this call; null = no point
  timed_out: boolean;
};

export type BattleRound = {
  round: number;
  sudden_death: boolean;
  theme: ThemeMeta;
  your_card: BattleCard;
  their_card: BattleCard;
  your_call: BattleCall | null;  // null in sudden death
  their_call: BattleCall | null;
  sudden_death_call: Omit<BattleCall, "timed_out"> | null; // the one drawn-stat comparison
  points: { you: number; them: number };
  your_pick_timed_out: boolean;
  their_pick_timed_out: boolean;
  your_card_stats: Partial<Record<StatKey, number | null>>;
  their_card_stats: Partial<Record<StatKey, number | null>>;
};

export type BattleView = {
  id: number;
  status: BattleStatus;
  you_are: "challenger" | "opponent";
  you: string;
  opponent: { id: number; username: string };
  server_now: string;
  created_at: string;
  expires_at: string;
  current_round: number;
  phase: "CARD_PICK" | "CALL" | "REVEAL" | null;
  phase_deadline: string | null;
  score: { you: number; them: number };
  winner: Who | null;
  decided_by: "regulation" | "sudden_death" | "draw" | null; // null while playing or after a forfeit
  hand: (BattleCard & { used: boolean; available?: boolean })[]; // available: can be picked this round
  current: {
    round: number;
    sudden_death: boolean;
    theme: ThemeMeta;
    your_pick: BattleCard | null;
    their_pick_made: boolean;
    their_pick: BattleCard | null;
    your_call: StatKey | null;
    their_call_made: boolean; // never which stat, until both are in
    sudden_death_stat: { key: StatKey; label: string; lower_wins: boolean } | null; // shown before the pick
  } | null;
  rounds: BattleRound[];
};

export type BattleSummary = {
  id: number;
  status: BattleStatus;
  you_are: "challenger" | "opponent";
  opponent: string;
  current_round: number;
  phase: BattleView["phase"];
  score: { you: number; them: number };
  winner: Who | null;
  created_at: string;
  expires_at: string;
  finished_at: string | null;
};

export type MarketListing = {
  listing_id: number;
  card_instance_id: number;
  card_definition_id: number;
  serial_number: number;
  rarity: Rarity;
  player_name: string;
  player_role: string;
  player_country: string;
  max_supply: number;
  player_id: number;
  seller_id: number;
  seller_username: string;
  price: string;
  listed_at: string;
};

export type PackType = {
  pack_type: "standard" | "premium";
  price: number;
  cards: number;
  odds: Record<Rarity, number>;
};

export type PackResult = {
  pack_opening_id: number;
  pack_type: string;
  price: string;
  balance: string;
  cards: Card[];
};

export type CardEvent = {
  event_type: "MINTED" | "PULLED" | "LISTED" | "DELISTED" | "SOLD" | "BURNED" | "TRADED";
  from_username: string | null;
  to_username: string | null;
  price: string | null;
  related_listing_id: number | null;
  related_pack_opening_id: number | null;
  related_trade_id?: number | null;
  created_at: string;
};

export type Sale = { card_instance_id: number; serial_number: number; price: string; sold_at: string };

export type Listing = {
  id: number;
  card_instance_id: number;
  seller_id: number;
  price: string;
  status: string;
};

// ---- SBCs -----------------------------------------------------------------

export type SbcRewardCard = {
  edition_key: string;
  edition_label: string;
  rarity: Rarity;
  max_supply: number;
  minted_count: number;
  player_name: string;
  player_role: string;
  player_country: string;
};

export type Sbc = {
  slug: string;
  title: string;
  description: string;
  requirements: string[];
  card_count: number;
  reward: { cards: SbcRewardCard[]; runs: number };
  starts_at: string | null;
  ends_at: string | null;
  open: boolean;
  max_completions: number;
  completed: number;
};

export type SbcCheck = {
  checklist: { rule: string; ok: boolean; progress: string }[];
  blocked: Record<string, string>;
  ready: boolean;
};

export type SbcResult = { completion_id: number; burned: number[]; reward_cards: number[]; reward_runs: number };

// ---- Trades ---------------------------------------------------------------

export type TradeCard = PublicCard & { edition_label: string | null; listed: boolean; in_battle?: boolean };

export type TradeStatus = "PENDING" | "ACCEPTED" | "DECLINED" | "CANCELLED" | "EXPIRED" | "INVALID";

export type Trade = {
  id: number;
  status: TradeStatus;
  invalid_reason: string | null;
  proposer: string;
  recipient: string;
  role: "proposer" | "recipient";
  created_at: string;
  expires_at: string;
  resolved_at: string | null;
  offered: TradeCard[]; // the proposer gives these
  requested: TradeCard[]; // the recipient gives these
  problems: { permanent: boolean; message: string }[];
};

export type TradeRules = {
  min_cards_per_side: number;
  max_cards_per_side: number;
  expiry_hours: number;
  min_account_age_days: number;
  min_battles_finished: number;
  max_accepted_per_day: number;
};

export type TradeEligibility = {
  eligible: boolean;
  reasons: string[];
  joined: string;
  battles_finished: number;
  trades_today: number;
  at_daily_limit: boolean;
  rules: TradeRules;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Only declare a JSON body when there is one.
  const res = await fetch(BASE + path, {
    ...init,
    headers: init?.body ? { "content-type": "application/json", ...init.headers } : init?.headers,
  });
  const body = await res.json().catch(() => null);

  if (!res.ok) {
    // FastAPI returns a string for HTTPException and a list for validation errors.
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg: string }) => d.msg).join(", ")
          : `Request failed (${res.status})`;
    throw new ApiError(res.status, message);
  }

  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  users: () => request<User[]>("/users"),
  // The logged-in user, or null when there's no (valid) session.
  me: () =>
    request<User>("/auth/me").catch((e) => {
      if (e instanceof ApiError && e.status === 401) return null;
      throw e;
    }),
  register: (username: string, password: string) => post<User>("/auth/register", { username, password }),
  login: (username: string, password: string) => post<User>("/auth/login", { username, password }),
  logout: () => post<{ ok: boolean }>("/auth/logout"),
  feed: () => request<FeedItem[]>("/feed"),
  profile: (username: string) => request<Profile>(`/profiles/${encodeURIComponent(username)}`),
  setShowcase: (cardIds: number[]) =>
    request<PublicCard[]>("/me/showcase", { method: "PUT", body: JSON.stringify({ card_ids: cardIds }) }),
  visit: () => post<{ ok: boolean }>("/me/visit"),
  packs: () => request<PackType[]>("/packs"),
  openPack: (userId: number, packType: string) =>
    post<PackResult>("/packs/open", { user_id: userId, pack_type: packType }),
  collection: (userId: number) => request<CollectionCard[]>(`/users/${userId}/cards`),
  market: (rarity?: Rarity) =>
    request<MarketListing[]>(`/marketplace${rarity ? `?rarity=${rarity}` : ""}`),
  card: (id: number) => request<Card>(`/card-instances/${id}`),
  themeStats: (playerId: number) => request<PlayerThemeStats>(`/players/${playerId}/theme-stats`),
  history: (id: number) => request<CardEvent[]>(`/card-instances/${id}/history`),
  priceHistory: (definitionId: number) =>
    request<Sale[]>(`/card-definitions/${definitionId}/price-history`),
  createListing: (cardId: number, sellerId: number, price: string) =>
    post<Listing>("/listings", { card_instance_id: cardId, seller_id: sellerId, price }),
  cancelListing: (listingId: number, sellerId: number) =>
    post<Listing>(`/listings/${listingId}/cancel?seller_id=${sellerId}`),
  buy: (listingId: number, buyerId: number) =>
    post<Listing>(`/listings/${listingId}/buy?buyer_id=${buyerId}`),
  sbcs: () => request<Sbc[]>("/sbcs"),
  sbc: (slug: string) => request<Sbc>(`/sbcs/${encodeURIComponent(slug)}`),
  checkSbc: (slug: string, cardIds: number[]) =>
    post<SbcCheck>(`/sbcs/${encodeURIComponent(slug)}/check`, { card_ids: cardIds }),
  submitSbc: (slug: string, cardIds: number[]) =>
    post<SbcResult>(`/sbcs/${encodeURIComponent(slug)}/submit`, { card_ids: cardIds }),
  trades: () => request<{ incoming: Trade[]; outgoing: Trade[]; history: Trade[] }>("/trades"),
  tradeEligibility: () => request<TradeEligibility>("/trades/eligibility"),
  tradeableCards: (username: string) => request<TradeCard[]>(`/profiles/${encodeURIComponent(username)}/cards`),
  proposeTrade: (recipient: string, offered: number[], requested: number[]) =>
    post<Trade>("/trades", { recipient, offered_card_ids: offered, requested_card_ids: requested }),
  acceptTrade: (id: number) => post<Trade>(`/trades/${id}/accept`),
  declineTrade: (id: number) => post<Trade>(`/trades/${id}/decline`),
  cancelTrade: (id: number) => post<Trade>(`/trades/${id}/cancel`),
  battles: (userId: number) => request<BattleSummary[]>(`/users/${userId}/battles`),
  battle: (id: number, userId: number) => request<BattleView>(`/battles/${id}?user_id=${userId}`),
  challenge: (challengerId: number, opponentId: number, cardIds: number[]) =>
    post<BattleView>("/battles", { challenger_id: challengerId, opponent_id: opponentId, card_ids: cardIds }),
  accept: (id: number, userId: number, cardIds: number[]) =>
    post<BattleView>(`/battles/${id}/accept`, { user_id: userId, card_ids: cardIds }),
  decline: (id: number, userId: number) => post<BattleView>(`/battles/${id}/decline`, { user_id: userId }),
  forfeit: (id: number, userId: number) => post<BattleView>(`/battles/${id}/forfeit`, { user_id: userId }),
  pick: (id: number, userId: number, round: number, cardId: number) =>
    post<BattleView>(`/battles/${id}/pick`, { user_id: userId, round, card_id: cardId }),
  call: (id: number, userId: number, round: number, stat: StatKey) =>
    post<BattleView>(`/battles/${id}/call`, { user_id: userId, round, stat }),
};
