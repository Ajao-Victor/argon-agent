"""Minimal ABIs for registry, vault, adapter, Uniswap v3."""

REGISTRY_ABI = [
    {
        "inputs": [
            {"name": "hourId", "type": "uint64"},
            {"name": "pct1hBps", "type": "int256"},
            {"name": "pct2hBps", "type": "int256"},
            {"name": "pct8hBps", "type": "int256"},
            {"name": "forecastHash", "type": "bytes32"},
        ],
        "name": "submit",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "hourId", "type": "uint64"}],
        "name": "getForecast",
        "outputs": [
            {
                "components": [
                    {"name": "pct1hBps", "type": "int256"},
                    {"name": "pct2hBps", "type": "int256"},
                    {"name": "pct8hBps", "type": "int256"},
                    {"name": "forecastHash", "type": "bytes32"},
                    {"name": "submittedAt", "type": "uint64"},
                    {"name": "submitter", "type": "address"},
                ],
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestHourId",
        "outputs": [{"name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "forecastCount",
        "outputs": [{"name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "warmupComplete",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "modelId",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "hourId", "type": "uint64"},
            {"name": "pct1hBps", "type": "int256"},
            {"name": "pct2hBps", "type": "int256"},
            {"name": "pct8hBps", "type": "int256"},
        ],
        "name": "computeHash",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
]

VAULT_ABI = [
    {
        "inputs": [
            {"name": "hourId", "type": "uint64"},
            {"name": "poolId", "type": "uint8"},
            {"name": "action", "type": "uint8"},
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "amountAMin", "type": "uint256"},
            {"name": "amountBMin", "type": "uint256"},
        ],
        "name": "rebalance",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "poolId", "type": "uint8"}],
        "name": "poolStatus",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "warmupComplete",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "keeper",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

ADAPTER_ABI = [
    {
        "inputs": [],
        "name": "inPosition",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "tokenA",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "tokenB",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

NPM_ABI = [
    {
        "inputs": [],
        "name": "factory",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "fee", "type": "uint24"}],
        "name": "feeAmountTickSpacing",
        "outputs": [{"name": "", "type": "int24"}],
        "stateMutability": "view",
        "type": "function",
    },
]

POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint8"},
            {"name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]
