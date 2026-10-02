// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IPoolAdapter} from "../interfaces/IPoolAdapter.sol";
import {INonfungiblePositionManager} from "../interfaces/INonfungiblePositionManager.sol";
import {IERC20} from "../interfaces/IERC20.sol";
import {SafeTransfer} from "../utils/SafeTransfer.sol";
import {TickMath} from "../utils/TickMath.sol";
import {FullMath} from "../utils/FullMath.sol";

interface IV3Factory { function getPool(address, address, uint24) external view returns (address); }
interface IV3Pool { function slot0() external view returns (uint160, int24, uint16, uint16, uint16, uint8, bool); }
interface INpmFactory { function factory() external view returns (address); }

contract UniswapV3Adapter is IPoolAdapter {
    using SafeTransfer for address;

    address public immutable vault;
    address public immutable tokenA;
    address public immutable tokenB;
    uint24 public immutable fee;
    INonfungiblePositionManager public immutable npm;
    address public immutable pool;

    uint256 public positionId;
    uint256 public principalA;
    uint256 public principalB;

    error NotVault();
    error NotIn();
    error BadTicks();

    modifier onlyVault() {
        if (msg.sender != vault) revert NotVault();
        _;
    }

    constructor(address vault_, address npm_, address tokenA_, address tokenB_, uint24 fee_) {
        vault = vault_;
        npm = INonfungiblePositionManager(npm_);
        tokenA = tokenA_;
        tokenB = tokenB_;
        fee = fee_;
        pool = IV3Factory(INpmFactory(npm_).factory()).getPool(tokenA_, tokenB_, fee_);
        tokenA_.approve(npm_, type(uint256).max);
        tokenB_.approve(npm_, type(uint256).max);
    }

    function inPosition() public view returns (bool) {
        return positionId != 0;
    }

    function amounts() external view returns (uint256, uint256) {
        return (principalA, principalB);
    }

    function valueAt(uint160 sqrtP) external view returns (uint256 amtA, uint256 amtB) {
        if (positionId == 0) return (0, 0);
        (,,,,, int24 lo, int24 hi, uint128 liq,,, uint128 owed0, uint128 owed1) = npm.positions(positionId);
        uint160 sa = TickMath.getSqrtRatioAtTick(lo);
        uint160 sb = TickMath.getSqrtRatioAtTick(hi);
        uint256 a0;
        uint256 a1;
        if (sqrtP <= sa) {
            a0 = _amount0(sa, sb, liq);
        } else if (sqrtP < sb) {
            a0 = _amount0(sqrtP, sb, liq);
            a1 = _amount1(sa, sqrtP, liq);
        } else {
            a1 = _amount1(sa, sb, liq);
        }
        a0 += owed0;
        a1 += owed1;
        (amtA, amtB) = tokenA < tokenB ? (a0, a1) : (a1, a0);
    }

    function _amount0(uint160 sa, uint160 sb, uint128 liq) private pure returns (uint256) {
        return FullMath.mulDiv(uint256(liq) << 96, sb - sa, sb) / sa;
    }

    function _amount1(uint160 sa, uint160 sb, uint128 liq) private pure returns (uint256) {
        return FullMath.mulDiv(liq, sb - sa, 1 << 96);
    }

    function spot() external view returns (uint160 sqrtPriceX96, int24 tick, address token0) {
        (sqrtPriceX96, tick,,,,,) = IV3Pool(pool).slot0();
        token0 = tokenA < tokenB ? tokenA : tokenB;
    }

    function enter(int24 tickLower, int24 tickUpper, uint256 amountAMin, uint256 amountBMin, uint256 deadline)
        external
        onlyVault
    {
        if (tickLower >= tickUpper) revert BadTicks();
        tokenA.pull(vault, IERC20(tokenA).balanceOf(vault));
        tokenB.pull(vault, IERC20(tokenB).balanceOf(vault));
        uint256 balA = IERC20(tokenA).balanceOf(address(this));
        uint256 balB = IERC20(tokenB).balanceOf(address(this));
        if (positionId == 0) {
            positionId = _mint(tickLower, tickUpper, balA, balB, amountAMin, amountBMin, deadline);
        } else {
            _increase(balA, balB, amountAMin, amountBMin, deadline);
        }
        // [FIX H-principal] measure what the position actually absorbed BEFORE returning the dust.
        uint256 leftA = IERC20(tokenA).balanceOf(address(this));
        uint256 leftB = IERC20(tokenB).balanceOf(address(this));
        _returnDust();
        principalA += balA - leftA;
        principalB += balB - leftB;
    }

    function _mint(int24 tickLower, int24 tickUpper, uint256 balA, uint256 balB, uint256 minA, uint256 minB, uint256 dl)
        internal
        returns (uint256 id)
    {
        (address token0, address token1, uint256 amount0, uint256 amount1, uint256 min0, uint256 min1) =
            _sort(balA, balB, minA, minB);
        (id,,,) = npm.mint(
            INonfungiblePositionManager.MintParams({
                token0: token0,
                token1: token1,
                fee: fee,
                tickLower: tickLower,
                tickUpper: tickUpper,
                amount0Desired: amount0,
                amount1Desired: amount1,
                amount0Min: min0,
                amount1Min: min1,
                recipient: address(this),
                deadline: dl
            })
        );
    }

    function _increase(uint256 balA, uint256 balB, uint256 minA, uint256 minB, uint256 dl) internal {
        (,, uint256 amount0, uint256 amount1, uint256 min0, uint256 min1) = _sort(balA, balB, minA, minB);
        npm.increaseLiquidity(
            INonfungiblePositionManager.IncreaseLiquidityParams({
                tokenId: positionId,
                amount0Desired: amount0,
                amount1Desired: amount1,
                amount0Min: min0,
                amount1Min: min1,
                deadline: dl
            })
        );
    }

    function exit(uint256 amountAMin, uint256 amountBMin) external onlyVault {
        if (positionId == 0) revert NotIn();
        (,,,,,,, uint128 liquidity,,,,) = npm.positions(positionId);
        if (liquidity != 0) {
            (uint256 min0, uint256 min1) = _mins(amountAMin, amountBMin);
            npm.decreaseLiquidity(
                INonfungiblePositionManager.DecreaseLiquidityParams({
                    tokenId: positionId,
                    liquidity: liquidity,
                    amount0Min: min0,
                    amount1Min: min1,
                    deadline: block.timestamp
                })
            );
        }
        npm.collect(
            INonfungiblePositionManager.CollectParams({
                tokenId: positionId,
                recipient: address(this),
                amount0Max: type(uint128).max,
                amount1Max: type(uint128).max
            })
        );
        npm.burn(positionId);
        positionId = 0;
        principalA = 0;
        principalB = 0;
        _pushAll(vault);
    }

    /// [FIX M-forced-flatten] proportional removal; fees must be harvested by the vault first.
    function exitShare(uint256 num, uint256 den) external onlyVault returns (uint256 outA, uint256 outB) {
        if (positionId == 0) revert NotIn();
        (,,,,,,, uint128 liquidity,,,,) = npm.positions(positionId);
        uint128 part = uint128((uint256(liquidity) * num) / den);
        if (part != 0) {
            npm.decreaseLiquidity(
                INonfungiblePositionManager.DecreaseLiquidityParams({
                    tokenId: positionId, liquidity: part, amount0Min: 0, amount1Min: 0, deadline: block.timestamp
                })
            );
        }
        (uint256 c0, uint256 c1) = npm.collect(
            INonfungiblePositionManager.CollectParams({
                tokenId: positionId, recipient: vault, amount0Max: type(uint128).max, amount1Max: type(uint128).max
            })
        );
        (outA, outB) = tokenA < tokenB ? (c0, c1) : (c1, c0);
        principalA -= (principalA * num) / den;
        principalB -= (principalB * num) / den;
    }

    function harvest() external onlyVault {
        if (positionId == 0) revert NotIn();
        // NPM.collect pokes the pool (burn 0) itself, so accrued fees are included.
        npm.collect(
            INonfungiblePositionManager.CollectParams({
                tokenId: positionId,
                recipient: vault,
                amount0Max: type(uint128).max,
                amount1Max: type(uint128).max
            })
        );
    }

    function _sort(uint256 balA, uint256 balB, uint256 minA, uint256 minB)
        internal
        view
        returns (address token0, address token1, uint256 amount0, uint256 amount1, uint256 min0, uint256 min1)
    {
        if (tokenA < tokenB) {
            return (tokenA, tokenB, balA, balB, minA, minB);
        }
        return (tokenB, tokenA, balB, balA, minB, minA);
    }

    function _mins(uint256 minA, uint256 minB) internal view returns (uint256 min0, uint256 min1) {
        if (tokenA < tokenB) return (minA, minB);
        return (minB, minA);
    }

    function _returnDust() internal {
        uint256 a = IERC20(tokenA).balanceOf(address(this));
        uint256 b = IERC20(tokenB).balanceOf(address(this));
        if (a != 0) tokenA.push(vault, a);
        if (b != 0) tokenB.push(vault, b);
    }

    function _pushAll(address to) internal {
        uint256 a = IERC20(tokenA).balanceOf(address(this));
        uint256 b = IERC20(tokenB).balanceOf(address(this));
        if (a != 0) tokenA.push(to, a);
        if (b != 0) tokenB.push(to, b);
    }
}
