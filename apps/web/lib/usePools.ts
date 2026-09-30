import { useEffect, useState } from "react";
import { getPools, POLL_MS, type LpPool, type PoolsResponse } from "./agent";

export function usePools() {
  const [data, setData] = useState<PoolsResponse | null>(null);
  const [selected, setSelected] = useState<LpPool["id"] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      getPools()
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
    const id = setInterval(tick, POLL_MS.pools);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const pools = data?.pools ?? [];
  const selectedPool = pools.find((p) => p.id === selected) ?? null;
  return { data, pools, selected, setSelected, selectedPool, error };
}
