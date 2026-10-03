// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ArgonVault} from "../src/ArgonVault.sol";
import {InferenceRegistry} from "../src/InferenceRegistry.sol";
import {MockERC20} from "../src/mocks/MockERC20.sol";
import {MockOracle} from "../src/mocks/MockOracle.sol";
import {MockPoolAdapter} from "../src/mocks/MockPoolAdapter.sol";

contract ArgonVaultTest is Test {
    ArgonVault internal vault;
    InferenceRegistry internal reg;
    MockERC20 internal weth;
    MockERC20 internal usdc;
    MockOracle internal oracle;
    MockPoolAdapter internal adapter;
    address internal keeper = address(0xB0);
    address internal alice = address(0xA1);
    address internal bob = address(0xB2);
    bytes32 internal modelId = keccak256("eth-1-2-8h-v1");

    function setUp() public {
        weth = new MockERC20("WETH", "WETH", 18);
        usdc = new MockERC20("USDC", "USDC", 6);
        oracle = new MockOracle();
        reg = new InferenceRegistry(address(this), keeper, modelId);
        vault = new ArgonVault(address(this), keeper, address(reg), address(oracle), address(weth), address(usdc), 6);
        adapter = new MockPoolAdapter(address(weth), address(usdc));
        adapter.setVault(address(vault));
        // [FIX-SIM] mock pool spot == mock oracle (2000 USD), tick 0 inside the tests' [-60,60] range
        adapter.setSpot(address(weth) < address(usdc) ? uint160(3543191142285914327220224) : uint160(1771595571142957166518320255467520), 0);
        vault.setPool(1, address(adapter), true);

        weth.mint(alice, 10 ether);
        usdc.mint(alice, 20_000e6);
        weth.mint(bob, 10 ether);
        usdc.mint(bob, 20_000e6);
        vm.startPrank(alice);
        weth.approve(address(vault), type(uint256).max);
        usdc.approve(address(vault), type(uint256).max);
        vm.stopPrank();
        vm.startPrank(bob);
        weth.approve(address(vault), type(uint256).max);
        usdc.approve(address(vault), type(uint256).max);
        vm.stopPrank();
    }

    function _hash(uint64 hour, int256 a, int256 b, int256 c) internal view returns (bytes32) {
        return keccak256(abi.encode(hour, a, b, c, modelId));
    }

    function _submit(uint64 hour, int256 a, int256 b, int256 c) internal {
        vm.warp(uint256(hour) * 3600 + 60); // [FIX-SIM] registry now binds hourId to block.timestamp
        vm.prank(keeper);
        reg.submit(hour, a, b, c, _hash(hour, a, b, c));
    }

    function _warmup() internal {
        for (uint64 i = 0; i < 9; i++) {
            _submit(1000 + i, 10, 20, 30);
        }
    }

    function testDepositMintsSharesAndWithdrawProRata() public {
        vm.prank(alice);
        vault.deposit(address(usdc), 1_000e6);
        vm.prank(bob);
        vault.deposit(address(usdc), 1_000e6);
        assertEq(vault.shareBalance(alice), vault.shareBalance(bob));

        uint256 aliceUsdc = usdc.balanceOf(alice);
        uint256 shares = vault.shareBalance(alice);
        vm.prank(alice);
        vault.withdraw(shares);
        assertEq(usdc.balanceOf(alice) - aliceUsdc, 1_000e6);
        assertEq(vault.shareBalance(alice), 0);
    }

    function testEnterThenExit() public {
        _warmup();
        vm.prank(alice);
        vault.deposit(address(usdc), 2_000e6);
        vm.prank(alice);
        vault.deposit(address(weth), 1 ether);

        _submit(1010, -40, -110, -150);
        vm.prank(keeper);
        vault.rebalance(1010, 1, ArgonVault.Action.ENTER, -60, 60, 0, 0);
        assertEq(vault.poolStatus(1), 1);

        _submit(1011, -30, -280, -150);
        vm.prank(keeper);
        vault.rebalance(1011, 1, ArgonVault.Action.EXIT, 0, 0, 0, 0);
        assertEq(vault.poolStatus(1), 0);
        assertGt(weth.balanceOf(address(vault)), 0);
        assertGt(usdc.balanceOf(address(vault)), 0);
    }

    function testRejectEnterWhenGateSaysExit() public {
        _warmup();
        vm.prank(alice);
        vault.deposit(address(usdc), 1_000e6);
        _submit(1010, -30, -280, -50);
        vm.prank(keeper);
        vm.expectRevert(abi.encodeWithSelector(ArgonVault.ActionMismatch.selector, 2, 1));
        vault.rebalance(1010, 1, ArgonVault.Action.ENTER, -60, 60, 0, 0);
    }

    function testRejectEnterDuringWarmup() public {
        _submit(1, 10, 20, 30);
        vm.prank(alice);
        vault.deposit(address(usdc), 1_000e6);
        vm.prank(keeper);
        vm.expectRevert(ArgonVault.Warmup.selector);
        vault.rebalance(1, 1, ArgonVault.Action.ENTER, -60, 60, 0, 0);
    }

    function testWithdrawFlattensLp() public {
        _warmup();
        vm.prank(alice);
        vault.deposit(address(usdc), 2_000e6);
        vm.prank(alice);
        vault.deposit(address(weth), 1 ether);
        _submit(1010, -40, -110, -150);
        vm.prank(keeper);
        vault.rebalance(1010, 1, ArgonVault.Action.ENTER, -60, 60, 0, 0);
        assertEq(vault.poolStatus(1), 1);

        uint256 shares = vault.shareBalance(alice);
        vm.prank(alice);
        vault.withdraw(shares);
        assertEq(vault.poolStatus(1), 0);
        assertEq(vault.shareBalance(alice), 0);
    }

    function testEnterCooldownAfterExit() public {
        _warmup();
        vm.prank(alice);
        vault.deposit(address(usdc), 2_000e6);
        vm.prank(alice);
        vault.deposit(address(weth), 1 ether);
        _submit(1010, -40, -110, -150);
        vm.prank(keeper);
        vault.rebalance(1010, 1, ArgonVault.Action.ENTER, -60, 60, 0, 0);
        _submit(1011, -30, -280, -150);
        vm.prank(keeper);
        vault.rebalance(1011, 1, ArgonVault.Action.EXIT, 0, 0, 0, 0);
        _submit(1012, -40, -110, -150);
        vm.prank(keeper);
        vm.expectRevert(ArgonVault.Cooldown.selector);
        vault.rebalance(1012, 1, ArgonVault.Action.ENTER, -60, 60, 0, 0);
    }

    function testRejectDoubleRebalanceSameHour() public {
        _warmup();
        vm.prank(alice);
        vault.deposit(address(usdc), 1_000e6);
        _submit(1010, -40, -110, -150);
        vm.startPrank(keeper);
        vault.rebalance(1010, 1, ArgonVault.Action.HOLD, 0, 0, 0, 0);
        vm.expectRevert(ArgonVault.AlreadyRebalanced.selector);
        vault.rebalance(1010, 1, ArgonVault.Action.HOLD, 0, 0, 0, 0);
        vm.stopPrank();
    }

    function testUngatedPoolReverts() public {
        MockPoolAdapter link = new MockPoolAdapter(address(weth), address(usdc));
        link.setVault(address(vault));
        vault.setPool(2, address(link), false);
        _warmup();
        _submit(1010, 0, 0, 0);
        vm.prank(keeper);
        vm.expectRevert(ArgonVault.PoolNotGated.selector);
        vault.rebalance(1010, 2, ArgonVault.Action.HOLD, 0, 0, 0, 0);
    }

    // ---- news pause ----

    function _enterAt(uint64 hour) internal {
        _submit(hour, -40, -110, -150);
        vm.prank(keeper);
        vault.rebalance(hour, 1, ArgonVault.Action.ENTER, -60, 60, 0, 0);
    }

    function testNewsPauseForcesExitWhenForecastCalm() public {
        _warmup();
        vm.startPrank(alice);
        vault.deposit(address(usdc), 2_000e6);
        vault.deposit(address(weth), 1 ether);
        vm.stopPrank();
        _enterAt(1010);
        assertEq(vault.poolStatus(1), 1);

        // CPI in hour 1012 -> pause [1011, 1016)
        vm.prank(keeper);
        vault.setNewsPause(1011, 1016);

        _submit(1011, -10, -20, -30); // calm: the gate alone would say HOLD
        vm.prank(keeper);
        vm.expectRevert(abi.encodeWithSelector(ArgonVault.ActionMismatch.selector, 2, 0));
        vault.rebalance(1011, 1, ArgonVault.Action.HOLD, 0, 10, 0, 0);
        vm.prank(keeper);
        vault.rebalance(1011, 1, ArgonVault.Action.EXIT, 0, 10, 0, 0);
        assertEq(vault.poolStatus(1), 0);
    }

    function testNewsPauseBlocksEnterUntilWindowEnds() public {
        _warmup();
        vm.startPrank(alice);
        vault.deposit(address(usdc), 2_000e6);
        vault.deposit(address(weth), 1 ether);
        vm.stopPrank();
        vm.warp(1010 * 3600 + 60);
        vm.prank(keeper);
        vault.setNewsPause(1010, 1015);

        for (uint64 h = 1010; h < 1015; h++) {
            _submit(h, -40, -110, -150);
            vm.prank(keeper);
            vm.expectRevert(abi.encodeWithSelector(ArgonVault.ActionMismatch.selector, 2, 1));
            vault.rebalance(h, 1, ArgonVault.Action.ENTER, -60, 60, 0, 0);
            vm.prank(keeper);
            vault.rebalance(h, 1, ArgonVault.Action.EXIT, 0, 10, 0, 0); // idle: stays flat
            assertTrue(vault.newsPaused(h));
        }
        assertFalse(vault.newsPaused(1015));
        _enterAt(1015);
        assertEq(vault.poolStatus(1), 1);
    }

    function testNewsPauseOnlyKeeper() public {
        vm.warp(1000 * 3600);
        vm.prank(alice);
        vm.expectRevert(ArgonVault.NotKeeper.selector);
        vault.setNewsPause(1001, 1005);
    }

    function testNewsPauseBounds() public {
        vm.warp(1000 * 3600);
        vm.startPrank(keeper);
        vm.expectRevert(ArgonVault.BadNewsPause.selector);
        vault.setNewsPause(1005, 1005); // empty
        vm.expectRevert(ArgonVault.BadNewsPause.selector);
        vault.setNewsPause(1001, 1026); // 25h > cap
        vm.expectRevert(ArgonVault.BadNewsPause.selector);
        vault.setNewsPause(990, 1000); // already over
        vm.expectRevert(ArgonVault.BadNewsPause.selector);
        vault.setNewsPause(1049, 1053); // too far ahead
        vault.setNewsPause(1048, 1053); // max lead is fine
        vault.setNewsPause(1001, 1025); // pending window may be replaced; 24h is fine
        vm.stopPrank();
        assertEq(vault.newsPauseFrom(), 1001);
        assertEq(vault.newsPauseUntil(), 1025);
    }

    function testActiveNewsPauseCannotBeShortened() public {
        vm.warp(1000 * 3600);
        vm.prank(keeper);
        vault.setNewsPause(999, 1004);
        vm.startPrank(keeper);
        vm.expectRevert(ArgonVault.BadNewsPause.selector);
        vault.setNewsPause(999, 1002); // shorten
        vm.expectRevert(ArgonVault.BadNewsPause.selector);
        vault.setNewsPause(1002, 1010); // move start into the future (would lift the pause now)
        vault.setNewsPause(1000, 1008); // extend from now is fine
        vm.stopPrank();
        assertTrue(vault.newsPaused(1000));
        assertEq(vault.newsPauseUntil(), 1008);

        vm.prank(alice);
        vm.expectRevert();
        vault.clearNewsPause();
        vault.clearNewsPause(); // owner
        assertFalse(vault.newsPaused(1000));
    }
}
