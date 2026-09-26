import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type BattleSummary } from "../api";
import { useSession } from "../session";
import { useToast } from "../toast";
import { PageHeader } from "../components/Layout";
import { DeckBuilder } from "../components/DeckBuilder";
import { useCountdown } from "../useCountdown";

export function BattlesPage() {
  const { user, users } = useSession();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [opponentId, setOpponentId] = useState<number | null>(null);
  const [accepting, setAccepting] = useState<number | null>(null);

  const { data: battles = [] } = useQuery({
    queryKey: ["battles", user?.id],
    queryFn: () => api.battles(user!.id),
    enabled: !!user,
    refetchInterval: 3000,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["battles"] });
  const fail = (e: Error) => toast(e.message, "error");

  const challenge = useMutation({
    mutationFn: (cardIds: number[]) => api.challenge(user!.id, opponentId!, cardIds),
    onSuccess: () => {
      toast("Challenge sent. It expires in 5 minutes.");
      setOpponentId(null);
      refresh();
    },
    onError: fail,
  });
  const accept = useMutation({
    mutationFn: ({ id, cardIds }: { id: number; cardIds: number[] }) => api.accept(id, user!.id, cardIds),
    onSuccess: (view) => navigate(`/battles/${view.id}`),
    onError: fail,
  });
  const decline = useMutation({ mutationFn: (id: number) => api.decline(id, user!.id), onSuccess: refresh, onError: fail });

  if (!user) return null;
  const others = users.filter((u) => u.id !== user.id);
  const incoming = battles.filter((b) => b.status === "PENDING" && b.you_are === "opponent");
  const sent = battles.filter((b) => b.status === "PENDING" && b.you_are === "challenger");
  const active = battles.filter((b) => b.status === "ACTIVE");
  const done = battles.filter((b) => ["FINISHED", "FORFEIT"].includes(b.status));

  return (
    <>
      <PageHeader eyebrow="Six rounds · hidden stats · know your cricket" title="Battles">
        <p className="max-w-sm text-sm text-mute">
          Each round draws a theme. Both players secretly play a card, then the caller names a stat. Only then are the
          numbers revealed.
        </p>
      </PageHeader>

      {incoming.length > 0 && (
        <Section title="Challenges for you">
          {incoming.map((b) => (
            <div key={b.id} className="border border-brass/50 bg-brass/5 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <span className="font-display text-2xl font-black uppercase">{b.opponent}</span>
                  <span className="ml-3 font-mono text-xs text-mute">challenged you · <Expires at={b.expires_at} /></span>
                </div>
                <div className="flex gap-2">
                  <button className="btn btn-ghost !h-9" onClick={() => decline.mutate(b.id)}>Decline</button>
                  <button className="btn btn-primary !h-9" onClick={() => setAccepting(accepting === b.id ? null : b.id)}>
                    {accepting === b.id ? "Close" : "Choose deck"}
                  </button>
                </div>
              </div>
              {accepting === b.id && (
                <div className="mt-5 border-t border-line pt-5">
                  <DeckBuilder
                    userId={user.id}
                    submitLabel="Accept & play"
                    busy={accept.isPending}
                    onSubmit={(cardIds) => accept.mutate({ id: b.id, cardIds })}
                  />
                </div>
              )}
            </div>
          ))}
        </Section>
      )}

      {active.length > 0 && (
        <Section title="In progress">
          {active.map((b) => (
            <Row key={b.id} b={b} action={<Link to={`/battles/${b.id}`} className="btn btn-primary !h-9">Play</Link>} />
          ))}
        </Section>
      )}

      <Section title="New challenge">
        <div className="border border-line p-5">
          <label className="eyebrow mb-3 block" htmlFor="opponent">Opponent</label>
          <div className="mb-5 flex flex-wrap gap-2">
            {others.map((u) => (
              <button key={u.id} className="chip" data-active={opponentId === u.id} onClick={() => setOpponentId(u.id)}>
                {u.username}
              </button>
            ))}
          </div>
          {opponentId && (
            <DeckBuilder
              userId={user.id}
              submitLabel="Send challenge"
              busy={challenge.isPending}
              onSubmit={(cardIds) => challenge.mutate(cardIds)}
            />
          )}
        </div>
      </Section>

      {sent.length > 0 && (
        <Section title="Waiting for a reply">
          {sent.map((b) => (
            <Row key={b.id} b={b} action={<button className="btn btn-ghost !h-9" onClick={() => decline.mutate(b.id)}>Cancel</button>} />
          ))}
        </Section>
      )}

      {done.length > 0 && (
        <Section title="Finished">
          {done.map((b) => (
            <Row key={b.id} b={b} action={<Link to={`/battles/${b.id}`} className="btn btn-ghost !h-9">Result</Link>} />
          ))}
        </Section>
      )}
    </>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-10">
      <h2 className="eyebrow mb-4">{title}</h2>
      <div className="grid gap-3">{children}</div>
    </section>
  );
}

function Row({ b, action }: { b: BattleSummary; action: ReactNode }) {
  const outcome =
    b.status === "PENDING" ? <Expires at={b.expires_at} />
    : b.status === "ACTIVE" ? `Round ${b.current_round > 6 ? "SD" : b.current_round} · ${b.score.you}–${b.score.them}`
    : `${b.winner === "you" ? "Won" : b.winner === "them" ? "Lost" : "Drawn"} ${b.score.you}–${b.score.them}${b.status === "FORFEIT" ? " (forfeit)" : ""}`;
  const tone = b.winner === "you" ? "text-pitch" : b.winner === "them" ? "text-leather" : "text-mute";
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border border-line px-4 py-3">
      <div>
        <span className="font-mono text-xs text-faint">vs</span>{" "}
        <span className="font-display text-xl font-black uppercase">{b.opponent}</span>
        <span className={`ml-3 font-mono text-xs ${b.status === "FINISHED" || b.status === "FORFEIT" ? tone : "text-mute"}`}>{outcome}</span>
      </div>
      {action}
    </div>
  );
}

function Expires({ at }: { at: string }) {
  const left = useCountdown(at);
  if (left <= 0) return <>expired</>;
  const m = Math.floor(left / 60);
  const s = Math.floor(left % 60);
  return <>expires in {m}:{String(s).padStart(2, "0")}</>;
}
