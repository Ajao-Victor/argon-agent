/** Drop into a Next.js client component. Polls Heroku for live vault USD. */

import { useEffect, useState } from "react";
import { getPortfolio, POLL_MS, type Portfolio } from "./agent";

export function usePortfolio(address: string | undefined) {
  const [data, setData] = useState<Portfolio | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!address) {
      setData(null);
      return;
    }
    let cancelled = false;
    const tick = () => {
      getPortfolio(address)
        .then((row) => {
          if (!cancelled) {
            setData(row);
            setError(null);
          }
        })
        .catch((err: Error) => {
          if (!cancelled) setError(err.message);
        });
    };
    tick();
    const id = setInterval(tick, POLL_MS.portfolio);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [address]);

  return { data, error, totalUsd: data?.totalUsd ?? 0 };
}
