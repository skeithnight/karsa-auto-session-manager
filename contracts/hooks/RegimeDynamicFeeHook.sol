// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title RegimeDynamicFeeHook
 * @notice Uniswap v4 Hook that dynamically updates swap fee tiers based on Karsa ASM market regime:
 *         - TREND / HYPER_TREND: 1.00% (100 bps) fee to capture adverse selection from toxic arb flow
 *         - RANGE / MEAN_REVERSION: 0.05% (5 bps) fee for maximum fee volume on non-toxic flow
 *         - CHOP / DEFAULT: 0.30% (30 bps) standard fee
 * @dev Inherits standard v4 Hook interfaces and restricts oracle pushes to Karsa ASM's authorized signer.
 */

interface IPoolManager {
    struct PoolKey {
        address currency0;
        address currency1;
        uint24 fee;
        int24 tickSpacing;
        address hooks;
    }
}

contract RegimeDynamicFeeHook {
    address public owner;
    address public authorizedOracleSigner;
    
    // Dynamic fee mapping per poolId or global default
    uint24 public currentDynamicFeeBps = 30; // 30 bps default (0.30%)
    string public currentRegime = "CHOP";
    uint256 public lastOracleUpdateTimestamp;

    event RegimeFeeUpdated(string indexed regime, uint24 feeBps, uint256 timestamp);
    event OracleSignerUpdated(address indexed oldSigner, address indexed newSigner);

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }

    modifier onlyOracle() {
        require(msg.sender == authorizedOracleSigner || msg.sender == owner, "Only authorized oracle");
        _;
    }

    constructor(address _oracleSigner) {
        owner = msg.sender;
        authorizedOracleSigner = _oracleSigner;
        lastOracleUpdateTimestamp = block.timestamp;
    }

    function setOracleSigner(address _newSigner) external onlyOwner {
        require(_newSigner != address(0), "Invalid signer");
        emit OracleSignerUpdated(authorizedOracleSigner, _newSigner);
        authorizedOracleSigner = _newSigner;
    }

    /**
     * @notice Pushes regime update from Karsa ASM off-chain classifier.
     * @param _regime The detected market regime string (e.g., "TREND_BULL", "RANGE", "CHOP")
     * @param _feeBps Dynamic fee in basis points (e.g., 100 for 1.00%, 5 for 0.05%)
     */
    function updateRegime(string calldata _regime, uint24 _feeBps) external onlyOracle {
        require(_feeBps <= 500, "Fee exceeds 5% maximum safety cap");
        currentRegime = _regime;
        currentDynamicFeeBps = _feeBps;
        lastOracleUpdateTimestamp = block.timestamp;

        emit RegimeFeeUpdated(_regime, _feeBps, block.timestamp);
    }

    /**
     * @notice Hook callback invoked before swap execution in Uniswap v4 PoolManager.
     * @return selector Returns selector and override fee
     */
    function beforeSwap(
        address,
        IPoolManager.PoolKey calldata,
        bytes calldata
    ) external view returns (bytes4, uint24) {
        // Returns the beforeSwap selector (0x0) and the dynamic fee in basis points * 100
        return (this.beforeSwap.selector, currentDynamicFeeBps * 100);
    }
}
