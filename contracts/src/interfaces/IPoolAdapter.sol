// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IPoolAdapter {
    function vault() external view returns (address);
    function tokenA() external view returns (address);
    function tokenB() external view returns (address);
    function fee() external view returns (uint24);
    function enter(int24 tickLower, int24 tickUpper, uint256 amountAMin, uint256 amountBMin, uint256 deadline) external;
    function exit(uint256 amountAMin, uint256 amountBMin) external;
    /// [FIX M-forced-flatten] remove `num/den` of the liquidity and send the tokens to the vault.
    function exitShare(uint256 num, uint256 den) external returns (uint256 amountA, uint256 amountB);
    function harvest() external;
    function inPosition() external view returns (bool);
    function amounts() external view returns (uint256 amountA, uint256 amountB);
    /// [FIX M-nav] live token amounts of the position (liquidity + owed) at a given sqrt price.
    function valueAt(uint160 sqrtPriceX96) external view returns (uint256 amountA, uint256 amountB);
    /// [FIX H-enter-sandwich] current pool sqrtPriceX96 and tick.
    function spot() external view returns (uint160 sqrtPriceX96, int24 tick, address token0);
}
