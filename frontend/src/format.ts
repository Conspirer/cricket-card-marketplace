import type { Rarity, StatKey } from "./api";

export function runs(value: string | number) {
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function serial(n: number) {
  return String(n).padStart(3, "0");
}

const COUNTRY_CODES: Record<string, string> = {
  India: "IND",
  Australia: "AUS",
  England: "ENG",
  Pakistan: "PAK",
  "South Africa": "RSA",
  "New Zealand": "NZ",
  "Sri Lanka": "SL",
  "West Indies": "WI",
  Bangladesh: "BAN",
  Afghanistan: "AFG",
};

export function countryCode(country: string) {
  return COUNTRY_CODES[country] ?? country.slice(0, 3).toUpperCase();
}

export const RARITY_RANK: Record<Rarity, number> = { Common: 0, Rare: 1, Epic: 2, Legendary: 3 };

export const RARITY_COLOR: Record<Rarity, string> = {
  Common: "var(--color-common)",
  Rare: "var(--color-rare)",
  Epic: "var(--color-epic)",
  Legendary: "var(--color-legendary)",
};

export function shortDate(iso: string) {
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}


export const STAT_LABELS: Record<StatKey, string> = {
  runs: "Runs",
  batting_average: "Bat avg",
  strike_rate: "Strike rate",
  hundreds: "100s",
  sixes: "Sixes",
  wickets: "Wickets",
  bowling_average: "Bowl avg",
  economy: "Economy",
  catches: "Catches",
};

// Lower wins for these; everything else, higher wins.
export const LOWER_WINS: StatKey[] = ["bowling_average", "economy"];

export function statValue(stat: StatKey, value: number | null | undefined) {
  if (value == null) return "–";
  const decimals = ["batting_average", "strike_rate", "bowling_average", "economy"].includes(stat) ? 2 : 0;
  return value.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function longDate(iso: string) {
  return new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
