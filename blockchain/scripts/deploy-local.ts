import { network } from 'hardhat'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const connection = await network.create()
const { ethers, networkName } = connection

const signers = await ethers.getSigners()
if (signers.length < 7) {
  throw new Error('At least seven unlocked local accounts are required')
}

const [admin, requester, dataProvider, modelProvider, operator, executionAttestor, deliveryAttestor] = signers
const initialSupply = ethers.parseUnits('1000000', 6)
const spaceScopeDigest = ethers.id('medtrust:space:0967d9f7-509a-5583-b214-df66c2eae6de')

// Keep this order stable: later contracts depend on the addresses deployed before them.
const roleCredential = await ethers.deployContract('MedTrustRoleCredential', [
  admin.address,
  spaceScopeDigest,
])
await roleCredential.waitForDeployment()

const agreementRegistry = await ethers.deployContract('MedTrustAgreementRegistry', [
  admin.address,
  await roleCredential.getAddress(),
  spaceScopeDigest,
])
await agreementRegistry.waitForDeployment()

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

// The roadshow operator submits credential issuance and agreement-registration
// transactions from the browser wallet. The deployer keeps the admin role for
// emergency recovery, while the application still requires server-side review
// and exact receipt verification before any platform authority is mirrored.
await (
  await roleCredential.grantRole(await roleCredential.ISSUER_ROLE(), operator.address)
).wait()
await (
  await agreementRegistry.grantRole(await agreementRegistry.REGISTRAR_ROLE(), operator.address)
).wait()

const chainId = (await ethers.provider.getNetwork()).chainId
const deployment = {
  network: networkName,
  chainId: chainId.toString(),
  spaceScopeDigest,
  contracts: {
    roleCredential: await roleCredential.getAddress(),
    agreementRegistry: await agreementRegistry.getAddress(),
    settlementToken: await settlementToken.getAddress(),
    escrow: await escrow.getAddress(),
  },
  localDemoAccounts: {
    admin: admin.address,
    requester: requester.address,
    dataProvider: dataProvider.address,
    modelProvider: modelProvider.address,
    operator: operator.address,
    executionAttestor: executionAttestor.address,
    deliveryAttestor: deliveryAttestor.address,
  },
  mockSettlementToken: {
    symbol: 'mCNY',
    decimals: 6,
    initialHolder: requester.address,
    initialSupply: initialSupply.toString(),
  },
}

const deploymentDirectory = resolve(process.cwd(), 'deployments')
mkdirSync(deploymentDirectory, { recursive: true })
const deploymentPath = resolve(deploymentDirectory, 'localhost.json')
writeFileSync(deploymentPath, `${JSON.stringify(deployment, null, 2)}\n`, { encoding: 'utf8' })

console.log('MedTrust local contracts deployed in dependency order.')
console.log(`Deployment manifest: ${deploymentPath}`)
console.log(JSON.stringify(deployment, null, 2))
