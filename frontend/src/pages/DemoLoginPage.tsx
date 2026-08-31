import {
  BankOutlined,
  ExperimentOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Button } from 'antd'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Brand } from '../components/Brand'
import { demoRoles } from '../mock/data'
import { useDemo } from '../mock/DemoContext'
import type { RoleId } from '../mock/types'

const roleIcons = {
  hospital: <BankOutlined />,
  research: <ExperimentOutlined />,
  ai: <RobotOutlined />,
  operator: <TeamOutlined />,
}

export function DemoLoginPage() {
  const navigate = useNavigate()
  const { role, setRole } = useDemo()
  const [selected, setSelected] = useState<RoleId>(role.id)

  const enterDemo = () => {
    setRole(selected)
    navigate('/overview')
  }

  return (
    <main className="login-page">
      <section className="login-hero">
        <div className="login-hero__inner">
          <Brand />
          <div className="login-hero__eyebrow">DIGITAL PATHOLOGY TRUSTED COLLABORATION</div>
          <h1>让医疗数据按约使用，<br />让每一步都有证据。</h1>
          <p>
            MedTrust Space 连接数据提供方、使用方与服务方，贯通数据产品发现、受控计算和审计追溯。
          </p>
          <div className="trust-principles">
            <div><SafetyCertificateOutlined /><span><strong>数据不被随意搬走</strong>受控计算替代原始数据下载</span></div>
            <div><SafetyCertificateOutlined /><span><strong>规则不是一纸说明</strong>数字合约约束每次使用行为</span></div>
            <div><SafetyCertificateOutlined /><span><strong>过程不是黑盒</strong>申请、履约、审查全程可追溯</span></div>
          </div>
        </div>
      </section>
      <section className="login-panel">
        <div className="login-panel__inner">
          <h2>选择身份</h2>
          <p>不同身份拥有不同待办和操作权限。</p>
          <div className="role-grid">
            {demoRoles.map((item) => (
              <button
                type="button"
                className={`role-card ${selected === item.id ? 'is-selected' : ''}`}
                key={item.id}
                onClick={() => setSelected(item.id)}
              >
                <span className="role-card__icon">{roleIcons[item.id]}</span>
                <span className="role-card__content">
                  <strong>{item.name}</strong>
                  <small>{item.organization}</small>
                  <span>{item.description}</span>
                </span>
                <span className="role-card__check">✓</span>
              </button>
            ))}
          </div>
          <Button type="primary" size="large" block onClick={enterDemo}>
            以{demoRoles.find((item) => item.id === selected)?.name}进入
          </Button>
        </div>
      </section>
    </main>
  )
}
