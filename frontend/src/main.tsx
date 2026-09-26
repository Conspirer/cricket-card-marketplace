import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import "./index.css";

import { SessionProvider } from "./session";
import { ToastProvider } from "./toast";
import { Layout } from "./components/Layout";
import { PacksPage } from "./pages/PacksPage";
import { CollectionPage } from "./pages/CollectionPage";
import { MarketPage } from "./pages/MarketPage";
import { CardPage } from "./pages/CardPage";
import { BattlesPage } from "./pages/BattlesPage";
import { BattlePage } from "./pages/BattlePage";
import { CreditsPage } from "./pages/CreditsPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <ToastProvider>
          <BrowserRouter>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<PacksPage />} />
                <Route path="collection" element={<CollectionPage />} />
                <Route path="market" element={<MarketPage />} />
                <Route path="cards/:id" element={<CardPage />} />
                <Route path="battles" element={<BattlesPage />} />
                <Route path="battles/:id" element={<BattlePage />} />
                <Route path="credits" element={<CreditsPage />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </ToastProvider>
      </SessionProvider>
    </QueryClientProvider>
  </StrictMode>,
);
