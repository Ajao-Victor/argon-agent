// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "./ForkBase.sol";
import {IPoolAdapter} from "../../src/interfaces/IPoolAdapter.sol";

contract EvilAdapter {
    address public vault = address(1); address public tokenA; address public tokenB;
    constructor(address a, address b) { tokenA = a; tokenB = b; }
}

contract FixValidate is ForkBase {
    address alice = address(0xA11CE); address bob = address(0xB0B); address eve = address(0xE7E);
    function setUp() public { _setUpFork(); }

    function _val(address who, uint256 w0, uint256 u0) internal view returns (uint256) {
        return _usd(IERC20(WETH).balanceOf(who) - w0, IERC20(USDC).balanceOf(who) - u0);
    }

    // FIX principal: book NAV == true NAV after ENTER; in-position depositor made whole
    function test_fix_principal() public {
        _fund(alice, 10 ether, 200_000e6);
        vm.startPrank(alice); vault.deposit(USDC, 200_000e6); vault.deposit(WETH, 10 ether); vm.stopPrank();
        uint256 navTrue = _usd(10 ether, 200_000e6);
        _submit(10, 20, 30);
        (int24 lo, int24 hi) = _range();
        vm.prank(keeper); vault.rebalance(hour, 1, ArgonVault.Action.ENTER, lo, hi, 0, 0);
        (uint256 pa, uint256 pb) = adapter.amounts();
        uint256 navBook = _usd(IERC20(WETH).balanceOf(address(vault)) + pa, IERC20(USDC).balanceOf(address(vault)) + pb);
        emit log_named_uint("true NAV", navTrue); emit log_named_uint("book NAV (fixed)", navBook);
        assertApproxEqRel(navBook, navTrue, 1e14); // 0.01%
        _fund(bob, 0, 100_000e6);
        vm.prank(bob); vault.deposit(USDC, 100_000e6);
        uint256 w0 = IERC20(WETH).balanceOf(bob); uint256 u0 = IERC20(USDC).balanceOf(bob);
        { uint256 _s = vault.shareBalance(bob); vm.prank(bob); vault.withdraw(_s); }
        uint256 got = _val(bob, w0, u0);
        emit log_named_uint("bob out usd8 (in 1e13)", got);
        assertApproxEqRel(got, 100_000e6 * 100, 2e15); // within 0.2%
        assertEq(vault.poolStatus(1), 1, "alice still in pool after bob leaves");
    }

    // FIX inflation: donation attack is a loss for the attacker
    function test_fix_inflation() public {
        _fund(eve, 0, 1_000_000e6);
        vm.startPrank(eve);
        vault.deposit(USDC, 1);
        vault.withdraw(vault.shareBalance(eve) - 1);
        IERC20(USDC).transfer(address(vault), 50_000e6);
        vm.stopPrank();
        _fund(alice, 0, 99_000e6);
        vm.prank(alice); vault.deposit(USDC, 99_000e6);
        uint256 a0 = IERC20(USDC).balanceOf(alice);
        { uint256 _s = vault.shareBalance(alice); vm.prank(alice); vault.withdraw(_s); }
        emit log_named_uint("alice gets back of 99k", IERC20(USDC).balanceOf(alice) - a0);
        assertGe(IERC20(USDC).balanceOf(alice) - a0, 99_000e6 - 1);
        _fund(bob, 0, 40_000e6);
        vm.prank(bob); vault.deposit(USDC, 40_000e6); // no ZeroShares DoS
        assertGt(vault.shareBalance(bob), 1e12);
    }

    // FIX oracle lag: fee >= deviation makes the round trip unprofitable
    function test_fix_oracleLag() public {
        vault.setDepositFeeBps(10); // Arbitrum: feed deviation 0.05%
        _fund(alice, 50 ether, 150_000e6);
        vm.startPrank(alice); vault.deposit(USDC, 150_000e6); vault.deposit(WETH, 50 ether); vm.stopPrank();
        uint256 market = oracle.ethUsd8() * 9995 / 10000; // 0.05% lag (max on Arb)
        _fund(eve, 2_000 ether, 0);
        uint256 w0 = IERC20(WETH).balanceOf(eve);
        vm.startPrank(eve); vault.deposit(WETH, 2_000 ether); vault.withdraw(vault.shareBalance(eve)); vm.stopPrank();
        int256 pnl = int256((IERC20(WETH).balanceOf(eve) * market / 1e18) + IERC20(USDC).balanceOf(eve) * 100) - int256(w0 * market / 1e18);
        emit log_named_int("eve PnL usd8 with fee", pnl);
        assertLt(pnl, 0);
    }

    // FIX forced flatten: a 1-share withdraw no longer unwinds everyone
    function test_fix_partialWithdraw() public {
        _fund(alice, 10 ether, 30_000e6);
        vm.startPrank(alice); vault.deposit(USDC, 30_000e6); vault.deposit(WETH, 10 ether); vm.stopPrank();
        _fund(bob, 5 ether, 10_000e6);
        vm.startPrank(bob); vault.deposit(USDC, 10_000e6); vault.deposit(WETH, 5 ether); vm.stopPrank();
        _fund(eve, 0, 1);
        vm.prank(eve); vault.deposit(USDC, 1);
        uint256 nav0 = _usd(15 ether, 40_000e6);
        _submit(10, 20, 30);
        (int24 lo, int24 hi) = _range();
        vm.prank(keeper); vault.rebalance(hour, 1, ArgonVault.Action.ENTER, lo, hi, 0, 0);
        vm.prank(eve); vault.withdraw(1);
        assertEq(vault.poolStatus(1), 1, "position survives dust withdraw");
        // bob withdraws half, then all; alice is last and fully exits
        uint256 w0 = IERC20(WETH).balanceOf(bob); uint256 u0 = IERC20(USDC).balanceOf(bob);
        uint256 bs = vault.shareBalance(bob);
        vm.prank(bob); vault.withdraw(bs / 2);
        { uint256 _s = vault.shareBalance(bob); vm.prank(bob); vault.withdraw(_s); }
        uint256 bobOut = _val(bob, w0, u0);
        { uint256 _e = vault.shareBalance(eve); vm.prank(eve); vault.withdraw(_e); }
        assertEq(vault.poolStatus(1), 1, "still in pool while alice holds shares");
        w0 = IERC20(WETH).balanceOf(alice); u0 = IERC20(USDC).balanceOf(alice);
        { uint256 _s = vault.shareBalance(alice); vm.prank(alice); vault.withdraw(_s); }
        uint256 aliceOut = _val(alice, w0, u0);
        emit log_named_uint("bob in", _usd(5 ether, 10_000e6)); emit log_named_uint("bob out", bobOut);
        emit log_named_uint("alice in", _usd(10 ether, 30_000e6)); emit log_named_uint("alice out", aliceOut);
        assertApproxEqRel(bobOut, _usd(5 ether, 10_000e6), 1e15);
        assertApproxEqRel(aliceOut, _usd(10 ether, 30_000e6), 1e15);
        assertApproxEqRel(bobOut + aliceOut, nav0, 1e15);
        assertEq(vault.totalShares(), 0);
        assertEq(vault.poolStatus(1), 0);
        assertLe(IERC20(USDC).balanceOf(address(vault)), 10); // only rounding dust left
    }

    // FIX sandwich: manipulated pool -> ENTER reverts; clean pool -> ENTER works
    function test_fix_enterGuard() public {
        _fund(alice, 30 ether, 90_000e6);
        vm.startPrank(alice); vault.deposit(USDC, 90_000e6); vault.deposit(WETH, 30 ether); vm.stopPrank();
        _submit(10, 20, 30);
        address mev = address(0x3E3);
        uint256 snap = vm.snapshotState();
        _swap(USDC, WETH, 6_000_000e6, mev);
        (int24 lo, int24 hi) = _range();
        vm.prank(keeper);
        vm.expectRevert(); // SpotDeviation
        vault.rebalance(hour, 1, ArgonVault.Action.ENTER, lo, hi, 0, 0);
        vm.revertToState(snap);
        (lo, hi) = _range();
        uint256 spot = 0; (uint160 s,, address t0) = IPoolAdapter(address(adapter)).spot(); spot = vault.spotPriceUsd8(s, t0);
        emit log_named_uint("spot usd8", spot); emit log_named_uint("oracle usd8", oracle.ethUsd8());
        vm.prank(keeper); vault.rebalance(hour, 1, ArgonVault.Action.ENTER, lo, hi, 0, 0);
        assertEq(vault.poolStatus(1), 1);
        // keeper cannot pick a far-away range either
        _submit(300, 300, 300);
        vm.prank(keeper); vault.rebalance(hour, 1, ArgonVault.Action.EXIT, 0, 0, 0, 0);
    }

    function test_fix_hourBinding_and_setPool() public {
        vm.prank(keeper);
        vm.expectRevert();
        reg.submit(hour + 50, 1, 1, 1, keccak256(abi.encode(hour + 50, int256(1), int256(1), int256(1), modelId)));
        EvilAdapter evil = new EvilAdapter(WETH, USDC);
        vm.expectRevert(ArgonVault.BadAdapter.selector);
        vault.setPool(7, address(evil), false);
        vm.expectRevert();
        vault.setOracle(address(0));
    }

    function test_fix_emergencyFallback() public {
        _fund(alice, 10 ether, 30_000e6);
        vm.startPrank(alice); vault.deposit(USDC, 30_000e6); vault.deposit(WETH, 10 ether); vm.stopPrank();
        _fund(bob, 0, 1000e6);
        vm.prank(bob); vault.deposit(USDC, 1000e6);
        _submit(10, 20, 30);
        (int24 lo, int24 hi) = _range();
        vm.prank(keeper); vault.rebalance(hour, 1, ArgonVault.Action.ENTER, lo, hi, 0, 0);
        vm.mockCallRevert(address(adapter), abi.encodeWithSelector(IPoolAdapter.harvest.selector), "broken");
        { uint256 _s = vault.shareBalance(bob); vm.prank(bob); vm.expectRevert(); vault.withdraw(_s); }
        vm.prank(bob); vault.emergencyWithdraw();
        assertEq(vault.shareBalance(bob), 0);
    }
}

contract FixOneSided is ForkBase {
    function setUp() public { _setUpFork(); }
    function test_fix_oneSidedEnter() public {
        vault.setRouter(ROUTER);
        address a = address(0xA11CE);
        _fund(a, 0, 100_000e6);
        vm.prank(a); vault.deposit(USDC, 100_000e6);
        _submit(10, 20, 30);
        (int24 lo, int24 hi) = _range();
        vm.prank(keeper); vault.rebalance(hour, 1, ArgonVault.Action.ENTER, lo, hi, 0, 0);
        assertEq(vault.poolStatus(1), 1);
        (uint256 pa, uint256 pb) = adapter.amounts();
        uint256 inPos = _usd(pa, pb);
        uint256 idle = _usd(IERC20(WETH).balanceOf(address(vault)), IERC20(USDC).balanceOf(address(vault)));
        emit log_named_uint("in position usd8", inPos); emit log_named_uint("idle usd8", idle);
        assertGt(inPos, idle * 10);
        assertApproxEqRel(inPos + idle, 100_000e6 * 100, 2e15); // swap cost < 0.2%
        uint256 s = vault.shareBalance(a);
        vm.prank(a); vault.withdraw(s);
        emit log_named_uint("alice out usd8", _usd(IERC20(WETH).balanceOf(a), IERC20(USDC).balanceOf(a)));
    }
}

interface IAgg { function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80); }

contract FixLiveNav is ForkBase {
    function setUp() public { _setUpFork(); }
    function test_fix_liveNav() public {
        address a = address(0xA11CE); address b = address(0xB0B); address whale = address(0x3E3);
        _fund(a, 20 ether, 60_000e6);
        vm.startPrank(a); vault.deposit(USDC, 60_000e6); vault.deposit(WETH, 20 ether); vm.stopPrank();
        _submit(10, 20, 30);
        (int24 lo, int24 hi) = _range();
        vm.prank(keeper); vault.rebalance(hour, 1, ArgonVault.Action.ENTER, lo, hi, 0, 0);
        // market sells ETH ~3%: pool moves, Chainlink follows
        _swap(WETH, USDC, 4_000 ether, whale);
        (uint160 s,, address t0) = IPoolAdapter(address(adapter)).spot();
        uint256 newPx = vault.spotPriceUsd8(s, t0);
        (uint80 r,,,, uint80 ar) = IAgg(FEED).latestRoundData();
        vm.mockCall(FEED, abi.encodeWithSelector(IAgg.latestRoundData.selector), abi.encode(r, int256(newPx), block.timestamp, block.timestamp, ar));
        (uint256 pa, uint256 pb) = adapter.amounts();
        (uint256 la, uint256 lb) = adapter.valueAt(vault.oracleSqrtPriceX96(t0));
        emit log_named_uint("new price usd8", newPx);
        emit log_named_uint("book (principal) usd8", _usd(pa, pb));
        emit log_named_uint("live position usd8", _usd(la, lb));
        _fund(b, 0, 100_000e6);
        vm.prank(b); vault.deposit(USDC, 100_000e6);
        uint256 sb = vault.shareBalance(b);
        vm.prank(b); vault.withdraw(sb);
        uint256 out = _usd(IERC20(WETH).balanceOf(b), IERC20(USDC).balanceOf(b));
        emit log_named_uint("bob out of 1e13", out);
        assertApproxEqRel(out, 100_000e6 * 100, 3e15); // <0.3% (pool vs oracle rounding + fees)
    }
}
