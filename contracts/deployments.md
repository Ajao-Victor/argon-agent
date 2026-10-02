# Live deployments

Redeployed 2026-10-02. The chains' deployer nonces had drifted, so addresses now differ per chain. Owner and keeper: `0x9642b6D1Db5D1A3B0A61a831099568bbCbC04D4E`.

| Contract | Arbitrum One (42161) | Robinhood Chain (4663) |
|----------|----------------------|------------------------|
| InferenceRegistry | [`0x16CFd132f8fBF67A31b207E54667C4aA4432e747`](https://arbiscan.io/address/0x16CFd132f8fBF67A31b207E54667C4aA4432e747) | [`0xacB34bE584177C44740064bE249D2fe1582eD261`](https://robinhoodchain.blockscout.com/address/0xacB34bE584177C44740064bE249D2fe1582eD261) |
| ChainlinkEthOracle | [`0xe8eA8f046152C36dC8c11a5434C31A1E2343f274`](https://arbiscan.io/address/0xe8eA8f046152C36dC8c11a5434C31A1E2343f274) | [`0x16CFd132f8fBF67A31b207E54667C4aA4432e747`](https://robinhoodchain.blockscout.com/address/0x16CFd132f8fBF67A31b207E54667C4aA4432e747) |
| ArgonVault | [`0x744e2dD4Ce32148C8a4bf65BC37824cc129086aF`](https://arbiscan.io/address/0x744e2dD4Ce32148C8a4bf65BC37824cc129086aF) | [`0xe8eA8f046152C36dC8c11a5434C31A1E2343f274`](https://robinhoodchain.blockscout.com/address/0xe8eA8f046152C36dC8c11a5434C31A1E2343f274) |
| UniswapV3Adapter | [`0x83a424953c6b7a32e9bE1d1F26a47EcFe2Ff93DC`](https://arbiscan.io/address/0x83a424953c6b7a32e9bE1d1F26a47EcFe2Ff93DC) | [`0x1D0bdfa3ab21C72cA6733d584811E79e019eB7C0`](https://robinhoodchain.blockscout.com/address/0x1D0bdfa3ab21C72cA6733d584811E79e019eB7C0) |

- Arbitrum: gated **pool 1** (WETH/USDC 500), deposit fee 10 bps, USDC/USD feed `0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3`.
- Robinhood: gated **pool 4** (WETH/USDG 500), deposit fee 60 bps.

Previous vaults (`0x9F844b4D1b28Be7413067f9d4fC08Bc276fd1C60` on both chains) are retired; they held 0 shares at redeploy.
