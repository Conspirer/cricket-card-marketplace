import { useEffect, useState } from "react";

/** Seconds until `deadline`, corrected by the server clock offset when given. */
export function useCountdown(deadline: string | null, serverNow?: string, fetchedAt?: number) {
  const offset = serverNow && fetchedAt ? new Date(serverNow).getTime() - fetchedAt : 0;
  const compute = () => (deadline ? (new Date(deadline).getTime() - (Date.now() + offset)) / 1000 : 0);
  const [left, setLeft] = useState(compute);

  useEffect(() => {
    setLeft(compute());
    const id = setInterval(() => setLeft(compute()), 200);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deadline, offset]);

  return Math.max(0, left);
}
