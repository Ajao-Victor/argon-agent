// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IEthUsdOracle} from "../interfaces/IEthUsdOracle.sol";

contract MockOracle is IEthUsdOracle {
    uint256 public price = 2000e8;
    bool public healthy = true;

    error Unhealthy();

    function set(uint256 p) external {
        price = p;
    }

    function setHealthy(bool v) external {
        healthy = v;
    }

    function ethUsd8() external view returns (uint256) {
        return price;
    }

    function stableUsd8() external view returns (uint256) {
        return 1e8;
    }

    function assertHealthy() external view {
        if (!healthy) revert Unhealthy();
    }
}
