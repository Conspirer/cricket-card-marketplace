import { PageHeader } from "../components/Layout";

const SOURCES = [
  {
    name: "Cricsheet",
    url: "https://cricsheet.org",
    use: "Ball-by-ball data for IPL, World Cup 2011, T20 World Cup 2009 and Champions Trophy 2006–2017 stats, the player register, and the Cricsheet cross-check of every format figure.",
    licence: "Open Data Commons Attribution License (ODC-BY 1.0)",
    licenceUrl: "https://opendatacommons.org/licenses/by/1-0/",
  },
  {
    name: "Wikipedia",
    url: "https://en.wikipedia.org",
    use: "Test, ODI and T20I career totals from player infoboxes, and official match counts used to check tournament coverage. Figures by Wikipedia contributors.",
    licence: "Creative Commons Attribution-ShareAlike 4.0",
    licenceUrl: "https://creativecommons.org/licenses/by-sa/4.0/",
  },
  {
    name: "Wikidata",
    url: "https://www.wikidata.org",
    use: "Player names and Wikipedia article links, matched through ESPNcricinfo player IDs.",
    licence: "CC0 1.0 (public domain)",
    licenceUrl: "https://creativecommons.org/publicdomain/zero/1.0/",
  },
];

export function CreditsPage() {
  return (
    <>
      <PageHeader eyebrow="Where the numbers come from" title="Credits" />
      <div className="grid max-w-3xl gap-6">
        {SOURCES.map((s) => (
          <section key={s.name} className="border border-line p-6">
            <h2 className="font-display text-3xl font-black uppercase">
              <a href={s.url} className="hover:text-brass-bright">{s.name}</a>
            </h2>
            <p className="mt-2 text-mute">{s.use}</p>
            <p className="mt-3 font-mono text-xs text-faint">
              Licence: <a href={s.licenceUrl} className="underline hover:text-cream">{s.licence}</a>
            </p>
          </section>
        ))}
        <p className="text-sm text-mute">
          Crease is an independent fan project. It is not affiliated with the ICC, the BCCI, the IPL or any team or player.
          Runs are a virtual currency with no cash value.
        </p>
      </div>
    </>
  );
}
