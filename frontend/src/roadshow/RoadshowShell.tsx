import {
  ApartmentOutlined,
  AuditOutlined,
  CloudServerOutlined,
  CodeSandboxOutlined,
  DatabaseOutlined,
  FileDoneOutlined,
  FileSearchOutlined,
  FileProtectOutlined,
  FormOutlined,
  LogoutOutlined,
  RobotOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { Avatar, Button, Dropdown, Layout, Menu, Select, Space, type MenuProps } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Brand } from '../components/Brand'
import { roleProfiles, useRoadshow } from './RoadshowContext'
import type { DemoIdentity } from './types'
import { RoleAssistant } from './RoleAssistant'

const { Sider, Header, Content } = Layout

const common = {
  roadshow: { key: '/roadshow', icon: <PlayCircleOutlined />, label: '统一路演总览' },
  overview: { key: '/overview', icon: <ApartmentOutlined />, label: '角色工作台' },
  data: { key: '/data-catalog', icon: <DatabaseOutlined />, label: '数据商城' },
  dataProducts: { key: '/data-products', icon: <FormOutlined />, label: '数据产品管理' },
  models: { key: '/model-catalog', icon: <RobotOutlined />, label: '模型商城' },
  modelProducts: { key: '/model-products', icon: <FormOutlined />, label: '我的模型产品' },
  applications: { key: '/applications', icon: <FileSearchOutlined />, label: '计算需求' },
  workflow: { key: '/workflow', icon: <FileDoneOutlined />, label: '协作流程' },
  contracts: { key: '/contracts', icon: <FileProtectOutlined />, label: '数字合约' },
  execution: { key: '/execution', icon: <CodeSandboxOutlined />, label: '受控执行' },
  results: { key: '/results', icon: <SafetyCertificateOutlined />, label: '结果审核与下载' },
  audit: { key: '/audit', icon: <AuditOutlined />, label: '审计与基础设施' },
  lifecycle: { key: '/lifecycle', icon: <ApartmentOutlined />, label: '产品生命周期' },
  externalCatalog: { key: '/external-catalog/datasets', icon: <DatabaseOutlined />, label: '公共候选数据目录' },
  externalCatalogSync: { key: '/portal/operator/external-catalog', icon: <CloudServerOutlined />, label: '外部目录同步' },
  externalModels: { key: '/external-catalog/models', icon: <RobotOutlined />, label: '公共候选模型目录' },
  externalModelSync: { key: '/portal/operator/external-model-catalog', icon: <CloudServerOutlined />, label: '模型目录同步' },
  externalModelGovernance: { key: '/external-catalog/models/governance', icon: <SafetyCertificateOutlined />, label: '公共模型治理' },
  externalModelGovernanceOps: { key: '/portal/operator/external-model-catalog/governance', icon: <SafetyCertificateOutlined />, label: '模型治理工作台' },
  datasetModelEvidence: { key: '/portal/operator/dataset-model-evidence', icon: <ApartmentOutlined />, label: '数据—模型证据' },
  materializationPlans: { key: '/portal/operator/materialization-plans', icon: <SafetyCertificateOutlined />, label: '物化计划' },
  connectorControl: { key: '/portal/operator/connectors', icon: <CloudServerOutlined />, label: 'Hospital Connector' },
  policyControl: { key: '/portal/operator/policy-control', icon: <FileProtectOutlined />, label: 'Policy Control' },
  hospitalConnectors: { key: '/portal/hospital/connectors', icon: <CloudServerOutlined />, label: '本组织 Connector' },
  hospitalPolicyControl: { key: '/portal/hospital/policy-control', icon: <FileProtectOutlined />, label: '本组织 Policy Control' },
}

const roleMenus: Record<DemoIdentity, MenuProps['items']> = {
  space_operator: [
    { ...common.roadshow, label: '平台总览' },
    { ...common.overview, label: '运营工作台' },
    common.data,
    common.models,
    { ...common.applications, label: '服务申请审批' },
    { ...common.results, label: '结果中心' },
  ],
  data_provider: [
    { ...common.overview, label: '工作台' },
    common.data,
    { ...common.dataProducts, label: '数据产品管理' },
    { ...common.applications, label: '数据授权与使用审批' },
    { ...common.execution, label: '执行准备' },
    { ...common.results, label: '结果审核' },
  ],
  model_provider: [
    { ...common.overview, label: '工作台' },
    common.models,
    { ...common.modelProducts, label: '模型产品管理' },
    { ...common.applications, label: '模型授权与使用审批' },
    { ...common.execution, label: '执行准备' },
    { ...common.results, label: '结果审核' },
  ],
  data_requester: [
    { ...common.overview, label: '工作台' },
    common.data,
    common.models,
    { ...common.applications, label: '我的申请' },
    { ...common.execution, label: '执行进度' },
    { ...common.results, label: '结果下载' },
  ],
}

const titleByPath: Record<string, string> = {
  roadshow: '全链路路演',
  overview: '角色工作台', 'data-catalog': '数据商城', 'data-products': '数据产品管理', 'model-catalog': '模型商城', 'model-products': '模型产品管理',
  applications: '计算需求与组合申请',
  workflow: '多主体协作流程', contracts: '数字合约', execution: '受控执行',
  results: '结果审核与下载', audit: '审计与基础设施',
  lifecycle: '产品生命周期',
  'external-catalog': '公共候选数据目录',
  portal: '外部目录同步',
  '/portal/operator/external-catalog': '公共候选数据目录',
  '/portal/operator/external-model-catalog': '公共候选模型目录',
  '/portal/operator/connectors': 'Hospital Connector 控制与证据中心',
  '/portal/hospital/connectors': '本组织 Connector 控制与证据中心',
  '/portal/operator/policy-control': 'Policy Control',
  '/portal/hospital/policy-control': '本组织 Policy Control',
}

export function RoadshowShell() {
  const icpNumber = import.meta.env.VITE_ICP_NUMBER?.trim()
  const navigate = useNavigate()
  const location = useLocation()
  const { identity, setIdentity, logout, contextError, roadshow, exitRoadshow } = useRoadshow()
  const profile = roleProfiles[identity]
  const segment = location.pathname.split('/')[1] || 'overview'
  const dataMarketplaceRoute = location.pathname.startsWith('/external-catalog/datasets')
    || location.pathname.startsWith('/portal/operator/external-catalog')
    || (identity === 'space_operator' && location.pathname.startsWith('/data-products'))
  const modelMarketplaceRoute = location.pathname.startsWith('/external-catalog/models')
    || location.pathname.startsWith('/portal/operator/external-model-catalog')
    || (identity === 'space_operator' && location.pathname.startsWith('/model-products'))
  const selected = dataMarketplaceRoute
    ? '/data-catalog'
    : modelMarketplaceRoute
      ? '/model-catalog'
      : location.pathname.startsWith('/portal/')
        ? location.pathname
        : `/${segment}`
  const userMenu: MenuProps = { items: [{ key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: async () => { await logout(); navigate('/demo-login') } }] }
  const debugRoleSwitch = import.meta.env.VITE_ENABLE_DEMO_ROLE_SWITCH === 'true'
  const pageTitle = identity === 'data_requester' && segment === 'applications'
    ? '我的申请'
    : titleByPath[location.pathname] || titleByPath[segment] || 'MedTrust Space'

  return <Layout className="app-shell phase4-shell">
    <Sider width={232} className="app-sider" breakpoint="lg" collapsedWidth={72}>
      <div className="app-sider__brand"><Brand /></div>
      <Menu mode="inline" selectedKeys={[selected]} items={roleMenus[identity]} onClick={({ key }) => navigate(key)} className="app-menu" />
      {icpNumber && <div className="app-sider__foot"><a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">{icpNumber}</a></div>}
    </Sider>
    <Layout>
      <Header className="app-header phase4-header">
        <div className="app-header__title">{pageTitle}</div>
        <Space size={12} wrap>
          {debugRoleSwitch && <div className="role-switcher"><span>调试显示</span><Select value={identity} onChange={(value) => { setIdentity(value); if (!roadshow.enabled) navigate('/overview') }} options={(Object.keys(roleProfiles) as DemoIdentity[]).map((key) => ({ value: key, label: roleProfiles[key].shortLabel }))} popupMatchSelectWidth={210} variant="borderless" /></div>}
          <Dropdown menu={userMenu}><Button type="text" className="user-button"><Avatar style={{ background: profile.color }}>{profile.shortLabel.slice(0, 1)}</Avatar><span>{profile.shortLabel}</span></Button></Dropdown>
        </Space>
      </Header>
      {contextError && <div className="phase4-context-error">{contextError}</div>}
      {roadshow.enabled && segment !== 'roadshow' && <div className="phase58-persistent">
        <span><PlayCircleOutlined /> 路演会话</span>
        <strong>{roadshow.mode === '8min' ? '8 分钟主路演' : '15 分钟完整演示'}</strong>
        <code>{roadshow.applicationId.slice(0, 8)}</code>
        <Button size="small" onClick={() => navigate('/roadshow')}>返回主链</Button>
        <Button size="small" type="text" onClick={exitRoadshow}>退出</Button>
      </div>}
      <Content className="app-content"><Outlet /></Content>
      <RoleAssistant />
    </Layout>
  </Layout>
}
