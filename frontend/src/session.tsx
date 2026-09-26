import { createContext, useContext, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type User } from "./api";

// No auth yet: the "session" is just which user you're acting as, picked in
// the header. Swap this for a real login once the backend has auth.
type Session = { user: User | null; users: User[]; setUserId: (id: number) => void };

const SessionContext = createContext<Session | null>(null);
const STORAGE_KEY = "crease:user-id";

function readStoredId() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? Number(raw) : null;
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const { data: users = [] } = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [userId, setUserIdState] = useState<number | null>(readStoredId);

  const setUserId = (id: number) => {
    setUserIdState(id);
    try {
      localStorage.setItem(STORAGE_KEY, String(id));
    } catch {
      // Storage can be unavailable (private mode); the choice just won't persist.
    }
  };

  // Fall back to the first user if nothing is stored (or the stored user is gone).
  const user = users.find((u) => u.id === userId) ?? users[0] ?? null;

  return <SessionContext.Provider value={{ user, users, setUserId }}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
