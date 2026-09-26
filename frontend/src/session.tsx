import { createContext, useContext, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type User } from "./api";

// The logged-in user comes from the server session (an HttpOnly cookie), so
// it's the same in every tab and can't be switched from the browser.
type Session = {
  user: User | null;
  loading: boolean;
  users: User[];
  logout: () => Promise<void>;
};

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  // Keys start with "users" so invalidating ["users"] after a purchase also
  // refreshes the header balance.
  const me = useQuery({ queryKey: ["users", "me"], queryFn: api.me, staleTime: 5_000 });
  const { data: users = [] } = useQuery({ queryKey: ["users", "all"], queryFn: api.users, enabled: !!me.data });

  const logout = async () => {
    await api.logout();
    queryClient.clear();
    await queryClient.invalidateQueries();
  };

  return (
    <SessionContext.Provider value={{ user: me.data ?? null, loading: me.isLoading, users, logout }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
