import {
  ApartmentOutlined,
  AuditOutlined,
  BookOutlined,
  CloudServerOutlined,
  CodeSandboxOutlined,
  DashboardOutlined,
  FileProtectOutlined,
  FileSearchOutlined,
  LogoutOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { Avatar, Button, Dropdown, Layout, Menu, Select, Space, Tag, Tooltip, type MenuProps } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Brand } from './Brand'
import { demoRoles } from '../mock/data'
import { useDemo } from '../mock/DemoContext'
import type { RoleId } from '../mock/types'
import { useDataMode } from '../api/DataModeContext'

const { Sider, Header, Content } = Layout

const menuItems: MenuProps['items'] = [
  { key: '/overview', icon: <DashboardOutlined />, label: '工作台' },
  { key: '/products', icon: <BookOutlined />, label: '数据产品' },
  { key: '/applications', icon: <FileSearchOutlined />, label: '使用申请' },
  { key: '/contracts', icon: <FileProtectOutlined />, label: '数字合约' },
  { key: '/compute', icon: <CodeSandboxOutlined />, label: '可信计算' },
  { key: '/connectors', icon: <CloudServerOutlined />, label: '节点中心' },
  { key: '/audit', icon: <AuditOutlined />, label: '审计中心' },
  { type: 'divider' },
  { key: '/governance', icon: <ApartmentOutlined />, label: '空间治理' },
]

const titleByPath: Record<string, string> = {
  overview: '工作台',
  products: '数据产品',
  applications: '使用申请',
  contracts: '数字合约',
  compute: '可信计算',
  connectors: '节点中心',
  audit: '审计中心',
  governance: '空间治理',
}

export function AppShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const { role, setRole, resetDemo } = useDemo()
  const { isApi } = useDataMode()
  const rootPath = `/${location.pathname.split('/')[1] || 'overview'}`
  const pageTitle = titleByPath[location.pathname.split('/')[1]] ?? 'MedTrust Space'

  const userMenu: MenuProps = {
    items: [
      {
        key: 'reset',
        icon: <ReloadOutlined />,
        label: '重置当前流程',
        onClick: () => {
          if (isApi) window.location.reload()
          else resetDemo()
          navigate('/overview')
        },
      },
      {
        key: 'logout',
        icon: <LogoutOutlined />,
        label: '返回登录',
        onClick: () => navigate('/demo-login'),
      },
    ],
  }

  return (
    <Layout className="app-shell">
      <Sider width={248} className="app-sider" breakpoint="lg" collapsedWidth={72}>
        <div className="app-sider__brand"><Brand /></div>
        <div className="space-badge">
          <SafetyCertificateOutlined />
          <div>
            <span>当前逻辑空间</span>
            <strong>数字病理 AI 协作空间</strong>
          </div>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[rootPath]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          className="app-menu"
        />
        <div className="app-sider__foot">
          <span className="status-dot" /> 服务平台运行中
          <small>{isApi ? '后端服务已连接' : '交互预览'}</small>
        </div>
      </Sider>
      <Layout>
        <Header className="app-header">
          <div>
            <div className="app-header__title">{pageTitle}</div>
            <div className="app-header__crumb">MedTrust Space / {pageTitle}</div>
          </div>
          <Space size={14}>
            <Tag color={isApi ? 'green' : 'blue'} variant="filled">{isApi ? '后端服务' : '预览模式'}</Tag>
            <div className="role-switcher">
              <span>当前身份</span>
              <Select
                value={role.id}
                onChange={(value) => setRole(value as RoleId)}
                options={demoRoles.map((item) => ({ label: item.name, value: item.id }))}
                popupMatchSelectWidth={210}
                variant="borderless"
              />
            </div>
            <Tooltip title="系统状态正常">
              <div className="trust-indicator"><span /> 可信链路正常</div>
            </Tooltip>
            <Dropdown menu={userMenu} placement="bottomRight">
              <Button type="text" className="user-button">
                <Avatar size={34}>{role.name.slice(0, 1)}</Avatar>
                <span>{role.shortName}</span>
              </Button>
            </Dropdown>
          </Space>
        </Header>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
