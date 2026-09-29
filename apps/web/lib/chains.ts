import { defineChain } from "viem";

export const arbitrumOne = defineChain({
  id: 42161,
  name: "Arbitrum One",
  nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  rpcUrls: {
    default: { http: [process.env.NEXT_PUBLIC_ARB_RPC ?? "https://arb1.arbitrum.io/rpc"] },
  },
  blockExplorers: {
    default: { name: "Arbiscan", url: "https://arbiscan.io" },
  },
});

export const robinhoodChain = defineChain({
  id: 4663,
  name: "Robinhood Chain",
  nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  rpcUrls: {
    default: {
      http: [process.env.NEXT_PUBLIC_RH_RPC ?? "https://rpc.mainnet.chain.robinhood.com"],
    },
  },
  blockExplorers: {
    default: { name: "Blockscout", url: "https://robinhoodchain.blockscout.com" },
  },
});

export const CONTRACTS = {
  42161: {
    registry: (process.env.NEXT_PUBLIC_REGISTRY_ARB ??
      "0xbAf00c0aCa440337d43495c7de661A0AC2E01e8f") as `0x${string}`,
    vault: (process.env.NEXT_PUBLIC_VAULT_ARB ??
      "0x9F844b4D1b28Be7413067f9d4fC08Bc276fd1C60") as `0x${string}`,
  },
  4663: {
    registry: (process.env.NEXT_PUBLIC_REGISTRY_RH ??
      "0xbAf00c0aCa440337d43495c7de661A0AC2E01e8f") as `0x${string}`,
    vault: (process.env.NEXT_PUBLIC_VAULT_RH ??
      "0x9F844b4D1b28Be7413067f9d4fC08Bc276fd1C60") as `0x${string}`,
  },
} as const;
