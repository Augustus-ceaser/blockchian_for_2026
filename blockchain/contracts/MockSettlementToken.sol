// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.34;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @notice Local-demo settlement token. Never use this mock for real funds.
contract MockSettlementToken is ERC20 {
    constructor(address initialHolder, uint256 initialSupply) ERC20("MedTrust Demo CNY", "mCNY") {
        _mint(initialHolder, initialSupply);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}
