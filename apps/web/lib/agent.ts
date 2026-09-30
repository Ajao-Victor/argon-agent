/** Types + REST client the Vercel app uses. Point NEXT_PUBLIC_AGENT_URL at Heroku. */

export type PolicyAction = "warmup" | "exit" | "enter" | "hold";

export type Forecast = {
  hourId: number;
  targetHourId: number;
  submittedAt: string;
  ethPct1h: number;
  ethPct2h: number;
  ethPct8h: number;
  ethPct1hSource: "lgbm" | "persistence";
  ethPct2hSource: "lgbm" | "persistence";
  ethPct8hSource: "lgbm" | "persistence";
  spotUsd: number | null;
  modelId: string;
  status: "pending" | "matured";
  realizedPctChange: number | null;
  realizedSpotUsd: number | null;
  action: PolicyAction;
  gate1hBps: number;
  gate2hBps: number;
  gate8hBps: number;
  warmupComplete: boolean;
  forecastHash: string | null;
  txHash: string | null;
  txHashRh: string | null;
  rebalanceTx: string | null;
  rebalanceTxRh: string | null;
  poolStatusArb: 0 | 1 | null;
  poolStatusRh: 0 | 1 | null;
  trippedHorizons: Array<"1h" | "2h" | "8h">;
};

export type AgentStatus = {
  ok: boolean;
  warmupComplete: boolean;
  hoursUntilFirstDecision: number;
  gate1hBps: number;
  gate2hBps: number;
  gate8hBps: number;
  lastHourId: number | null;
  currentHourId: number;
  modelId: string;
  modelLoaded: boolean;
};

export type ChainPortfolio = {
  name: string;
  chainId: number;
  vault: string;
  poolId: number;
  pair: string;
  inPool: boolean;
  ethUsd: number;
  totalShares: string;
  tvlUsd: number;
  shares: string;
  shareUsd: number;
  idleWeth: string;
  idleStable: string;
  idleWethFormatted: number;
  idleStableFormatted: number;
  walletWeth: string;
  walletStable: string;
  walletWethFormatted: number;
  walletStableFormatted: number;
  stableSymbol: string;
  stableDecimals: number;
  error?: string;
};

export type Portfolio = {
  address: string;
  updatedAt: string;
  pollSeconds: number;
  totalUsd: number;
  chains: {
    arbitrum?: ChainPortfolio;
    robinhood?: ChainPortfolio;
  };
  forecast: Forecast | null;
};

/** Polling cadence for a live dashboard. */
export const POLL_MS = {
  portfolio: 10_000,
  forecast: 30_000,
  status: 30_000,
  pools: 60_000,
} as const;

const BASE = process.env.NEXT_PUBLIC_AGENT_URL ?? "";

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  if (!BASE) {
    throw new Error("NEXT_PUBLIC_AGENT_URL is not set");
  }
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    next: init?.next ?? { revalidate: 30 },
  });
  if (!res.ok) {
    throw new Error(`agent ${res.status} ${path}`);
  }
  return res.json() as Promise<T>;
}

export function getLatestForecast() {
  return getJson<Forecast>("/forecasts/latest");
}

export function getForecasts(limit = 24) {
  return getJson<{ items: Forecast[] }>(`/forecasts?limit=${limit}`);
}

export function getForecast(hourId: number) {
  return getJson<Forecast>(`/forecasts/${hourId}`);
}

export function getAgentStatus() {
  return getJson<AgentStatus>("/status");
}

export function getHealth() {
  return getJson<{ ok: boolean; modelLoaded: boolean }>("/health");
}

export function getVault() {
  return getJson<Omit<Portfolio, "address" | "forecast">>("/vault");
}

/** Near-real-time user equity. Call on an interval of POLL_MS.portfolio. */
export function getPortfolio(address: string) {
  return getJson<Portfolio>(`/portfolio/${address}`, {
    cache: "no-store",
    next: { revalidate: 0 },
  });
}

export type LpPool = {
  id: "arbitrum" | "robinhood";
  chainId: number;
  poolId: number;
  pair: string;
  feePercent: number;
  uniswapFee: number;
  vault: string;
  pool: string;
  weth: string;
  stable: string;
  stableSymbol: string;
  inPool: boolean;
  poolTvlUsd: number;
  ethUsd: number;
  selectable: boolean;
  depositHint: string;
  aprPct: number | null;
  aprBasePct: number | null;
  aprSource: "defillama" | "unavailable";
  llamaTvlUsd: number | null;
  volumeUsd1d: number | null;
  error?: string;
};

export type PoolsResponse = {
  updatedAt: string;
  pollSeconds: number;
  selectOneChain: true;
  pools: LpPool[];
};

export function getPools() {
  return getJson<PoolsResponse>("/pools", { next: { revalidate: 60 } });
}
