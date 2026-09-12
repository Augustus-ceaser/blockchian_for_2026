// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.34;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IAgreementRegistry {
    function isActive(bytes32 agreementId) external view returns (bool);

    function spaceScopeDigest() external view returns (bytes32);

    function participants(bytes32 agreementId)
        external
        view
        returns (address requester, address dataProvider, address modelProvider, address operator);
}

interface IRoleCredential {
    function hasValidCredential(address holder, bytes32 role) external view returns (bool);

    function spaceScopeDigest() external view returns (bytes32);
}

/// @title MedTrustEscrow
/// @notice Holds controlled-compute fees and settles only after two independent
///         attestations: successful execution and approved delivery evidence.
/// @dev The attestations contain hashes of off-chain audit evidence, never data.
contract MedTrustEscrow is AccessControl, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant EXECUTION_ATTESTOR_ROLE = keccak256("EXECUTION_ATTESTOR_ROLE");
    bytes32 public constant DELIVERY_ATTESTOR_ROLE = keccak256("DELIVERY_ATTESTOR_ROLE");
    bytes32 public constant ATTESTOR_ADMIN_ROLE = keccak256("ATTESTOR_ADMIN_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    bytes32 public constant REQUESTER_ROLE = keccak256("data_requester");
    bytes32 public constant DATA_PROVIDER_ROLE = keccak256("data_provider");
    bytes32 public constant MODEL_PROVIDER_ROLE = keccak256("model_provider");
    bytes32 public constant OPERATOR_ROLE = keccak256("space_operator");

    enum EscrowState {
        None,
        Funded,
        Settled,
        Refunded
    }

    struct Escrow {
        bytes32 agreementId;
        bytes32 taskDigest;
        address payer;
        address dataProvider;
        address modelProvider;
        address platformTreasury;
        uint128 dataFee;
        uint128 modelFee;
        uint128 platformFee;
        uint64 refundAfter;
        bytes32 executionDigest;
        bytes32 deliveryDigest;
        address executionAttestor;
        address deliveryAttestor;
        EscrowState state;
    }

    IERC20 public immutable settlementToken;
    IAgreementRegistry public immutable agreementRegistry;
    IRoleCredential public immutable roleCredential;
    bytes32 public immutable spaceScopeDigest;

    mapping(bytes32 orderId => Escrow escrow) private _escrows;
    mapping(address beneficiary => uint256 amount) public claimable;

    error InvalidConfiguration();
    error InvalidEscrow();
    error EscrowAlreadyExists(bytes32 orderId);
    error EscrowNotFound(bytes32 orderId);
    error InvalidState(EscrowState actual);
    error AgreementInactive(bytes32 agreementId);
    error MissingCredential(address holder, bytes32 role);
    error NotAgreementRequester(address caller);
    error AttestationAlreadyRecorded();
    error RefundNotAvailable();
    error NothingToWithdraw();
    error AttestorRoleConflict(address account);
    error AttestorReuse(address account);

    event EscrowFunded(
        bytes32 indexed orderId,
        bytes32 indexed agreementId,
        bytes32 indexed taskDigest,
        address payer,
        uint256 totalAmount,
        uint64 refundAfter
    );
    event ExecutionAttested(
        bytes32 indexed orderId,
        bytes32 indexed executionDigest,
        address indexed attestor
    );
    event DeliveryAttested(
        bytes32 indexed orderId,
        bytes32 indexed deliveryDigest,
        address indexed attestor
    );
    event EscrowSettled(
        bytes32 indexed orderId,
        bytes32 indexed executionDigest,
        bytes32 indexed deliveryDigest,
        uint256 dataFee,
        uint256 modelFee,
        uint256 platformFee
    );
    event EscrowRefunded(bytes32 indexed orderId, address indexed payer, uint256 amount);
    event ProceedsWithdrawn(address indexed beneficiary, uint256 amount);

    constructor(
        address admin,
        IERC20 settlementToken_,
        IAgreementRegistry agreementRegistry_,
        IRoleCredential roleCredential_,
        bytes32 spaceScopeDigest_,
        address executionAttestor,
        address deliveryAttestor
    ) {
        if (
            admin == address(0) || address(settlementToken_) == address(0)
                || address(agreementRegistry_) == address(0)
                || address(roleCredential_) == address(0)
                || spaceScopeDigest_ == bytes32(0)
                || executionAttestor == address(0) || deliveryAttestor == address(0)
                || executionAttestor == deliveryAttestor
                || agreementRegistry_.spaceScopeDigest() != spaceScopeDigest_
                || roleCredential_.spaceScopeDigest() != spaceScopeDigest_
        ) revert InvalidConfiguration();
        settlementToken = settlementToken_;
        agreementRegistry = agreementRegistry_;
        roleCredential = roleCredential_;
        spaceScopeDigest = spaceScopeDigest_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
        _grantRole(ATTESTOR_ADMIN_ROLE, admin);
        _setRoleAdmin(EXECUTION_ATTESTOR_ROLE, ATTESTOR_ADMIN_ROLE);
        _setRoleAdmin(DELIVERY_ATTESTOR_ROLE, ATTESTOR_ADMIN_ROLE);
        _grantRole(EXECUTION_ATTESTOR_ROLE, executionAttestor);
        _grantRole(DELIVERY_ATTESTOR_ROLE, deliveryAttestor);
    }

    function openEscrow(
        bytes32 orderId,
        bytes32 agreementId,
        bytes32 taskDigest,
        uint128 dataFee,
        uint128 modelFee,
        uint128 platformFee,
        uint64 refundAfter
    ) external nonReentrant whenNotPaused {
        if (_escrows[orderId].state != EscrowState.None) revert EscrowAlreadyExists(orderId);
        if (
            orderId == bytes32(0) || agreementId == bytes32(0) || taskDigest == bytes32(0)
                || refundAfter <= block.timestamp
        ) revert InvalidEscrow();
        uint256 total = uint256(dataFee) + uint256(modelFee) + uint256(platformFee);
        if (total == 0) revert InvalidEscrow();
        if (!agreementRegistry.isActive(agreementId)) revert AgreementInactive(agreementId);

        (address requester, address dataProvider, address modelProvider, address operator) =
            agreementRegistry.participants(agreementId);
        if (msg.sender != requester) revert NotAgreementRequester(msg.sender);
        _requireCredential(requester, REQUESTER_ROLE);
        _requireCredential(dataProvider, DATA_PROVIDER_ROLE);
        _requireCredential(modelProvider, MODEL_PROVIDER_ROLE);
        _requireCredential(operator, OPERATOR_ROLE);

        _escrows[orderId] = Escrow({
            agreementId: agreementId,
            taskDigest: taskDigest,
            payer: requester,
            dataProvider: dataProvider,
            modelProvider: modelProvider,
            platformTreasury: operator,
            dataFee: dataFee,
            modelFee: modelFee,
            platformFee: platformFee,
            refundAfter: refundAfter,
            executionDigest: bytes32(0),
            deliveryDigest: bytes32(0),
            executionAttestor: address(0),
            deliveryAttestor: address(0),
            state: EscrowState.Funded
        });
        settlementToken.safeTransferFrom(msg.sender, address(this), total);
        emit EscrowFunded(orderId, agreementId, taskDigest, msg.sender, total, refundAfter);
    }

    function attestExecution(bytes32 orderId, bytes32 executionDigest)
        external
        onlyRole(EXECUTION_ATTESTOR_ROLE)
        whenNotPaused
        nonReentrant
    {
        if (executionDigest == bytes32(0)) revert InvalidEscrow();
        Escrow storage item = _requiredFunded(orderId);
        if (block.timestamp >= item.refundAfter) revert RefundNotAvailable();
        if (item.executionDigest != bytes32(0)) revert AttestationAlreadyRecorded();
        if (item.deliveryAttestor == msg.sender) revert AttestorReuse(msg.sender);
        _requireSettlementEligibility(item);
        item.executionDigest = executionDigest;
        item.executionAttestor = msg.sender;
        emit ExecutionAttested(orderId, executionDigest, msg.sender);
        _settleIfComplete(orderId, item);
    }

    function attestDelivery(bytes32 orderId, bytes32 deliveryDigest)
        external
        onlyRole(DELIVERY_ATTESTOR_ROLE)
        whenNotPaused
        nonReentrant
    {
        if (deliveryDigest == bytes32(0)) revert InvalidEscrow();
        Escrow storage item = _requiredFunded(orderId);
        if (block.timestamp >= item.refundAfter) revert RefundNotAvailable();
        if (item.deliveryDigest != bytes32(0)) revert AttestationAlreadyRecorded();
        if (item.executionAttestor == msg.sender) revert AttestorReuse(msg.sender);
        _requireSettlementEligibility(item);
        item.deliveryDigest = deliveryDigest;
        item.deliveryAttestor = msg.sender;
        emit DeliveryAttested(orderId, deliveryDigest, msg.sender);
        _settleIfComplete(orderId, item);
    }

    function refund(bytes32 orderId) external nonReentrant {
        Escrow storage item = _requiredFunded(orderId);
        if (msg.sender != item.payer || block.timestamp < item.refundAfter) {
            revert RefundNotAvailable();
        }
        uint256 total = _total(item);
        item.state = EscrowState.Refunded;
        settlementToken.safeTransfer(item.payer, total);
        emit EscrowRefunded(orderId, item.payer, total);
    }

    /// @notice Pull-based withdrawal prevents one recipient or token edge case
    ///         from blocking the atomic settlement decision for every party.
    function withdrawProceeds() external nonReentrant {
        uint256 amount = claimable[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        claimable[msg.sender] = 0;
        settlementToken.safeTransfer(msg.sender, amount);
        emit ProceedsWithdrawn(msg.sender, amount);
    }

    function escrow(bytes32 orderId) external view returns (Escrow memory) {
        Escrow storage item = _escrows[orderId];
        if (item.state == EscrowState.None) revert EscrowNotFound(orderId);
        return item;
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    /// @dev Enforce independent execution and delivery authorities for every
    ///      grant path, including future internal extensions of this contract.
    function _grantRole(bytes32 role, address account) internal override returns (bool) {
        if (
            (role == EXECUTION_ATTESTOR_ROLE && hasRole(DELIVERY_ATTESTOR_ROLE, account))
                || (role == DELIVERY_ATTESTOR_ROLE && hasRole(EXECUTION_ATTESTOR_ROLE, account))
        ) revert AttestorRoleConflict(account);
        return super._grantRole(role, account);
    }

    function _requireCredential(address holder, bytes32 role) private view {
        if (!roleCredential.hasValidCredential(holder, role)) {
            revert MissingCredential(holder, role);
        }
    }

    function _requiredFunded(bytes32 orderId) private view returns (Escrow storage item) {
        item = _escrows[orderId];
        if (item.state == EscrowState.None) revert EscrowNotFound(orderId);
        if (item.state != EscrowState.Funded) revert InvalidState(item.state);
    }

    function _requireSettlementEligibility(Escrow storage item) private view {
        if (!agreementRegistry.isActive(item.agreementId)) {
            revert AgreementInactive(item.agreementId);
        }
        _requireCredential(item.payer, REQUESTER_ROLE);
        _requireCredential(item.dataProvider, DATA_PROVIDER_ROLE);
        _requireCredential(item.modelProvider, MODEL_PROVIDER_ROLE);
        _requireCredential(item.platformTreasury, OPERATOR_ROLE);
    }

    function _settleIfComplete(bytes32 orderId, Escrow storage item) private {
        if (item.executionDigest == bytes32(0) || item.deliveryDigest == bytes32(0)) return;
        uint256 dataFee = item.dataFee;
        uint256 modelFee = item.modelFee;
        uint256 platformFee = item.platformFee;
        item.state = EscrowState.Settled;

        if (dataFee != 0) claimable[item.dataProvider] += dataFee;
        if (modelFee != 0) claimable[item.modelProvider] += modelFee;
        if (platformFee != 0) claimable[item.platformTreasury] += platformFee;
        emit EscrowSettled(
            orderId,
            item.executionDigest,
            item.deliveryDigest,
            dataFee,
            modelFee,
            platformFee
        );
    }

    function _total(Escrow storage item) private view returns (uint256) {
        return uint256(item.dataFee) + uint256(item.modelFee) + uint256(item.platformFee);
    }
}
