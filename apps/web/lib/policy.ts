/** Mirror of DualHorizonGate.sol. Prefer the agent `action` field in the UI. */

export const GATE_1H = 1.0;
export const GATE_2H = 2.5;
export const GATE_8H = 2.0;

export type PolicyAction = "warmup" | "exit" | "enter" | "hold";

export function policyAction(opts: {
  ethPct1h: number;
  ethPct2h: number;
  ethPct8h: number;
  warmupComplete: boolean;
  currentlyInPool: boolean;
}): PolicyAction {
  if (!opts.warmupComplete) return "warmup";
  const mustExit = Math.abs(opts.ethPct1h) >= GATE_1H || Math.abs(opts.ethPct2h) >= GATE_2H;
  if (mustExit) return "exit";
  if (opts.currentlyInPool) return "hold";
  const canEnter =
    Math.abs(opts.ethPct1h) < GATE_1H &&
    Math.abs(opts.ethPct2h) < GATE_2H &&
    Math.abs(opts.ethPct8h) < GATE_8H;
  return canEnter ? "enter" : "exit";
}

export function hourIdFromDate(d = new Date()) {
  return Math.floor(d.getTime() / 1000 / 3600);
}

export function dateFromHourId(hourId: number) {
  return new Date(hourId * 3600 * 1000);
}
