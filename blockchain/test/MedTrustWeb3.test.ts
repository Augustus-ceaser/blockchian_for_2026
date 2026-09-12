import { expect } from 'chai'
import { network } from 'hardhat'

const { ethers } = await network.create()

const ROLE = {
  requester: ethers.id('data_requester'),
  dataProvider: ethers.id('data_provider'),
  modelProvider: ethers.id('model_provider'),
  operator: ethers.id('space_operator'),
}

const SPACE_SCOPE_DIGEST = ethers.id('medtrust:space:0967d9f7-509a-5583-b214-df66c2eae6de')

async function latestTimestamp() {
  const block = await ethers.provider.getBlock('latest')
  if (!block) throw new Error('latest block is unavailable')
  return BigInt(block.timestamp)
}

async function increaseTime(seconds: number) {
  await ethers.provider.send('evm_increaseTime', [seconds])
  await ethers.provider.send('evm_mine', [])
}

async function deployIdentityAndAgreement() {
  const [admin, requester, dataProvider, modelProvider, operator, executionAttestor, deliveryAttestor, outsider] =
    await ethers.getSigners()
  const credentials = await ethers.deployContract('MedTrustRoleCredential', [
    admin.address,
    SPACE_SCOPE_DIGEST,
  ])
  const now = await latestTimestamp()
  const expiry = now + 7n * 24n * 60n * 60n
  const participants = [
    [requester, ROLE.requester],
    [dataProvider, ROLE.dataProvider],
    [modelProvider, ROLE.modelProvider],
    [operator, ROLE.operator],
  ] as const
  for (const [holder, role] of participants) {
    await credentials.issue(
      holder.address,
      ethers.id(`did:${holder.address}`),
      ethers.id(`org:${holder.address}`),
      role,
      ethers.id(`evidence:${holder.address}`),
      expiry,
    )
  }

  const agreements = await ethers.deployContract('MedTrustAgreementRegistry', [
    admin.address,
    await credentials.getAddress(),
    SPACE_SCOPE_DIGEST,
  ])
  return {
    admin,
    requester,
    dataProvider,
    modelProvider,
    operator,
    executionAttestor,
    deliveryAttestor,
    outsider,
    credentials,
    agreements,
    now,
  }
}

async function registerActiveAgreement() {
  const fixture = await deployIdentityAndAgreement()
  const agreementId = ethers.id('medtrust:agreement:1')
  const termsDigest = ethers.sha256(ethers.toUtf8Bytes('frozen terms v1'))
  await fixture.agreements.registerAgreement(
    agreementId,
    termsDigest,
    fixture.requester.address,
    fixture.dataProvider.address,
    fixture.modelProvider.address,
    fixture.operator.address,
    fixture.now,
    fixture.now + 24n * 60n * 60n,
  )
  await fixture.agreements.connect(fixture.requester).confirm(agreementId)
  await fixture.agreements.connect(fixture.dataProvider).confirm(agreementId)
  await fixture.agreements.connect(fixture.modelProvider).confirm(agreementId)
  await fixture.agreements.connect(fixture.operator).confirm(agreementId)
  return { ...fixture, agreementId, termsDigest }
}

describe('Phase 5.4 MedTrustAgreementRegistry', function () {
  it('requires all four fixed parties and activates atomically on the operator confirmation', async function () {
    const fixture = await deployIdentityAndAgreement()
    const agreementId = ethers.id('agreement:unanimous')
    const termsDigest = ethers.sha256(ethers.toUtf8Bytes('canonical contract document'))
    await fixture.agreements.registerAgreement(
      agreementId,
      termsDigest,
      fixture.requester.address,
      fixture.dataProvider.address,
      fixture.modelProvider.address,
      fixture.operator.address,
      fixture.now,
      fixture.now + 3600n,
    )

    await fixture.agreements.connect(fixture.dataProvider).confirm(agreementId)
    await fixture.agreements.connect(fixture.requester).confirm(agreementId)
    await fixture.agreements.connect(fixture.modelProvider).confirm(agreementId)
    await expect(fixture.agreements.connect(fixture.operator).confirm(agreementId))
      .to.emit(fixture.agreements, 'AgreementActivated')
      .withArgs(agreementId, termsDigest)

    const agreement = await fixture.agreements.getAgreement(agreementId)
    expect(agreement.confirmationBitmap).to.equal(15n)
    expect(agreement.state).to.equal(3n)
    expect(await fixture.agreements.isActive(agreementId)).to.equal(true)
  })

  it('rejects outsiders, duplicate confirmations, and an operator confirming early', async function () {
    const fixture = await deployIdentityAndAgreement()
    const agreementId = ethers.id('agreement:guards')
    await fixture.agreements.registerAgreement(
      agreementId,
      ethers.id('terms'),
      fixture.requester.address,
      fixture.dataProvider.address,
      fixture.modelProvider.address,
      fixture.operator.address,
      fixture.now,
      fixture.now + 3600n,
    )

    await expect(fixture.agreements.connect(fixture.outsider).confirm(agreementId))
      .to.be.revertedWithCustomError(fixture.agreements, 'NotAgreementParty')
    await expect(fixture.agreements.connect(fixture.operator).confirm(agreementId))
      .to.be.revertedWithCustomError(fixture.agreements, 'OperatorMustConfirmLast')
    await fixture.agreements.connect(fixture.requester).confirm(agreementId)
    await expect(fixture.agreements.connect(fixture.requester).confirm(agreementId))
      .to.be.revertedWithCustomError(fixture.agreements, 'AlreadyConfirmed')
  })

  it('fails closed when a party credential is revoked before confirmation', async function () {
    const fixture = await deployIdentityAndAgreement()
    const agreementId = ethers.id('agreement:revoked')
    await fixture.agreements.registerAgreement(
      agreementId,
      ethers.id('terms'),
      fixture.requester.address,
      fixture.dataProvider.address,
      fixture.modelProvider.address,
      fixture.operator.address,
      fixture.now,
      fixture.now + 3600n,
    )
    const tokenId = await fixture.credentials.credentialId(fixture.modelProvider.address, ROLE.modelProvider)
    await fixture.credentials.revoke(tokenId, ethers.id('qualification withdrawn'))

    await expect(fixture.agreements.connect(fixture.modelProvider).confirm(agreementId))
      .to.be.revertedWithCustomError(fixture.agreements, 'MissingCredential')
  })

  it('rechecks all earlier confirmations before the fourth confirmation activates', async function () {
    const fixture = await deployIdentityAndAgreement()
    const agreementId = ethers.id('agreement:revoked-after-confirmation')
    await fixture.agreements.registerAgreement(
      agreementId,
      ethers.id('terms:revoked-after-confirmation'),
      fixture.requester.address,
      fixture.dataProvider.address,
      fixture.modelProvider.address,
      fixture.operator.address,
      fixture.now,
      fixture.now + 3600n,
    )

    await fixture.agreements.connect(fixture.requester).confirm(agreementId)
    await fixture.agreements.connect(fixture.dataProvider).confirm(agreementId)
    await fixture.agreements.connect(fixture.modelProvider).confirm(agreementId)
    const requesterCredential = await fixture.credentials.credentialId(
      fixture.requester.address,
      ROLE.requester,
    )
    await fixture.credentials.revoke(requesterCredential, ethers.id('requester qualification withdrawn'))

    await expect(fixture.agreements.connect(fixture.operator).confirm(agreementId))
      .to.be.revertedWithCustomError(fixture.agreements, 'MissingCredential')
      .withArgs(fixture.requester.address, ROLE.requester)
    const agreement = await fixture.agreements.getAgreement(agreementId)
    expect(agreement.confirmationBitmap).to.equal(7n)
    expect(agreement.state).to.equal(1n)
  })

  it('rechecks expired credentials before delayed activation', async function () {
    const fixture = await deployIdentityAndAgreement()
    const agreementId = ethers.id('agreement:future-dated-expired-credential')
    const validFrom = fixture.now + 8n * 24n * 60n * 60n
    await fixture.agreements.registerAgreement(
      agreementId,
      ethers.id('terms:future-dated'),
      fixture.requester.address,
      fixture.dataProvider.address,
      fixture.modelProvider.address,
      fixture.operator.address,
      validFrom,
      validFrom + 24n * 60n * 60n,
    )

    await fixture.agreements.connect(fixture.requester).confirm(agreementId)
    await fixture.agreements.connect(fixture.dataProvider).confirm(agreementId)
    await fixture.agreements.connect(fixture.modelProvider).confirm(agreementId)
    await fixture.agreements.connect(fixture.operator).confirm(agreementId)
    expect((await fixture.agreements.getAgreement(agreementId)).state).to.equal(2n)

    await increaseTime(8 * 24 * 60 * 60)
    await expect(fixture.agreements.activate(agreementId))
      .to.be.revertedWithCustomError(fixture.agreements, 'MissingCredential')
    expect((await fixture.agreements.getAgreement(agreementId)).state).to.equal(2n)
  })

  it('reports active agreements as inactive while the registry is paused', async function () {
    const fixture = await registerActiveAgreement()
    expect(await fixture.agreements.isActive(fixture.agreementId)).to.equal(true)
    await fixture.agreements.pause()
    expect(await fixture.agreements.isActive(fixture.agreementId)).to.equal(false)
    await fixture.agreements.unpause()
    expect(await fixture.agreements.isActive(fixture.agreementId)).to.equal(true)
  })
})

describe('Phase 5.5 MedTrustRoleCredential', function () {
  it('binds the registry to one non-zero immutable space scope', async function () {
    const fixture = await deployIdentityAndAgreement()
    expect(await fixture.credentials.spaceScopeDigest()).to.equal(SPACE_SCOPE_DIGEST)
    expect(await fixture.agreements.spaceScopeDigest()).to.equal(SPACE_SCOPE_DIGEST)

    const credentialFactory = await ethers.getContractFactory('MedTrustRoleCredential')
    await expect(
      ethers.deployContract('MedTrustRoleCredential', [fixture.admin.address, ethers.ZeroHash]),
    ).to.be.revertedWithCustomError(credentialFactory, 'InvalidCredential')

    const agreementFactory = await ethers.getContractFactory('MedTrustAgreementRegistry')
    await expect(
      ethers.deployContract('MedTrustAgreementRegistry', [
        fixture.admin.address,
        await fixture.credentials.getAddress(),
        ethers.id('medtrust:space:another-space'),
      ]),
    ).to.be.revertedWithCustomError(agreementFactory, 'InvalidAgreement')
  })

  it('is non-transferable, revocable, expiring, and re-issuable after revocation', async function () {
    const fixture = await deployIdentityAndAgreement()
    const tokenId = await fixture.credentials.credentialId(fixture.requester.address, ROLE.requester)
    expect(await fixture.credentials.locked(tokenId)).to.equal(true)
    expect(await fixture.credentials.hasValidCredential(fixture.requester.address, ROLE.requester)).to.equal(true)

    await expect(
      fixture.credentials.connect(fixture.requester).transferFrom(
        fixture.requester.address,
        fixture.outsider.address,
        tokenId,
      ),
    ).to.be.revertedWithCustomError(fixture.credentials, 'Soulbound')

    await fixture.credentials.revoke(tokenId, ethers.id('membership ended'))
    expect(await fixture.credentials.hasValidCredential(fixture.requester.address, ROLE.requester)).to.equal(false)

    const now = await latestTimestamp()
    await fixture.credentials.issue(
      fixture.requester.address,
      ethers.id('did:replacement'),
      ethers.id('org:replacement'),
      ROLE.requester,
      ethers.id('new review evidence'),
      now + 3600n,
    )
    expect(await fixture.credentials.hasValidCredential(fixture.requester.address, ROLE.requester)).to.equal(true)

    await increaseTime(3601)
    expect(await fixture.credentials.hasValidCredential(fixture.requester.address, ROLE.requester)).to.equal(false)
  })
})

describe('Phase 5.6 MedTrustEscrow', function () {
  async function deployEscrowFixture() {
    const fixture = await registerActiveAgreement()
    const token = await ethers.deployContract('MockSettlementToken', [
      fixture.requester.address,
      1_000_000_000n,
    ])
    const escrow = await ethers.deployContract('MedTrustEscrow', [
      fixture.admin.address,
      await token.getAddress(),
      await fixture.agreements.getAddress(),
      await fixture.credentials.getAddress(),
      SPACE_SCOPE_DIGEST,
      fixture.executionAttestor.address,
      fixture.deliveryAttestor.address,
    ])
    expect(await escrow.spaceScopeDigest()).to.equal(SPACE_SCOPE_DIGEST)
    return { ...fixture, token, escrow }
  }

  async function fund(fixture: Awaited<ReturnType<typeof deployEscrowFixture>>, suffix = '1') {
    const orderId = ethers.id(`order:${suffix}`)
    const total = 100_000_000n
    await fixture.token.connect(fixture.requester).approve(await fixture.escrow.getAddress(), total)
    const now = await latestTimestamp()
    await fixture.escrow.connect(fixture.requester).openEscrow(
      orderId,
      fixture.agreementId,
      ethers.id(`task:${suffix}`),
      50_000_000n,
      40_000_000n,
      10_000_000n,
      now + 3600n,
    )
    return { orderId, total }
  }

  it('releases claimable proceeds only after independent execution and delivery proofs', async function () {
    const fixture = await deployEscrowFixture()
    const { orderId } = await fund(fixture)
    const executionDigest = ethers.sha256(ethers.toUtf8Bytes('signed run evidence'))
    const deliveryDigest = ethers.sha256(ethers.toUtf8Bytes('approved result package'))

    await expect(
      fixture.escrow.connect(fixture.executionAttestor).attestExecution(orderId, executionDigest),
    )
      .to.emit(fixture.escrow, 'ExecutionAttested')
      .withArgs(orderId, executionDigest, fixture.executionAttestor.address)
    expect(await fixture.escrow.claimable(fixture.dataProvider.address)).to.equal(0n)
    const deliveryTransaction = fixture.escrow
      .connect(fixture.deliveryAttestor)
      .attestDelivery(orderId, deliveryDigest)
    await expect(deliveryTransaction)
      .to.emit(fixture.escrow, 'DeliveryAttested')
      .withArgs(orderId, deliveryDigest, fixture.deliveryAttestor.address)
    await expect(deliveryTransaction)
      .to.emit(fixture.escrow, 'EscrowSettled')
      .withArgs(orderId, executionDigest, deliveryDigest, 50_000_000n, 40_000_000n, 10_000_000n)

    expect(await fixture.escrow.claimable(fixture.dataProvider.address)).to.equal(50_000_000n)
    const before = await fixture.token.balanceOf(fixture.dataProvider.address)
    await fixture.escrow.connect(fixture.dataProvider).withdrawProceeds()
    expect(await fixture.token.balanceOf(fixture.dataProvider.address)).to.equal(before + 50_000_000n)
    await expect(fixture.escrow.connect(fixture.dataProvider).withdrawProceeds())
      .to.be.revertedWithCustomError(fixture.escrow, 'NothingToWithdraw')
  })

  it('rejects unauthorized, duplicate, and post-settlement attestations', async function () {
    const fixture = await deployEscrowFixture()
    const { orderId } = await fund(fixture, 'guards')
    const digest = ethers.id('evidence')

    await expect(fixture.escrow.connect(fixture.outsider).attestExecution(orderId, digest))
      .to.be.revertedWithCustomError(fixture.escrow, 'AccessControlUnauthorizedAccount')
    await fixture.escrow.connect(fixture.executionAttestor).attestExecution(orderId, digest)
    await expect(fixture.escrow.connect(fixture.executionAttestor).attestExecution(orderId, digest))
      .to.be.revertedWithCustomError(fixture.escrow, 'AttestationAlreadyRecorded')
    await fixture.escrow.connect(fixture.deliveryAttestor).attestDelivery(orderId, ethers.id('package'))
    await expect(fixture.escrow.connect(fixture.deliveryAttestor).attestDelivery(orderId, ethers.id('again')))
      .to.be.revertedWithCustomError(fixture.escrow, 'InvalidState')
  })

  it('allows a timeout refund but never both refund and settlement', async function () {
    const fixture = await deployEscrowFixture()
    const { orderId, total } = await fund(fixture, 'refund')
    const balanceAfterFunding = await fixture.token.balanceOf(fixture.requester.address)

    await expect(fixture.escrow.connect(fixture.requester).refund(orderId))
      .to.be.revertedWithCustomError(fixture.escrow, 'RefundNotAvailable')
    await increaseTime(3601)
    await expect(fixture.escrow.connect(fixture.requester).refund(orderId))
      .to.emit(fixture.escrow, 'EscrowRefunded')
      .withArgs(orderId, fixture.requester.address, total)
    expect(await fixture.token.balanceOf(fixture.requester.address)).to.equal(balanceAfterFunding + total)
    await expect(fixture.escrow.connect(fixture.executionAttestor).attestExecution(orderId, ethers.id('late')))
      .to.be.revertedWithCustomError(fixture.escrow, 'InvalidState')
  })

  it('rejects funding when any required participant credential is no longer valid', async function () {
    const fixture = await deployEscrowFixture()
    const tokenId = await fixture.credentials.credentialId(fixture.dataProvider.address, ROLE.dataProvider)
    await fixture.credentials.revoke(tokenId, ethers.id('provider suspended'))
    const orderId = ethers.id('order:missing-credential')
    await fixture.token.connect(fixture.requester).approve(await fixture.escrow.getAddress(), 3n)
    const now = await latestTimestamp()

    await expect(
      fixture.escrow.connect(fixture.requester).openEscrow(
        orderId,
        fixture.agreementId,
        ethers.id('task'),
        1n,
        1n,
        1n,
        now + 3600n,
      ),
    ).to.be.revertedWithCustomError(fixture.escrow, 'MissingCredential')
  })

  it('fails closed after funding if the agreement is suspended or a credential is revoked', async function () {
    const suspended = await deployEscrowFixture()
    const suspendedOrder = await fund(suspended, 'suspended-agreement')
    await suspended.agreements.suspend(suspended.agreementId, ethers.id('incident hold'))
    await expect(
      suspended.escrow
        .connect(suspended.executionAttestor)
        .attestExecution(suspendedOrder.orderId, ethers.id('execution')),
    ).to.be.revertedWithCustomError(suspended.escrow, 'AgreementInactive')

    const revoked = await deployEscrowFixture()
    const revokedOrder = await fund(revoked, 'revoked-after-funding')
    const modelCredential = await revoked.credentials.credentialId(
      revoked.modelProvider.address,
      ROLE.modelProvider,
    )
    await revoked.credentials.revoke(modelCredential, ethers.id('qualification withdrawn'))
    await expect(
      revoked.escrow
        .connect(revoked.deliveryAttestor)
        .attestDelivery(revokedOrder.orderId, ethers.id('delivery')),
    ).to.be.revertedWithCustomError(revoked.escrow, 'MissingCredential')
  })

  it('blocks funding and proof submission while the relevant contract is paused', async function () {
    const registryPaused = await deployEscrowFixture()
    await registryPaused.agreements.pause()
    const pausedOrderId = ethers.id('order:registry-paused')
    await registryPaused.token
      .connect(registryPaused.requester)
      .approve(await registryPaused.escrow.getAddress(), 3n)
    const now = await latestTimestamp()
    await expect(
      registryPaused.escrow.connect(registryPaused.requester).openEscrow(
        pausedOrderId,
        registryPaused.agreementId,
        ethers.id('task:registry-paused'),
        1n,
        1n,
        1n,
        now + 3600n,
      ),
    ).to.be.revertedWithCustomError(registryPaused.escrow, 'AgreementInactive')

    const registryPausedAfterFunding = await deployEscrowFixture()
    const fundedBeforeRegistryPause = await fund(registryPausedAfterFunding, 'registry-proof-paused')
    await registryPausedAfterFunding.agreements.pause()
    await expect(
      registryPausedAfterFunding.escrow
        .connect(registryPausedAfterFunding.executionAttestor)
        .attestExecution(fundedBeforeRegistryPause.orderId, ethers.id('execution:registry-paused')),
    ).to.be.revertedWithCustomError(registryPausedAfterFunding.escrow, 'AgreementInactive')

    const escrowPaused = await deployEscrowFixture()
    await escrowPaused.escrow.pause()
    const escrowPausedOrderId = ethers.id('order:escrow-paused')
    await escrowPaused.token
      .connect(escrowPaused.requester)
      .approve(await escrowPaused.escrow.getAddress(), 3n)
    await expect(
      escrowPaused.escrow.connect(escrowPaused.requester).openEscrow(
        escrowPausedOrderId,
        escrowPaused.agreementId,
        ethers.id('task:escrow-paused'),
        1n,
        1n,
        1n,
        now + 3600n,
      ),
    ).to.be.revertedWithCustomError(escrowPaused.escrow, 'EnforcedPause')

    await escrowPaused.escrow.unpause()
    const funded = await fund(escrowPaused, 'proof-paused')
    await escrowPaused.escrow.pause()
    await expect(
      escrowPaused.escrow
        .connect(escrowPaused.executionAttestor)
        .attestExecution(funded.orderId, ethers.id('execution:paused')),
    ).to.be.revertedWithCustomError(escrowPaused.escrow, 'EnforcedPause')
  })

  it('keeps execution and delivery attestor roles mutually exclusive', async function () {
    const fixture = await deployEscrowFixture()
    const attestorAdminRole = await fixture.escrow.ATTESTOR_ADMIN_ROLE()
    const executionRole = await fixture.escrow.EXECUTION_ATTESTOR_ROLE()
    const deliveryRole = await fixture.escrow.DELIVERY_ATTESTOR_ROLE()

    expect(await fixture.escrow.getRoleAdmin(executionRole)).to.equal(attestorAdminRole)
    expect(await fixture.escrow.getRoleAdmin(deliveryRole)).to.equal(attestorAdminRole)
    expect(await fixture.escrow.hasRole(attestorAdminRole, fixture.admin.address)).to.equal(true)
    await expect(fixture.escrow.connect(fixture.outsider).grantRole(executionRole, fixture.outsider.address))
      .to.be.revertedWithCustomError(fixture.escrow, 'AccessControlUnauthorizedAccount')
    await expect(fixture.escrow.grantRole(deliveryRole, fixture.executionAttestor.address))
      .to.be.revertedWithCustomError(fixture.escrow, 'AttestorRoleConflict')
      .withArgs(fixture.executionAttestor.address)
    await expect(fixture.escrow.grantRole(executionRole, fixture.deliveryAttestor.address))
      .to.be.revertedWithCustomError(fixture.escrow, 'AttestorRoleConflict')
      .withArgs(fixture.deliveryAttestor.address)

    await fixture.escrow.revokeRole(executionRole, fixture.executionAttestor.address)
    await fixture.escrow.grantRole(deliveryRole, fixture.executionAttestor.address)
    expect(await fixture.escrow.hasRole(deliveryRole, fixture.executionAttestor.address)).to.equal(true)
  })

  it('rejects deployment with one address controlling both attestation roles', async function () {
    const fixture = await registerActiveAgreement()
    const token = await ethers.deployContract('MockSettlementToken', [fixture.requester.address, 1n])
    await expect(
      ethers.deployContract('MedTrustEscrow', [
        fixture.admin.address,
        await token.getAddress(),
        await fixture.agreements.getAddress(),
        await fixture.credentials.getAddress(),
        SPACE_SCOPE_DIGEST,
        fixture.executionAttestor.address,
        fixture.executionAttestor.address,
      ]),
    ).to.be.revertedWithCustomError(await ethers.getContractFactory('MedTrustEscrow'), 'InvalidConfiguration')
  })

  it('rejects an escrow wired to a different data-space scope', async function () {
    const fixture = await registerActiveAgreement()
    const token = await ethers.deployContract('MockSettlementToken', [fixture.requester.address, 1n])
    await expect(
      ethers.deployContract('MedTrustEscrow', [
        fixture.admin.address,
        await token.getAddress(),
        await fixture.agreements.getAddress(),
        await fixture.credentials.getAddress(),
        ethers.id('medtrust:space:another-space'),
        fixture.executionAttestor.address,
        fixture.deliveryAttestor.address,
      ]),
    ).to.be.revertedWithCustomError(await ethers.getContractFactory('MedTrustEscrow'), 'InvalidConfiguration')
  })

  it('prevents one wallet from supplying both proofs after a role rotation', async function () {
    const fixture = await deployEscrowFixture()
    const { orderId } = await fund(fixture, 'rotated-attestor-reuse')
    const executionRole = await fixture.escrow.EXECUTION_ATTESTOR_ROLE()
    const deliveryRole = await fixture.escrow.DELIVERY_ATTESTOR_ROLE()
    const executionDigest = ethers.id('execution:before-role-rotation')

    await fixture.escrow
      .connect(fixture.executionAttestor)
      .attestExecution(orderId, executionDigest)
    let escrowRecord = await fixture.escrow.escrow(orderId)
    expect(escrowRecord.executionAttestor).to.equal(fixture.executionAttestor.address)

    await fixture.escrow.revokeRole(executionRole, fixture.executionAttestor.address)
    await fixture.escrow.grantRole(deliveryRole, fixture.executionAttestor.address)
    await expect(
      fixture.escrow
        .connect(fixture.executionAttestor)
        .attestDelivery(orderId, ethers.id('delivery:after-role-rotation')),
    )
      .to.be.revertedWithCustomError(fixture.escrow, 'AttestorReuse')
      .withArgs(fixture.executionAttestor.address)

    escrowRecord = await fixture.escrow.escrow(orderId)
    expect(escrowRecord.deliveryDigest).to.equal(ethers.ZeroHash)
    expect(escrowRecord.deliveryAttestor).to.equal(ethers.ZeroAddress)
    expect(escrowRecord.state).to.equal(1n)
  })
})
