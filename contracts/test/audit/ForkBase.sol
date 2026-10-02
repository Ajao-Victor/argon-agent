// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ArgonVault} from "../../src/ArgonVault.sol";
import {InferenceRegistry} from "../../src/InferenceRegistry.sol";
import {UniswapV3Adapter} from "../../src/adapters/UniswapV3Adapter.sol";
import {ChainlinkEthOracle} from "../../src/oracle/ChainlinkEthOracle.sol";
import {IERC20} from "../../src/interfaces/IERC20.sol";

interface IPoolV3 {
    function slot0() external view returns (uint160, int24, uint16, uint16, uint16, uint8, bool);
}

interface ISwapRouter02 {
    struct ExactInputSingleParams {
        address tokenIn; address tokenOut; uint24 fee; address recipient;
        uint256 amountIn; uint256 amountOutMinimum; uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata) external payable returns (uint256);
}

abstract contract ForkBase is Test {
    address constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    address constant USDC = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831;
    address constant NPM = 0xC36442b4a4522E871399CD717aBDD847Ab11FE88;
    address constant FEED = 0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612;
    address constant SEQ = 0xFdB631F5EE196F0ed6FAa767959853A9F217697D;
    address constant POOL = 0xC6962004f452bE9203591991D15f6b388e09E8D0;
    address constant ROUTER = 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45; // SwapRouter02 (Arbitrum)

    ArgonVault vault; InferenceRegistry reg; UniswapV3Adapter adapter; ChainlinkEthOracle oracle;
    address keeper = address(0xB0);
    bytes32 modelId = keccak256("eth-1-2-8h-v1");
    uint64 hour;

    function _setUpFork() internal {
        vm.createSelectFork("https://arb1.arbitrum.io/rpc");
        reg = new InferenceRegistry(address(this), keeper, modelId);
        oracle = new ChainlinkEthOracle(FEED, SEQ, 2 days);
        vault = new ArgonVault(address(this), keeper, address(reg), address(oracle), WETH, USDC, 6);
        adapter = new UniswapV3Adapter(address(vault), NPM, WETH, USDC, 500);
        vault.setPool(1, address(adapter), true);
        hour = uint64(block.timestamp / 3600) - 1;
        for (uint256 i; i < 9; i++) _submit(0, 0, 0);
    }

    function _submit(int256 a, int256 b, int256 c) internal {
        hour++;
        if (block.timestamp / 3600 < hour) vm.warp(uint256(hour) * 3600 + 30);
        vm.prank(keeper);
        reg.submit(hour, a, b, c, keccak256(abi.encode(hour, a, b, c, modelId)));
    }

    function _fund(address who, uint256 w, uint256 u) internal {
        deal(WETH, who, w); deal(USDC, who, u);
        vm.startPrank(who);
        IERC20(WETH).approve(address(vault), type(uint256).max);
        IERC20(USDC).approve(address(vault), type(uint256).max);
        vm.stopPrank();
    }

    function _tick() internal view returns (int24 t) { (, t,,,,,) = IPoolV3(POOL).slot0(); }

    function _range() internal view returns (int24 lo, int24 hi) {
        int24 t = _tick(); t = (t / 10) * 10; lo = t - 200; hi = t + 200;
    }

    function _swap(address tin, address tout, uint256 amt, address who) internal returns (uint256 out) {
        deal(tin, who, IERC20(tin).balanceOf(who) + amt);
        vm.startPrank(who);
        IERC20(tin).approve(ROUTER, type(uint256).max);
        out = ISwapRouter02(ROUTER).exactInputSingle(ISwapRouter02.ExactInputSingleParams(tin, tout, 500, who, amt, 0, 0));
        vm.stopPrank();
    }

    function _usd(uint256 w, uint256 u) internal view returns (uint256) {
        return w * oracle.ethUsd8() / 1e18 + u * 100;
    }
}
