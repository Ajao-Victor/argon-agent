// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./utils/Ownable.sol";
import {Pausable} from "./utils/Pausable.sol";
import {ReentrancyGuard} from "./utils/ReentrancyGuard.sol";
import {SafeTransfer} from "./utils/SafeTransfer.sol";
import {FullMath} from "./utils/FullMath.sol";
import {IERC20} from "./interfaces/IERC20.sol";
import {IWETH} from "./interfaces/IWETH.sol";
import {IInferenceRegistry} from "./interfaces/IInferenceRegistry.sol";
import {IPoolAdapter} from "./interfaces/IPoolAdapter.sol";
import {IEthUsdOracle} from "./interfaces/IEthUsdOracle.sol";
import {DualHorizonGate} from "./libraries/DualHorizonGate.sol";

/// @notice AUDIT FIX SIMULATION BUILD — not the deployed contract.
contract ArgonVault is Ownable, Pausable, ReentrancyGuard {
    using SafeTransfer for address;

    enum Action {
        HOLD,
        ENTER,
        EXIT
    }

    struct Pool {
        IPoolAdapter adapter;
        bool gated;
        bool exists;
        uint64 lastExitHourId;
        uint64 lastRebalanceHourId;
    }

    IInferenceRegistry public registry;
    IEthUsdOracle public oracle;
    address public keeper;
    address public immutable weth;
    address public immutable stable;
    uint8 public immutable stableDecimals;

    uint16 public gate1hBps = 100;
    uint16 public gate2hBps = 250;
    uint16 public gate8hBps = 200;
    uint64 public constant ENTER_COOLDOWN_HOURS = 2;

    // [FIX H-inflation] virtual shares/assets; ratio equals the original 1e10 shares per usd8 unit.
    uint256 internal constant VIRTUAL_SHARES = 1e10;
    uint256 internal constant VIRTUAL_ASSETS = 1;
    // [FIX H-oracle-lag] deposit fee >= feed deviation threshold (Arb 0.05%, RH 0.5%).
    uint16 public depositFeeBps;
    uint16 public constant MAX_DEPOSIT_FEE_BPS = 200;
    // [FIX H-enter-sandwich] pool spot must sit within this band of Chainlink at ENTER.
    uint16 public maxSpotDevBps = 50;
    int24 public maxTickWidth = 2000;

    // [FIX M-one-sided] optional inventory swap before ENTER (SwapRouter02), oracle-bounded.
    address public router;

    uint256 public totalShares;
    mapping(address => uint256) public shareBalance;
    mapping(uint8 => Pool) public pools;
    uint8[] internal _poolIds; // [FIX L-hardcoded-ids]

    error NotKeeper();
    error InvalidToken();
    error ZeroAmount();
    error ZeroShares();
    error InsufficientShares();
    error UnknownPool();
    error PoolNotGated();
    error Warmup();
    error StaleHour();
    error ActionMismatch(uint8 allowed, uint8 got);
    error Cooldown();
    error AlreadyRebalanced();
    error PoolConfigured();
    error BadAdapter();
    error BadParam();
    error SpotDeviation(uint256 spotUsd8, uint256 oracleUsd8);
    error BadRange();

    event KeeperSet(address indexed keeper);
    event RegistrySet(address indexed registry);
    event OracleSet(address indexed oracle);
    event GatesSet(uint16 gate1hBps, uint16 gate2hBps, uint16 gate8hBps);
    event PoolSet(uint8 indexed poolId, address adapter, bool gated);
    event Deposited(address indexed user, address indexed token, uint256 amount, uint256 shares);
    event Withdrawn(address indexed user, address indexed token, uint256 amount, uint256 shares);
    event Rebalanced(uint64 indexed hourId, uint8 indexed poolId, Action action, bytes32 forecastHash);
    event DepositFeeSet(uint16 bps);
    event EmergencyIdleOnly(address indexed user, uint256 shares);

    modifier onlyKeeper() {
        if (msg.sender != keeper) revert NotKeeper();
        _;
    }

    constructor(
        address initialOwner,
        address keeper_,
        address registry_,
        address oracle_,
        address weth_,
        address stable_,
        uint8 stableDecimals_
    ) Ownable(initialOwner) {
        if (
            keeper_ == address(0) || registry_ == address(0) || oracle_ == address(0) || weth_ == address(0)
                || stable_ == address(0)
        ) {
            revert ZeroAddress();
        }
        if (stableDecimals_ != IERC20(stable_).decimals()) revert BadParam();
        keeper = keeper_;
        registry = IInferenceRegistry(registry_);
        oracle = IEthUsdOracle(oracle_);
        weth = weth_;
        stable = stable_;
        stableDecimals = stableDecimals_;
        emit KeeperSet(keeper_);
        emit RegistrySet(registry_);
        emit OracleSet(oracle_);
    }

    function setKeeper(address keeper_) external onlyOwner {
        if (keeper_ == address(0)) revert ZeroAddress();
        keeper = keeper_;
        emit KeeperSet(keeper_);
    }

    function setRegistry(address registry_) external onlyOwner {
        if (registry_ == address(0)) revert ZeroAddress();
        registry = IInferenceRegistry(registry_);
        emit RegistrySet(registry_);
    }

    function setOracle(address oracle_) external onlyOwner {
        if (oracle_ == address(0)) revert ZeroAddress();
        oracle = IEthUsdOracle(oracle_);
        emit OracleSet(oracle_);
    }

    function setGates(uint16 g1, uint16 g2, uint16 g8) external onlyOwner {
        if (g1 == 0 || g2 == 0 || g8 == 0 || g1 > 10_000 || g2 > 10_000 || g8 > 10_000) revert BadParam();
        gate1hBps = g1;
        gate2hBps = g2;
        gate8hBps = g8;
        emit GatesSet(g1, g2, g8);
    }

    function setDepositFeeBps(uint16 bps) external onlyOwner {
        if (bps > MAX_DEPOSIT_FEE_BPS) revert BadParam();
        depositFeeBps = bps;
        emit DepositFeeSet(bps);
    }

    function setEnterGuards(uint16 devBps, int24 width) external onlyOwner {
        if (devBps == 0 || devBps > 500 || width <= 0) revert BadParam();
        maxSpotDevBps = devBps;
        maxTickWidth = width;
    }

    function setRouter(address r) external onlyOwner {
        if (router != address(0)) {
            weth.approve(router, 0);
            stable.approve(router, 0);
        }
        router = r;
        if (r != address(0)) {
            weth.approve(r, type(uint256).max);
            stable.approve(r, type(uint256).max);
        }
    }

    function _balanceInventory(uint24 fee) internal {
        if (router == address(0)) return;
        uint256 p = oracle.ethUsd8();
        uint256 wu = _usd8(weth, IERC20(weth).balanceOf(address(this)));
        uint256 su = _usd8(stable, IERC20(stable).balanceOf(address(this)));
        uint256 total = wu + su;
        if (total == 0) return;
        bool sellWeth = wu > su;
        uint256 excessUsd8 = (sellWeth ? wu - su : su - wu) / 2;
        if (excessUsd8 * 100 < total) return; // < 1% imbalance: leave it
        address tin = sellWeth ? weth : stable;
        address tout = sellWeth ? stable : weth;
        uint256 amountIn = sellWeth ? (excessUsd8 * 1e18) / p : (excessUsd8 * (10 ** uint256(stableDecimals))) / 1e8;
        uint256 fairOut = sellWeth ? (excessUsd8 * (10 ** uint256(stableDecimals))) / 1e8 : (excessUsd8 * 1e18) / p;
        uint256 minOut = (fairOut * (10_000 - maxSpotDevBps - fee / 100)) / 10_000;
        (bool ok, bytes memory ret) = router.call(
            abi.encodeWithSelector(0x04e45aaf, tin, tout, fee, address(this), amountIn, minOut, uint160(0))
        );
        if (!ok) {
            assembly { revert(add(ret, 32), mload(ret)) }
        }
    }

    function setPaused(bool v) external onlyOwner {
        _setPaused(v);
    }

    function setPool(uint8 poolId, address adapter, bool gated) external onlyOwner {
        if (adapter == address(0)) revert ZeroAddress();
        if (pools[poolId].exists) revert PoolConfigured();
        // [FIX C-1 partial] adapter must be bound to this vault and to the vault's own two tokens.
        address a = IPoolAdapter(adapter).tokenA();
        address b = IPoolAdapter(adapter).tokenB();
        if (IPoolAdapter(adapter).vault() != address(this)) revert BadAdapter();
        if (!((a == weth && b == stable) || (a == stable && b == weth))) revert BadAdapter();
        pools[poolId] = Pool({
            adapter: IPoolAdapter(adapter),
            gated: gated,
            exists: true,
            lastExitHourId: 0,
            lastRebalanceHourId: 0
        });
        if (gated) {
            if (_poolIds.length >= 4) revert BadParam();
            _poolIds.push(poolId);
        }
        a.approve(adapter, type(uint256).max);
        b.approve(adapter, type(uint256).max);
        emit PoolSet(poolId, adapter, gated);
    }

    function warmupComplete() public view returns (bool) {
        return registry.forecastCount() >= 9;
    }

    function poolStatus(uint8 poolId) external view returns (uint8) {
        Pool storage p = pools[poolId];
        if (!p.exists) revert UnknownPool();
        return p.adapter.inPosition() ? 1 : 0;
    }

    function idleBalance(address user, address token) external view returns (uint256) {
        uint256 supply = totalShares;
        if (supply == 0) return 0;
        return (IERC20(token).balanceOf(address(this)) * shareBalance[user]) / supply;
    }

    function depositETH() external payable whenNotPaused nonReentrant {
        if (msg.value == 0) revert ZeroAmount();
        uint256 navBefore = _totalAssetsUsd8();
        IWETH(weth).deposit{value: msg.value}();
        _deposit(msg.sender, weth, msg.value, navBefore);
    }

    function deposit(address token, uint256 amount) external whenNotPaused nonReentrant {
        if (token != weth && token != stable) revert InvalidToken();
        if (amount == 0) revert ZeroAmount();
        uint256 navBefore = _totalAssetsUsd8();
        token.pull(msg.sender, amount);
        _deposit(msg.sender, token, amount, navBefore);
    }

    function _deposit(address user, address token, uint256 amount, uint256 navBefore) internal {
        oracle.assertHealthy(); // [FIX] sequencer + staleness for deposits as well
        uint256 usd8 = _usd8(token, amount);
        usd8 -= (usd8 * depositFeeBps) / 10_000;
        uint256 shares = FullMath.mulDiv(usd8, totalShares + VIRTUAL_SHARES, navBefore + VIRTUAL_ASSETS);
        if (shares == 0) revert ZeroShares();
        shareBalance[user] += shares;
        totalShares += shares;
        emit Deposited(user, token, amount, shares);
    }

    function withdraw(uint256 shares) external nonReentrant {
        _withdraw(msg.sender, shares);
    }

    /// [FIX L-emergency] if an adapter call reverts, the user may still leave with the idle share.
    function emergencyWithdraw() external nonReentrant {
        uint256 shares = shareBalance[msg.sender];
        if (shares == 0) revert ZeroShares();
        try this.selfWithdraw(msg.sender, shares) {}
        catch {
            _payIdle(msg.sender, shares, 0, 0);
            emit EmergencyIdleOnly(msg.sender, shares);
        }
    }

    function selfWithdraw(address user, uint256 shares) external {
        require(msg.sender == address(this));
        _withdraw(user, shares);
    }

    function _withdraw(address user, uint256 shares) internal {
        if (shares == 0) revert ZeroAmount();
        if (shareBalance[user] < shares) revert InsufficientShares();
        uint256 supply = totalShares;
        // [FIX M-forced-flatten] fees to idle first, then remove only this user's slice of liquidity.
        uint256 lpA_weth;
        uint256 lpB_stable;
        for (uint256 i; i < _poolIds.length; i++) {
            Pool storage p = pools[_poolIds[i]];
            if (!p.adapter.inPosition()) continue;
            if (shares == supply) {
                p.adapter.exit(0, 0);
            } else {
                p.adapter.harvest();
                (uint256 a, uint256 b) = p.adapter.exitShare(shares, supply);
                if (p.adapter.tokenA() == weth) {
                    lpA_weth += a;
                    lpB_stable += b;
                } else {
                    lpA_weth += b;
                    lpB_stable += a;
                }
            }
        }
        _payIdle(user, shares, lpA_weth, lpB_stable);
    }

    /// idle (excluding the just-removed LP slice) is split pro-rata; the LP slice goes to the user whole.
    function _payIdle(address user, uint256 shares, uint256 lpWeth, uint256 lpStable) internal {
        uint256 supply = totalShares;
        uint256 wethBal = IERC20(weth).balanceOf(address(this)) - lpWeth;
        uint256 stableBal = IERC20(stable).balanceOf(address(this)) - lpStable;
        uint256 wethOut = (wethBal * shares) / supply + lpWeth;
        uint256 stableOut = (stableBal * shares) / supply + lpStable;
        shareBalance[user] -= shares;
        totalShares = supply - shares;
        if (wethOut != 0) weth.push(user, wethOut);
        if (stableOut != 0) stable.push(user, stableOut);
        emit Withdrawn(user, weth, wethOut, shares);
        emit Withdrawn(user, stable, stableOut, shares);
    }

    function rebalance(
        uint64 hourId,
        uint8 poolId,
        Action action,
        int24 tickLower,
        int24 tickUpper,
        uint256 amountAMin,
        uint256 amountBMin
    ) external onlyKeeper whenNotPaused nonReentrant {
        Pool storage p = pools[poolId];
        if (!p.exists) revert UnknownPool();
        if (!p.gated) revert PoolNotGated();
        if (p.lastRebalanceHourId == hourId) revert AlreadyRebalanced();
        if (hourId != registry.latestHourId()) revert StaleHour();
        if (hourId != uint64(block.timestamp / 3600)) revert StaleHour(); // [FIX M-hourId]

        IInferenceRegistry.Forecast memory f = registry.getForecast(hourId);
        bool inPool = p.adapter.inPosition();
        uint8 allowed =
            DualHorizonGate.allowedAction(f.pct1hBps, f.pct2hBps, f.pct8hBps, gate1hBps, gate2hBps, gate8hBps, inPool);

        if (action == Action.HOLD && allowed == DualHorizonGate.ENTER) {} else if (uint8(action) != allowed) {
            revert ActionMismatch(allowed, uint8(action));
        }

        if (action != Action.HOLD && !warmupComplete()) revert Warmup();

        if (action == Action.ENTER) {
            if (p.lastExitHourId != 0 && hourId < p.lastExitHourId + ENTER_COOLDOWN_HOURS) revert Cooldown();
            oracle.assertHealthy();
            _checkSpot(p.adapter, tickLower, tickUpper);
            _balanceInventory(p.adapter.fee());
            p.adapter.enter(tickLower, tickUpper, amountAMin, amountBMin, block.timestamp);
        } else if (action == Action.EXIT) {
            if (inPool) {
                p.adapter.exit(amountAMin, amountBMin);
                p.lastExitHourId = hourId;
            }
        } else {
            if (inPool) p.adapter.harvest();
        }

        p.lastRebalanceHourId = hourId;
        emit Rebalanced(hourId, poolId, action, f.forecastHash);
    }

    function _checkSpot(IPoolAdapter adapter, int24 lo, int24 hi) internal view {
        (uint160 sqrtP, int24 tick, address token0) = adapter.spot();
        if (!(lo < tick && tick < hi) || hi - lo > maxTickWidth) revert BadRange();
        uint256 spotUsd8 = spotPriceUsd8(sqrtP, token0);
        uint256 o = oracle.ethUsd8();
        uint256 diff = spotUsd8 > o ? spotUsd8 - o : o - spotUsd8;
        if (diff * 10_000 > o * maxSpotDevBps) revert SpotDeviation(spotUsd8, o);
    }

    function spotPriceUsd8(uint160 sqrtP, address token0) public view returns (uint256) {
        uint256 scale = 1e26 / (10 ** uint256(stableDecimals));
        uint256 pX64 = FullMath.mulDiv(sqrtP, sqrtP, 1 << 64); // token1/token0 raw, Q64
        if (token0 == weth) return FullMath.mulDiv(pX64, scale, 1 << 128);
        return FullMath.mulDiv(1 << 128, scale, pX64);
    }

    function oracleSqrtPriceX96(address token0) public view returns (uint160) {
        uint256 o = oracle.ethUsd8();
        uint256 scale = 1e26 / (10 ** uint256(stableDecimals));
        uint256 priceX192 = token0 == weth
            ? FullMath.mulDiv(o, (10 ** uint256(stableDecimals)) << 96, 1e26) << 96
            : FullMath.mulDiv(1 << 192, scale, o);
        return uint160(_sqrt(priceX192));
    }

    function _sqrt(uint256 x) internal pure returns (uint256 z) {
        if (x == 0) return 0;
        z = x;
        uint256 y = (x >> 1) + 1;
        while (y < z) {
            z = y;
            y = (x / y + y) >> 1;
        }
    }

    function _totalAssetsUsd8() internal view returns (uint256) {
        uint256 usd = _usd8(weth, IERC20(weth).balanceOf(address(this)));
        usd += _usd8(stable, IERC20(stable).balanceOf(address(this)));
        for (uint256 i; i < _poolIds.length; i++) {
            usd += _adapterUsd8(_poolIds[i]);
        }
        return usd;
    }

    function _adapterUsd8(uint8 poolId) internal view returns (uint256) {
        Pool storage p = pools[poolId];
        if (!p.exists || !p.adapter.inPosition()) return 0;
        // [FIX M-nav] value the live position at the oracle-implied price instead of entry principal.
        (,, address token0) = p.adapter.spot();
        (uint256 a, uint256 b) = p.adapter.valueAt(oracleSqrtPriceX96(token0));
        return _usd8(p.adapter.tokenA(), a) + _usd8(p.adapter.tokenB(), b);
    }

    function _usd8(address token, uint256 amount) internal view returns (uint256) {
        if (amount == 0) return 0;
        if (token == weth) {
            return (amount * oracle.ethUsd8()) / 1e18;
        }
        if (token == stable) {
            return (amount * 1e8) / (10 ** uint256(stableDecimals));
        }
        return 0;
    }
}
