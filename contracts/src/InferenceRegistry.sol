// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./utils/Ownable.sol";
import {IInferenceRegistry} from "./interfaces/IInferenceRegistry.sol";

/// @notice Public hourly forecast board. Same bytecode on Arbitrum and Robinhood.
contract InferenceRegistry is Ownable, IInferenceRegistry {
    address public keeper;
    bytes32 public modelId;
    uint64 public latestHourId;
    uint64 public forecastCount;

    mapping(uint64 hourId => Forecast) private _forecasts;

    uint64 public constant WARMUP_SUBMITS = 9;

    error NotKeeper();
    error AlreadySubmitted();
    error HourNotMonotonic();
    error HashMismatch();
    error UnknownHour();

    event KeeperSet(address indexed keeper);
    event ModelIdSet(bytes32 modelId);
    event ForecastSubmitted(
        uint64 indexed hourId, int256 pct1hBps, int256 pct2hBps, int256 pct8hBps, bytes32 forecastHash
    );

    modifier onlyKeeper() {
        if (msg.sender != keeper) revert NotKeeper();
        _;
    }

    constructor(address initialOwner, address keeper_, bytes32 modelId_) Ownable(initialOwner) {
        if (keeper_ == address(0)) revert ZeroAddress();
        keeper = keeper_;
        modelId = modelId_;
        emit KeeperSet(keeper_);
        emit ModelIdSet(modelId_);
    }

    function setKeeper(address keeper_) external onlyOwner {
        if (keeper_ == address(0)) revert ZeroAddress();
        keeper = keeper_;
        emit KeeperSet(keeper_);
    }

    function setModelId(bytes32 modelId_) external onlyOwner {
        modelId = modelId_;
        emit ModelIdSet(modelId_);
    }

    function submit(uint64 hourId, int256 pct1hBps, int256 pct2hBps, int256 pct8hBps, bytes32 forecastHash)
        external
        onlyKeeper
    {
        if (forecastCount != 0 && hourId <= latestHourId) revert HourNotMonotonic();
        if (hourId != uint64(block.timestamp / 3600)) revert HourNotMonotonic(); // [FIX M-hourId] bind to wall clock
        if (_abs(pct1hBps) > 10_000 || _abs(pct2hBps) > 10_000 || _abs(pct8hBps) > 10_000) revert HashMismatch(); // [FIX I-int-min]
        if (_forecasts[hourId].submitter != address(0)) revert AlreadySubmitted();

        bytes32 expected = keccak256(abi.encode(hourId, pct1hBps, pct2hBps, pct8hBps, modelId));
        if (forecastHash != expected) revert HashMismatch();

        _forecasts[hourId] = Forecast({
            pct1hBps: pct1hBps,
            pct2hBps: pct2hBps,
            pct8hBps: pct8hBps,
            forecastHash: forecastHash,
            submittedAt: uint64(block.timestamp),
            submitter: msg.sender
        });
        latestHourId = hourId;
        unchecked {
            forecastCount += 1;
        }
        emit ForecastSubmitted(hourId, pct1hBps, pct2hBps, pct8hBps, forecastHash);
    }

    function getForecast(uint64 hourId) external view returns (Forecast memory) {
        Forecast memory f = _forecasts[hourId];
        if (f.submitter == address(0)) revert UnknownHour();
        return f;
    }

    function warmupComplete() external view returns (bool) {
        return forecastCount >= WARMUP_SUBMITS;
    }

    function computeHash(uint64 hourId, int256 pct1hBps, int256 pct2hBps, int256 pct8hBps)
        external
        view
        returns (bytes32)
    {
        return keccak256(abi.encode(hourId, pct1hBps, pct2hBps, pct8hBps, modelId));
    }

    function _abs(int256 x) private pure returns (uint256) {
        return x == type(int256).min ? type(uint256).max : uint256(x >= 0 ? x : -x);
    }
}
