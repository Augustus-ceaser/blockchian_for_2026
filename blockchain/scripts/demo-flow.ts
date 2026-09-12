import assert from 'node:assert/strict'
import { network } from 'hardhat'

const connection = await network.create()
const { ethers, networkName } = connection

const signers = await ethers.getSigners()
if (signers.length < 7) {
  throw new Error('At least seven unlocked local accounts are required')
}

const [admin, requester, dataProvider, modelProvider, operator, executionAttestor, deliveryAttestor] = signers

const role = {
  requester: ethers.id('data_requester'),
  dataProvider: ethers.id('data_provider'),
  modelProvider: ethers.id('model_provider'),
  operator: ethers.id('space_operator'),
} as const
const spaceScopeDigest = ethers.id('medtrust:space:0967d9f7-509a-5583-b214-df66c2eae6de')

async function latestTimestamp(): Promise<bigint> {
  const block = await ethers.provider.getBlock('latest')
  if (!block) throw new Error('Latest block is unavailable')
  return BigInt(block.timestamp)
}

async function waitForTransaction(transaction: Promise<{ wait(): Promise<unknown> }>): Promise<void> {
  const response = await transaction
  await response.wait()
}

function displayAmount(amount: bigint): string {
  return `${ethers.formatUnits(amount, 6)} mCNY`
}

console.log(`[network] ${networkName}`)
console.log('[deploy] RoleCredential -> AgreementRegistry -> MockSettlementToken -> Escrow')

const roleCredential = await ethers.deployContract('MedTrustRoleCredential', [
  admin.address,
  spaceScopeDigest,
])
await roleCredential.waitForDeployment()
assert.equal(await roleCredential.spaceScopeDigest(), spaceScopeDigest, 'credential scope should be immutable')

const agreementRegistry = await ethers.deployContract('MedTrustAgreementRegistry', [
  admin.address,
  await roleCredential.getAddress(),
  spaceScopeDigest,
])
await agreementRegistry.waitForDeployment()
assert.equal(await agreementRegistry.spaceScopeDigest(), spaceScopeDigest, 'agreement scope should be immutable')

const initialSupply = ethers.parseUnits('1000000', 6)
const settlementToken = await ethers.deployContract('MockSettlementToken', [requester.address, initialSupply])
await settlementToken.waitForDeployment()

const escrow = await ethers.deployContract('MedTrustEscrow', [
  admin.address,
  await settlementToken.getAddress(),
  await agreementRegistry.getAddress(),
  await roleCredential.getAddress(),
  spaceScopeDigest,
  executionAttestor.address,
  deliveryAttestor.address,
])
await escrow.waitForDeployment()
assert.equal(await escrow.spaceScopeDigest(), spaceScopeDigest, 'escrow scope should be immutable')

console.log('[1/5] Issue four role credentials')
const issuedAt = await latestTimestamp()
const credentialExpiry = issuedAt + 7n * 24n * 60n * 60n
const participants = [
  { signer: requester, role: role.requester, label: 'requester' },
  { signer: dataProvider, role: role.dataProvider, label: 'data provider' },
  { signer: modelProvider, role: role.modelProvider, label: 'model provider' },
  { signer: operator, role: role.operator, label: 'space operator' },
] as const

for (const participant of participants) {
  await waitForTransaction(
    roleCredential.issue(
      participant.signer.address,
      ethers.id(`did:medtrust:demo:${participant.signer.address}`),
      ethers.id(`organization:demo:${participant.label}`),
      participant.role,
      ethers.sha256(ethers.toUtf8Bytes(`local admission evidence:${participant.label}`)),
      credentialExpiry,
    ),
  )
  assert.equal(
    await roleCredential.hasValidCredential(participant.signer.address, participant.role),
    true,
    `${participant.label} credential should be valid`,
  )
}

console.log('[2/5] Register the frozen contract digest and collect 4-of-4 confirmations')
const agreementId = ethers.id('medtrust:local-demo:agreement:001')
const termsDigest = ethers.sha256(
  ethers.toUtf8Bytes('MedTrust local demo agreement v1: immutable canonical terms'),
)
const agreementStart = await latestTimestamp()
await waitForTransaction(
  agreementRegistry.registerAgreement(
    agreementId,
    termsDigest,
    requester.address,
    dataProvider.address,
    modelProvider.address,
    operator.address,
    agreementStart,
    agreementStart + 24n * 60n * 60n,
  ),
)

for (const signer of [requester, dataProvider, modelProvider]) {
  await waitForTransaction((agreementRegistry.connect(signer) as typeof agreementRegistry).confirm(agreementId))
}

let agreement = await agreementRegistry.getAgreement(agreementId)
assert.equal(agreement.confirmationBitmap, 7n, 'The first three parties should set bitmap 0b0111')
assert.equal(agreement.state, 1n, 'The agreement should remain Proposed before the operator confirms')

await waitForTransaction((agreementRegistry.connect(operator) as typeof agreementRegistry).confirm(agreementId))
agreement = await agreementRegistry.getAgreement(agreementId)
assert.equal(agreement.confirmationBitmap, 15n, 'All four confirmation bits should be set')
assert.equal(agreement.state, 3n, 'The fourth confirmation should atomically activate the agreement')
assert.equal(await agreementRegistry.isActive(agreementId), true, 'The agreement should be active')

console.log('[3/5] Fund escrow with the local mock token')
const orderId = ethers.id('medtrust:local-demo:order:001')
const taskDigest = ethers.sha256(ethers.toUtf8Bytes('controlled compute task manifest v1'))
const dataFee = ethers.parseUnits('500', 6)
const modelFee = ethers.parseUnits('300', 6)
const platformFee = ethers.parseUnits('20', 6)
const total = dataFee + modelFee + platformFee
const refundAfter = (await latestTimestamp()) + 60n * 60n

await waitForTransaction(
  (settlementToken.connect(requester) as typeof settlementToken).approve(await escrow.getAddress(), total),
)
await waitForTransaction(
  (escrow.connect(requester) as typeof escrow).openEscrow(
    orderId,
    agreementId,
    taskDigest,
    dataFee,
    modelFee,
    platformFee,
    refundAfter,
  ),
)

let escrowRecord = await escrow.escrow(orderId)
assert.equal(escrowRecord.state, 1n, 'Escrow should be Funded')
assert.equal(await settlementToken.balanceOf(await escrow.getAddress()), total, 'Escrow should custody the full fee')

console.log('[4/5] Submit independent execution and delivery evidence digests')
const executionDigest = ethers.sha256(ethers.toUtf8Bytes('signed execution receipt v1'))
const deliveryDigest = ethers.sha256(ethers.toUtf8Bytes('approved delivery package v1'))

await waitForTransaction(
  (escrow.connect(executionAttestor) as typeof escrow).attestExecution(orderId, executionDigest),
)
assert.equal(await escrow.claimable(dataProvider.address), 0n, 'One proof must not release funds')

await waitForTransaction(
  (escrow.connect(deliveryAttestor) as typeof escrow).attestDelivery(orderId, deliveryDigest),
)
escrowRecord = await escrow.escrow(orderId)
assert.equal(escrowRecord.state, 2n, 'Two proofs should settle escrow')
assert.equal(await escrow.claimable(dataProvider.address), dataFee)
assert.equal(await escrow.claimable(modelProvider.address), modelFee)
assert.equal(await escrow.claimable(operator.address), platformFee)

console.log('[5/5] Verify and withdraw pull-payment balances')
const withdrawable = {
  dataProvider: await escrow.claimable(dataProvider.address),
  modelProvider: await escrow.claimable(modelProvider.address),
  operator: await escrow.claimable(operator.address),
}

await waitForTransaction((escrow.connect(dataProvider) as typeof escrow).withdrawProceeds())
await waitForTransaction((escrow.connect(modelProvider) as typeof escrow).withdrawProceeds())
await waitForTransaction((escrow.connect(operator) as typeof escrow).withdrawProceeds())

assert.equal(await settlementToken.balanceOf(dataProvider.address), dataFee)
assert.equal(await settlementToken.balanceOf(modelProvider.address), modelFee)
assert.equal(await settlementToken.balanceOf(operator.address), platformFee)
assert.equal(await settlementToken.balanceOf(await escrow.getAddress()), 0n)

console.log('\nDemo succeeded:')
console.log(`  agreement: ${agreementId}`)
console.log(`  confirmations: ${agreement.confirmationBitmap.toString()}/15 (4-of-4), state=Active`)
console.log(`  escrow order: ${orderId}, state=Settled`)
console.log(`  data provider withdrawable: ${displayAmount(withdrawable.dataProvider)}`)
console.log(`  model provider withdrawable: ${displayAmount(withdrawable.modelProvider)}`)
console.log(`  platform withdrawable: ${displayAmount(withdrawable.operator)}`)
console.log(`  total mock settlement: ${displayAmount(total)}`)
console.log('  all three pull-payment withdrawals: verified')
