// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.34;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

interface IERC5192 {
    event Locked(uint256 tokenId);
    event Unlocked(uint256 tokenId);

    function locked(uint256 tokenId) external view returns (bool);
}

/// @title MedTrustRoleCredential
/// @notice Revocable, expiring, non-transferable evidence that a wallet passed
///         MedTrust's off-chain organization and role admission process.
/// @dev This token is not legal KYC and intentionally exposes hashes only.
contract MedTrustRoleCredential is ERC721, AccessControl, Pausable, IERC5192 {
    bytes32 public constant ISSUER_ROLE = keccak256("ISSUER_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    struct Credential {
        bytes32 didHash;
        bytes32 organizationDigest;
        bytes32 role;
        bytes32 evidenceDigest;
        uint64 issuedAt;
        uint64 expiresAt;
        bool revoked;
    }

    uint256 private _nextTokenId = 1;
    /// @notice Immutable digest of the single MedTrust data space served by
    ///         this credential registry. It prevents one deployment from being
    ///         treated as a shared qualification source across spaces.
    bytes32 public immutable spaceScopeDigest;
    mapping(uint256 tokenId => Credential credential) private _credentials;
    mapping(address holder => mapping(bytes32 role => uint256 tokenId)) private _activeCredential;

    error InvalidCredential();
    error ActiveCredentialExists(address holder, bytes32 role);
    error CredentialNotFound(uint256 tokenId);
    error CredentialAlreadyRevoked(uint256 tokenId);
    error Soulbound();

    event CredentialIssued(
        uint256 indexed tokenId,
        address indexed holder,
        address indexed issuer,
        bytes32 role,
        bytes32 didHash,
        bytes32 organizationDigest,
        bytes32 evidenceDigest,
        uint64 expiresAt
    );
    event CredentialRevoked(
        uint256 indexed tokenId,
        address indexed holder,
        address indexed issuer,
        bytes32 role,
        bytes32 reasonDigest
    );

    constructor(address admin, bytes32 spaceScopeDigest_)
        ERC721("MedTrust Role Credential", "MTRC")
    {
        if (admin == address(0) || spaceScopeDigest_ == bytes32(0)) revert InvalidCredential();
        spaceScopeDigest = spaceScopeDigest_;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ISSUER_ROLE, admin);
        _grantRole(PAUSER_ROLE, admin);
    }

    function issue(
        address holder,
        bytes32 didHash,
        bytes32 organizationDigest,
        bytes32 role,
        bytes32 evidenceDigest,
        uint64 expiresAt
    ) external onlyRole(ISSUER_ROLE) whenNotPaused returns (uint256 tokenId) {
        if (
            holder == address(0) || didHash == bytes32(0)
                || organizationDigest == bytes32(0) || role == bytes32(0)
                || evidenceDigest == bytes32(0) || expiresAt <= block.timestamp
        ) revert InvalidCredential();
        uint256 existing = _activeCredential[holder][role];
        if (existing != 0 && _isValid(existing, holder, role)) {
            revert ActiveCredentialExists(holder, role);
        }

        tokenId = _nextTokenId++;
        _credentials[tokenId] = Credential({
            didHash: didHash,
            organizationDigest: organizationDigest,
            role: role,
            evidenceDigest: evidenceDigest,
            issuedAt: uint64(block.timestamp),
            expiresAt: expiresAt,
            revoked: false
        });
        _activeCredential[holder][role] = tokenId;
        _safeMint(holder, tokenId);
        emit Locked(tokenId);
        emit CredentialIssued(
            tokenId,
            holder,
            msg.sender,
            role,
            didHash,
            organizationDigest,
            evidenceDigest,
            expiresAt
        );
    }

    function revoke(uint256 tokenId, bytes32 reasonDigest) external onlyRole(ISSUER_ROLE) {
        address holder = _ownerOf(tokenId);
        if (holder == address(0)) revert CredentialNotFound(tokenId);
        Credential storage item = _credentials[tokenId];
        if (item.revoked) revert CredentialAlreadyRevoked(tokenId);
        item.revoked = true;
        if (_activeCredential[holder][item.role] == tokenId) {
            delete _activeCredential[holder][item.role];
        }
        emit CredentialRevoked(tokenId, holder, msg.sender, item.role, reasonDigest);
    }

    function credential(uint256 tokenId) external view returns (Credential memory) {
        if (_ownerOf(tokenId) == address(0)) revert CredentialNotFound(tokenId);
        return _credentials[tokenId];
    }

    function credentialId(address holder, bytes32 role) external view returns (uint256) {
        return _activeCredential[holder][role];
    }

    function hasValidCredential(address holder, bytes32 role) external view returns (bool) {
        return _isValid(_activeCredential[holder][role], holder, role);
    }

    function locked(uint256 tokenId) external view override returns (bool) {
        if (_ownerOf(tokenId) == address(0)) revert CredentialNotFound(tokenId);
        return true;
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    function approve(address, uint256) public pure override {
        revert Soulbound();
    }

    function setApprovalForAll(address, bool) public pure override {
        revert Soulbound();
    }

    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC721, AccessControl)
        returns (bool)
    {
        return interfaceId == type(IERC5192).interfaceId || super.supportsInterface(interfaceId);
    }

    function _update(address to, uint256 tokenId, address auth)
        internal
        override
        returns (address)
    {
        address from = _ownerOf(tokenId);
        if (from != address(0) && to != address(0)) revert Soulbound();
        return super._update(to, tokenId, auth);
    }

    function _isValid(uint256 tokenId, address holder, bytes32 role) private view returns (bool) {
        if (tokenId == 0 || _ownerOf(tokenId) != holder) return false;
        Credential storage item = _credentials[tokenId];
        return !item.revoked && item.role == role && block.timestamp < item.expiresAt;
    }
}
