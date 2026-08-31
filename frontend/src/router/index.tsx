import { Navigate, createBrowserRouter } from 'react-router-dom'
import { Result } from 'antd'
import { AppShell } from '../components/AppShell'
import { ApplicationsPage } from '../pages/ApplicationsPage'
import { AuditPage } from '../pages/AuditPage'
import { ComputePage } from '../pages/ComputePage'
import { ConnectorsPage } from '../pages/ConnectorsPage'
import { ContractsPage } from '../pages/ContractsPage'
import { DemoLoginPage } from '../pages/DemoLoginPage'
import { GovernancePage } from '../pages/GovernancePage'
import { NotFoundPage } from '../pages/NotFoundPage'
import { OverviewPage } from '../pages/OverviewPage'
import { ProductDetailPage } from '../pages/ProductDetailPage'
import { ProductsPage } from '../pages/ProductsPage'
import { useDataMode } from '../api/DataModeContext'
import {
  ApiAuditPage,
  ApiComputePage,
  ApiConnectorsPage,
  ApiContractsPage,
  ApiOverviewPage,
  ApiProductDetailPage,
  ApiProductsPage,
} from '../api/ApiPages'
import type { ReactNode } from 'react'
import { RoadshowShell } from '../roadshow/RoadshowShell'
import { RoadshowLoginPage } from '../roadshow/RoadshowLoginPage'
import {
  RoadshowAuditPage,
  RoadshowContractPage,
  RoadshowOverviewPage,
  RoadshowWorkflowPage,
} from '../roadshow/RoadshowPages'
import {
  ResultReleaseDetailPage,
  ResultReleaseListPage,
} from '../roadshow/ResultReleasePages'
import {
  DataProductDetailPage,
  DataProductFormPage,
  DataProductManagementPage,
  PublishedDataCatalogPage,
} from '../roadshow/DataProductLifecyclePages'
import {
  ModelProductDetailPage,
  ModelProductFormPage,
  ModelProductManagementPage,
  PublishedModelCatalogPage,
} from '../roadshow/ModelProductLifecyclePages'
import {
  ApplicationDetailPage,
  ApplicationManagementPage,
  ApplicationWizardPage,
} from '../roadshow/ApplicationLifecyclePages'
import {
  ContractDetailPage,
  ContractManagementPage,
} from '../roadshow/ContractLifecyclePages'
import {
  ExecutionReadinessDetailPage,
  ExecutionReadinessListPage,
} from '../roadshow/ExecutionReadinessPages'
import { RoadshowExperiencePage } from '../roadshow/RoadshowExperiencePage'
import { ProductLifecycleReviewPage } from '../roadshow/ProductLifecycleGovernance'
import { useRoadshow } from '../roadshow/RoadshowContext'
import type { DemoIdentity } from '../roadshow/types'
import { JoinPage } from '../roadshow/JoinPage'
import { PortalEntry } from '../roadshow/PortalEntry'
import { ExternalCatalogPage } from '../roadshow/ExternalCatalogPages'
import { ExternalGovernancePage } from '../roadshow/ExternalGovernancePages'
import { ExternalModelCatalogPage } from '../roadshow/ExternalModelCatalogPages'
import { ExternalModelGovernancePage } from '../roadshow/ExternalModelGovernancePages'
import { DatasetModelEvidencePage } from '../roadshow/DatasetModelEvidencePages'
import { MaterializationPlanPage } from '../roadshow/MaterializationPlanPages'
import { RoadshowSealPage } from '../roadshow/RoadshowSealPage'
import { ConnectorControlPage } from '../roadshow/ConnectorControlPages'
import { PolicyControlPage } from '../roadshow/PolicyControlPages'
import { CommercialCheckoutPage } from '../roadshow/CommercialCheckoutPage'

function ModePage({ mock, api }: { mock: ReactNode; api: ReactNode }) {
  const { isApi } = useDataMode()
  return isApi ? api : mock
}

function ModeShell() {
  const { isApi } = useDataMode()
  return isApi ? <RoadshowShell /> : <AppShell />
}

function ModeLogin() {
  const { isApi } = useDataMode()
  return isApi ? <RoadshowLoginPage /> : <DemoLoginPage />
}

function ProtectedShell() {
  const { isApi } = useDataMode()
  const { authenticated, authLoading } = useRoadshow()
  if (!isApi) return <ModeShell />
  if (authLoading) return null
  return authenticated ? <ModeShell /> : <Navigate to="/demo-login" replace />
}

function RoleGuard({ allowed, children }: { allowed: DemoIdentity[]; children: ReactNode }) {
  const { isApi } = useDataMode()
  const { identity } = useRoadshow()
  if (!isApi || allowed.includes(identity)) return children
  return <Result status="403" title="无权访问" subTitle="当前账号没有该门户页面的访问权限。" />
}

const operatorAndData = ['space_operator', 'data_provider'] satisfies DemoIdentity[]
const operatorAndModel = ['space_operator', 'model_provider'] satisfies DemoIdentity[]
const lifecycleRoles = ['space_operator', 'data_provider', 'model_provider'] satisfies DemoIdentity[]
const dataCatalogRoles = ['space_operator', 'data_provider', 'model_provider', 'data_requester'] satisfies DemoIdentity[]
const modelCatalogRoles = ['space_operator', 'data_provider', 'model_provider', 'data_requester'] satisfies DemoIdentity[]

export const router = createBrowserRouter([
  { path: '/', element: <Navigate to="/demo-login" replace /> },
  { path: '/demo-login', element: <ModeLogin /> },
  { path: '/join', element: <JoinPage /> },
  { path: '/portal/hospital', element: <PortalEntry role="data_provider" /> },
  { path: '/portal/model-provider', element: <PortalEntry role="model_provider" /> },
  { path: '/portal/requester', element: <PortalEntry role="data_requester" /> },
  { path: '/portal/operator', element: <PortalEntry role="space_operator" /> },
  {
    element: <ProtectedShell />,
    children: [
      { path: '/roadshow', element: <RoadshowSealPage /> },
      { path: '/roadshow/workflow', element: <RoadshowExperiencePage /> },
      { path: '/overview', element: <ModePage mock={<OverviewPage />} api={<RoadshowOverviewPage />} /> },
      { path: '/data-catalog', element: <RoleGuard allowed={dataCatalogRoles}><PublishedDataCatalogPage /></RoleGuard> },
      { path: '/external-catalog/datasets', element: <ExternalCatalogPage /> },
      { path: '/portal/operator/external-catalog', element: <RoleGuard allowed={['space_operator']}><ExternalCatalogPage operator /></RoleGuard> },
      { path: '/external-catalog/models', element: <RoleGuard allowed={modelCatalogRoles}><ExternalModelCatalogPage /></RoleGuard> },
      { path: '/portal/operator/external-model-catalog', element: <RoleGuard allowed={['space_operator']}><ExternalModelCatalogPage operator /></RoleGuard> },
      { path: '/external-catalog/models/governance', element: <RoleGuard allowed={modelCatalogRoles}><ExternalModelGovernancePage /></RoleGuard> },
      { path: '/portal/operator/external-model-catalog/governance', element: <RoleGuard allowed={['space_operator']}><ExternalModelGovernancePage operator /></RoleGuard> },
      { path: '/external-catalog/governance', element: <ExternalGovernancePage /> },
      { path: '/portal/operator/external-catalog/governance', element: <RoleGuard allowed={['space_operator']}><ExternalGovernancePage operator /></RoleGuard> },
      { path: '/portal/operator/dataset-model-evidence', element: <RoleGuard allowed={['space_operator']}><DatasetModelEvidencePage /></RoleGuard> },
      { path: '/portal/operator/materialization-plans', element: <RoleGuard allowed={['space_operator']}><MaterializationPlanPage /></RoleGuard> },
      { path: '/portal/operator/connectors', element: <RoleGuard allowed={['space_operator']}><ConnectorControlPage /></RoleGuard> },
      { path: '/portal/hospital/connectors', element: <RoleGuard allowed={['data_provider']}><ConnectorControlPage /></RoleGuard> },
      { path: '/portal/operator/policy-control', element: <RoleGuard allowed={['space_operator']}><PolicyControlPage /></RoleGuard> },
      { path: '/portal/hospital/policy-control', element: <RoleGuard allowed={['data_provider']}><PolicyControlPage /></RoleGuard> },
      { path: '/data-products', element: <RoleGuard allowed={operatorAndData}><DataProductManagementPage /></RoleGuard> },
      { path: '/data-products/new', element: <RoleGuard allowed={['data_provider']}><DataProductFormPage /></RoleGuard> },
      { path: '/data-products/:versionId', element: <RoleGuard allowed={dataCatalogRoles}><DataProductDetailPage /></RoleGuard> },
      { path: '/data-products/:versionId/edit', element: <RoleGuard allowed={['data_provider']}><DataProductFormPage /></RoleGuard> },
      { path: '/model-catalog', element: <RoleGuard allowed={modelCatalogRoles}><PublishedModelCatalogPage /></RoleGuard> },
      { path: '/model-products', element: <RoleGuard allowed={operatorAndModel}><ModelProductManagementPage /></RoleGuard> },
      { path: '/model-products/new', element: <RoleGuard allowed={['model_provider']}><ModelProductFormPage /></RoleGuard> },
      { path: '/model-products/:versionId', element: <RoleGuard allowed={modelCatalogRoles}><ModelProductDetailPage /></RoleGuard> },
      { path: '/model-products/:versionId/edit', element: <RoleGuard allowed={['model_provider']}><ModelProductFormPage /></RoleGuard> },
      { path: '/workflow', element: <RoadshowWorkflowPage /> },
      { path: '/execution', element: <ExecutionReadinessListPage /> },
      { path: '/execution/:contractId', element: <ExecutionReadinessDetailPage /> },
      { path: '/results', element: <ResultReleaseListPage /> },
      { path: '/results/:artifactId', element: <ResultReleaseDetailPage /> },
      { path: '/lifecycle', element: <RoleGuard allowed={lifecycleRoles}><ProductLifecycleReviewPage /></RoleGuard> },
      { path: '/products', element: <ModePage mock={<ProductsPage />} api={<ApiProductsPage />} /> },
      { path: '/products/:id', element: <ModePage mock={<ProductDetailPage />} api={<ApiProductDetailPage />} /> },
      { path: '/applications', element: <ModePage mock={<ApplicationsPage />} api={<ApplicationManagementPage />} /> },
      { path: '/applications/new', element: <RoleGuard allowed={['data_requester']}><ApplicationWizardPage /></RoleGuard> },
      { path: '/applications/:applicationId', element: <ApplicationDetailPage /> },
      { path: '/applications/:applicationId/edit', element: <RoleGuard allowed={['data_requester']}><ApplicationWizardPage /></RoleGuard> },
      { path: '/commercial-checkout/:orderId', element: <RoleGuard allowed={['data_requester']}><CommercialCheckoutPage /></RoleGuard> },
      { path: '/contracts', element: <ModePage mock={<ContractsPage />} api={<ContractManagementPage />} /> },
      { path: '/contracts/:contractId', element: <ContractDetailPage /> },
      { path: '/compute', element: <ModePage mock={<ComputePage />} api={<ApiComputePage />} /> },
      { path: '/connectors', element: <ModePage mock={<ConnectorsPage />} api={<ApiConnectorsPage />} /> },
      { path: '/audit', element: <ModePage mock={<AuditPage />} api={<RoadshowAuditPage />} /> },
      { path: '/governance', element: <RoleGuard allowed={['space_operator']}><GovernancePage /></RoleGuard> },
    ],
  },
  { path: '*', element: <NotFoundPage /> },
])
