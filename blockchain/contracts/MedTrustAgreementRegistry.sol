// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.34;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

interface IAgreementRoleCredential {
    function hasValidCredential(address holder, bytes32 role) external view returns (bool);

    function spaceScopeDigest() external view returns (bytes32);
}

/// @title MedTrustAgreementRegistry
/// @notice Anchors a MedTrust contract revision digest and requires all four
///         fixed business parties to confirm the same digest before activation.
/// @dev No medical record, identity document, contract prose, or result payload
///      belongs on-chain. This prototype accepts direct calls from fixed party
///      addresses; contract-wallet support also requires an EIP-1271-aware backend.
contract MedTrustAgreementRegistry is AccessControl, Pausable {
    bytes32 public constant REGISTRAR_ROLE = keccak256("REGISTRAR_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    bytes32 public constant REQUESTER_ROLE = keccak256("data_requester");
    bytes32 public constant DATA_PROVIDER_ROLE = keccak256("data_provider");
    bytes32 public constant MODEL_PROVIDER_ROLE = keccak256("model_provider");
    bytes32 public constant OPERATOR_ROLE = keccak256("space_operator");

    uint8 private constant REQUESTER_BIT = 1;
    uint8 private constant DATA_PROVIDER_BIT = 2;
    uint8 private constant MODEL_PROVIDER_BIT = 4;
    uint8 private constant OPERATOR_BIT = 8;
    uint8 private constant ALL_PARTIES = 15;

    enum AgreementState {
        None,
        Proposed,
        FullyConfirmed,
        Active,
        Suspended,
        Ended
    }

    struct Agreement {
        bytes32 termsDigest;
        address requester;
        address dataProvider;
        address modelProvider;
        address operator;
        uint64 validFrom;
        uint64 validUntil;
        uint8 confirmationBitmap;
        AgreementState state;
    }

    mapping(bytes32 agreementId => Agreement agreement) private _agreements;
    IAgreementRoleCredential public immutable roleCredential;
    bytes32 public immutable spaceScopeDigest;

    error AgreementAlreadyExists(bytes32 agreementId);
    error AgreementNotFound(bytes32 agreementId);
    error InvalidAgreement();
    error InvalidState(AgreementState actual);
    error NotAgreementParty(address caller);
    error OperatorMustConfirmLast();
    error AlreadyConfirmed(address caller);
    error OutsideEffectiveWindow();
    error MissingCredential(address holder, bytes32 role);

    event AgreementRegistered(
        bytes32 indexed agreementId,
        bytes32 indexed termsDigest,
        address requester,
        address dataProvider,
        address modelProvider,
        address operator,
        uint64 validFrom,
        uint64 validUntil
    );
    event AgreementConfirmed(
        bytes32 indexed agreementId,
        bytes32 indexed termsDigest,
        address indexed party,
        uint8 confirmationBitmap
    );
    event AgreementFullyConfirmed(bytes32 indexed agreementId, bytes32 indexed termsDigest);
    event AgreementActivated(bytes32 indexed agreementId, bytes32 indexed termsDigest);
    event AgreementSuspended(bytes32 indexed agreementId, bytes32 indexed reasonDigest);
    event AgreementEnded(bytes32 indexed agreementId, bytes32 indexed reasonDigest);

    constructor(
        address admin,
        IAgreementRoleCredential roleCredential_,
        bytes32 spaceScopeDigest_
    ) {
        if (
            admin == address(0) || address(roleCredential_) == address(0)
                || spaceScopeDigest_ == bytes32(0)
                || roleCredential_.spaceScopeDigest() != spaceScopeDigest_
        ) {
            revert InvalidAgreement();
        }
        roleCredential = roleCredential_;
        spaceScopeDigest = spaceScopeDigest_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(REGISTRAR_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
    }

    function registerAgreement(
        bytes32 agreementId,
        bytes32 termsDigest,
        address requester,
        address dataProvider,
        address modelProvider,
        address operator,
        uint64 validFrom,
        uint64 validUntil
    ) external onlyRole(REGISTRAR_ROLE) whenNotPaused {
        if (_agreements[agreementId].state != AgreementState.None) {
            revert AgreementAlreadyExists(agreementId);
        }
        if (
            agreementId == bytes32(0) || termsDigest == bytes32(0)
                || requester == address(0) || dataProvider == address(0)
                || modelProvider == address(0) || operator == address(0)
                || requester == dataProvider || requester == modelProvider
                || requester == operator || dataProvider == modelProvider
                || dataProvider == operator || modelProvider == operator
                || validUntil <= validFrom || validUntil <= block.timestamp
        ) revert InvalidAgreement();

        _agreements[agreementId] = Agreement({
            termsDigest: termsDigest,
            requester: requester,
            dataProvider: dataProvider,
            modelProvider: modelProvider,
            operator: operator,
            validFrom: validFrom,
            validUntil: validUntil,
            confirmationBitmap: 0,
            state: AgreementState.Proposed
        });
        emit AgreementRegistered(
            agreementId,
            termsDigest,
            requester,
            dataProvider,
            modelProvider,
            operator,
            validFrom,
            validUntil
        );
    }

    function confirm(bytes32 agreementId) external whenNotPaused {
        Agreement storage item = _requiredAgreement(agreementId);
        if (item.state != AgreementState.Proposed) revert InvalidState(item.state);
        if (block.timestamp >= item.validUntil) revert OutsideEffectiveWindow();

        uint8 partyBit = _partyBit(item, msg.sender);
        if (partyBit == 0) revert NotAgreementParty(msg.sender);
        bytes32 expectedRole = _partyRole(partyBit);
        if (!roleCredential.hasValidCredential(msg.sender, expectedRole)) {
            revert MissingCredential(msg.sender, expectedRole);
        }
        if ((item.confirmationBitmap & partyBit) != 0) revert AlreadyConfirmed(msg.sender);
        if (partyBit == OPERATOR_BIT && item.confirmationBitmap != 7) {
            revert OperatorMustConfirmLast();
        }

        item.confirmationBitmap |= partyBit;
        emit AgreementConfirmed(
            agreementId,
            item.termsDigest,
            msg.sender,
            item.confirmationBitmap
        );

        if (item.confirmationBitmap == ALL_PARTIES) {
            // Earlier confirmations are only historical intent. Qualification can
            // be revoked or expire before the operator submits the fourth
            // confirmation, so activation must re-check every current party.
            _requireAllCredentials(item);
            item.state = AgreementState.FullyConfirmed;
            emit AgreementFullyConfirmed(agreementId, item.termsDigest);
            if (block.timestamp >= item.validFrom) {
                item.state = AgreementState.Active;
                emit AgreementActivated(agreementId, item.termsDigest);
            }
        }
    }

    /// @notice Permissionless activation prevents a registrar from delaying an
    ///         already unanimous agreement once its effective window starts.
    function activate(bytes32 agreementId) external whenNotPaused {
        Agreement storage item = _requiredAgreement(agreementId);
        if (item.state != AgreementState.FullyConfirmed) revert InvalidState(item.state);
        if (block.timestamp < item.validFrom || block.timestamp >= item.validUntil) {
            revert OutsideEffectiveWindow();
        }
        // A future-dated agreement must not become active using credentials that
        // were valid only when the parties originally confirmed it.
        _requireAllCredentials(item);
        item.state = AgreementState.Active;
        emit AgreementActivated(agreementId, item.termsDigest);
    }

    function suspend(bytes32 agreementId, bytes32 reasonDigest) external onlyRole(PAUSER_ROLE) {
        Agreement storage item = _requiredAgreement(agreementId);
        if (item.state != AgreementState.Active) revert InvalidState(item.state);
        item.state = AgreementState.Suspended;
        emit AgreementSuspended(agreementId, reasonDigest);
    }

    function end(bytes32 agreementId, bytes32 reasonDigest) external onlyRole(REGISTRAR_ROLE) {
        Agreement storage item = _requiredAgreement(agreementId);
        if (
            item.state != AgreementState.Active
                && item.state != AgreementState.Suspended
                && item.state != AgreementState.FullyConfirmed
        ) revert InvalidState(item.state);
        item.state = AgreementState.Ended;
        emit AgreementEnded(agreementId, reasonDigest);
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function getAgreement(bytes32 agreementId) external view returns (Agreement memory) {
        return _requiredAgreementView(agreementId);
    }

    function participants(bytes32 agreementId)
        external
        view
        returns (address requester, address dataProvider, address modelProvider, address operator)
    {
        Agreement storage agreement_ = _requiredAgreementView(agreementId);
        return (
            agreement_.requester,
            agreement_.dataProvider,
            agreement_.modelProvider,
            agreement_.operator
        );
    }

    function hasConfirmed(bytes32 agreementId, address party) external view returns (bool) {
        Agreement storage agreement_ = _requiredAgreementView(agreementId);
        uint8 partyBit = _partyBit(agreement_, party);
        return partyBit != 0 && (agreement_.confirmationBitmap & partyBit) != 0;
    }

    function isActive(bytes32 agreementId) external view returns (bool) {
        Agreement storage agreement_ = _agreements[agreementId];
        return !paused()
            && agreement_.state == AgreementState.Active
            && block.timestamp >= agreement_.validFrom
            && block.timestamp < agreement_.validUntil;
    }

    function _requireAllCredentials(Agreement storage agreement_) private view {
        _requireCredential(agreement_.requester, REQUESTER_ROLE);
        _requireCredential(agreement_.dataProvider, DATA_PROVIDER_ROLE);
        _requireCredential(agreement_.modelProvider, MODEL_PROVIDER_ROLE);
        _requireCredential(agreement_.operator, OPERATOR_ROLE);
    }

    function _requireCredential(address holder, bytes32 role) private view {
        if (!roleCredential.hasValidCredential(holder, role)) {
            revert MissingCredential(holder, role);
        }
    }

    function _partyBit(Agreement storage agreement_, address party) private view returns (uint8) {
        if (party == agreement_.requester) return REQUESTER_BIT;
        if (party == agreement_.dataProvider) return DATA_PROVIDER_BIT;
        if (party == agreement_.modelProvider) return MODEL_PROVIDER_BIT;
        if (party == agreement_.operator) return OPERATOR_BIT;
        return 0;
    }

    function _partyRole(uint8 partyBit) private pure returns (bytes32) {
        if (partyBit == REQUESTER_BIT) return REQUESTER_ROLE;
        if (partyBit == DATA_PROVIDER_BIT) return DATA_PROVIDER_ROLE;
        if (partyBit == MODEL_PROVIDER_BIT) return MODEL_PROVIDER_ROLE;
        return OPERATOR_ROLE;
    }

    function _requiredAgreement(bytes32 agreementId) private view returns (Agreement storage agreement_) {
        agreement_ = _agreements[agreementId];
        if (agreement_.state == AgreementState.None) revert AgreementNotFound(agreementId);
    }

    function _requiredAgreementView(bytes32 agreementId)
        private
        view
        returns (Agreement storage agreement_)
    {
        agreement_ = _agreements[agreementId];
        if (agreement_.state == AgreementState.None) revert AgreementNotFound(agreementId);
    }
}
