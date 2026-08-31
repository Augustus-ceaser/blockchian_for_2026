import {
  ArrowRightOutlined,
  AuditOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  LinkOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Flex,
  Progress,
  Radio,
  Select,
  Skeleton,
  Space,
  Statistic,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { platformGet } from './api'
import { roleProfiles, useRoadshow } from './RoadshowContext'
import { startAbortableLoad } from './requestLifecycle'
import type {
  DemoIdentity,
  RoadshowChainDetail,
  RoadshowChainEvents,
  RoadshowChainSummary,
  RoadshowHealth,
} from './types'

const { Paragraph, Text, Title } = Typography

const statusLabel: Record<string, string> = {
  published: '已发布',
  approved: '已批准',
  active: '已生效',
  eligible: '资格通过',
  succeeded: '运行成功',
  quarantined: '隔离中',
  available: '可下载',
  exhausted: '已用尽',
  verified: '链路有效',
  pending: '待处理',
  checking: '检查中',
  not_created: '未创建',
  not_started: '未开始',
  not_granted: '未授权',
}

const guideCopy: Record<string, { prove: string; talk: string; expected: string }> = {
  data_product: {
    prove: '医院发布的是受政策约束的数据产品，不是原始数据文件。',
    talk: '先看供给侧：公开元数据可见，原始病理图像始终不可见。',
    expected: '数据产品保持 published，展示固定版本和摘要。',
  },
  model_product: {
    prove: '模型方发布固定能力、版本和 Schema，不交付模型权重。',
    talk: '模型进入平台的是固定入口和能力声明，不是任意代码。',
    expected: '模型产品保持 published，模型摘要可核对。',
  },
  application: {
    prove: '企业申请受约束计算，不获得数据下载权限。',
    talk: '申请把数据、模型、用途、次数和输出白名单绑定在一起。',
    expected: '申请经过三方审核后进入 approved。',
  },
  contract: {
    prove: '多方条件按最严格规则收敛，并由四方确认同一版本。',
    talk: '合同 active 不等于允许执行，后面还有就绪与资格检查。',
    expected: '四方确认完成，合同状态为 active。',
  },
  readiness: {
    prove: '数据、模型和平台环境必须分别就绪。',
    talk: '三项事实全部满足后才生成不可变 Eligibility Snapshot。',
    expected: '3/3 ready，资格快照存在。',
  },
  run: {
    prove: '任务经过 Dispatcher、Coordinator、固定 Executor 与 Callback。',
    talk: '查看任务执行状态与回调进度。',
    expected: '20 张公开 PathMNIST 图像完成 CPU 运行。',
  },
  artifact: {
    prove: '运行成功后，原始 Artifact 仍然保持 quarantined。',
    talk: '计算成功与允许出域是两件独立的事。',
    expected: 'Artifact 为 quarantined，不存在原始结果下载。',
  },
  result_review: {
    prove: '医院、模型方和平台分别承担不同结果审核责任。',
    talk: '只有三项审核全部通过，平台才可生成独立安全结果包。',
    expected: '3/3 结果审核 approved。',
  },
  package: {
    prove: '结果包只包含合同白名单内的三个聚合文件。',
    talk: '安全结果包与隔离 Artifact 是两个不同对象。',
    expected: 'Package available，文件数精确为 3。',
  },
  download: {
    prove: '下载授权绑定机构、用户和结果包，且只能消费一次。',
    talk: '第一次成功，第二次重用被拒绝并进入审计。',
    expected: 'Grant exhausted，下载计数 1/1。',
  },
  audit: {
    prove: '所有关键动作形成可验证的连续审计链。',
    talk: '失败和拒绝不会被隐藏；哈希用于篡改检测线索。',
    expected: 'Audit chain valid，invalid sequence 为 0/空。',
  },
}

function tagColor(status: string) {
  if (['published', 'approved', 'active', 'eligible', 'succeeded', 'available', 'verified'].includes(status)) return 'green'
  if (status === 'quarantined' || status === 'exhausted') return 'orange'
  if (status.includes('failed') || status === 'not_ready') return 'red'
  return 'default'
}

export function RoadshowExperiencePage() {
  const navigate = useNavigate()
  const { identity, setIdentity, roadshow, updateRoadshow } = useRoadshow()
  const [chains, setChains] = useState<RoadshowChainSummary[]>([])
  const [detail, setDetail] = useState<RoadshowChainDetail | null>(null)
  const [events, setEvents] = useState<RoadshowChainEvents | null>(null)
  const [health, setHealth] = useState<RoadshowHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [nonce, setNonce] = useState(0)

  const load = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    setLoading(true)
    setError('')
    return startAbortableLoad(
      async (signal) => {
        const list = await platformGet<{ items: RoadshowChainSummary[] }>(
          '/roadshow-experience/chains',
          identity,
          signal,
        )
        const selected = list.items.some((item) => item.application_id === roadshow.applicationId)
          ? roadshow.applicationId
          : list.items.find((item) => item.status === 'active')?.application_id || list.items[0]?.application_id || ''
        const [chain, eventPayload, healthPayload] = selected
          ? await Promise.all([
              platformGet<RoadshowChainDetail>(`/roadshow-experience/chains/${selected}`, identity, signal),
              platformGet<RoadshowChainEvents>(
                `/roadshow-experience/chains/${selected}/events?view=${roadshow.eventView}`,
                identity,
                signal,
              ),
              platformGet<RoadshowHealth>('/roadshow-experience/health', identity, signal),
            ])
          : [null, null, await platformGet<RoadshowHealth>('/roadshow-experience/health', identity, signal)]
        return { list: list.items, selected, chain, eventPayload, healthPayload }
      },
      {
        onSuccess: ({ list, selected, chain, eventPayload, healthPayload }) => {
          setChains(list)
          setDetail(chain)
          setEvents(eventPayload)
          setHealth(healthPayload)
          setLoading(false)
          if (selected && (selected !== roadshow.applicationId || !roadshow.enabled)) {
            updateRoadshow({ enabled: true, applicationId: selected })
          }
        },
        onError: (reason) => {
          setError(reason instanceof Error ? reason.message : '无法读取业务链')
          setLoading(false)
        },
      },
    )
  }, [identity, nonce, roadshow.applicationId, roadshow.eventView])

  useEffect(() => {
    if (!detail || detail.status === 'completed') return
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [detail, load])

  const currentNode = useMemo(
    () => detail?.nodes.find((item) => item.key === roadshow.currentNode) || detail?.nodes.find((item) => !item.complete) || detail?.nodes.at(-1),
    [detail, roadshow.currentNode],
  )
  const currentNodeRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    currentNodeRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [currentNode?.key])

  const guide = currentNode ? guideCopy[currentNode.key] || guideCopy.audit : null

  const switchNext = () => {
    if (!detail?.next_role) return
    setIdentity(detail.next_role)
    const target = detail.nodes.find(
      (item) => !item.complete && item.responsible_role === detail.next_role && item.href,
    )
    const fallback = detail.nodes.find((item) => item.key === 'application' && item.href)
    const destination = target || fallback
    if (!destination) return
    updateRoadshow({ currentNode: destination.key })
    navigate(destination.href!)
  }

  if (loading && !detail) {
    return <div className="phase58-page"><Skeleton active paragraph={{ rows: 12 }} /></div>
  }

  return <div className="phase58-page">
    <div className="phase58-heading">
      <div>
        <Title level={2}>全链路受控计算</Title>
        <Paragraph>同一条真实业务链贯穿产品、申请、合约、执行、隔离审核、安全结果包和一次性下载。</Paragraph>
      </div>
      <Space wrap>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={roadshow.mode}
          onChange={(event) => updateRoadshow({ mode: event.target.value })}
          options={[
            { label: '精简流程', value: '8min' },
            { label: '完整流程', value: '15min' },
          ]}
        />
        <Tooltip title="刷新真实后台事实"><Button icon={<ReloadOutlined />} onClick={load} /></Tooltip>
      </Space>
    </div>

    {error && <Alert type="error" showIcon message="业务链读取失败" description={error} />}
    {!detail ? <Empty description="当前身份没有可见的业务链" /> : <>
      <div className="phase58-commandbar">
        <div>
          <Text type="secondary">业务链路</Text>
          <Select
            value={detail.application_id}
            onChange={(applicationId) => updateRoadshow({ applicationId })}
            options={chains.map((item) => ({
              value: item.application_id,
              label: `${item.application_number} · ${item.status === 'completed' ? '完成态备用案例' : '实时主链'}`,
            }))}
          />
        </div>
        <div><Text type="secondary">当前角色</Text><strong>{roleProfiles[identity].shortLabel}</strong></div>
        <div><Text type="secondary">下一责任</Text><strong>{detail.next_role ? roleProfiles[detail.next_role].shortLabel : '流程完成'}</strong></div>
        <div className="phase58-progress">
          <Text type="secondary">主链进度</Text>
          <Progress percent={Math.round(detail.completed_nodes / detail.total_nodes * 100)} size="small" />
        </div>
        <Button
          type="primary"
          icon={<ArrowRightOutlined />}
          disabled={!detail.next_role}
          onClick={switchNext}
        >
          {detail.next_role ? '切换到下一责任方' : '全链路已完成'}
        </Button>
      </div>

      <div
        className="phase58-chain phase58-chain--overview"
        aria-label="全局业务链"
        data-current-node={currentNode?.key}
      >
        {detail.nodes.map((node, index) => <button
          type="button"
          key={node.key}
          ref={currentNode?.key === node.key ? currentNodeRef : undefined}
          className={`phase58-node ${node.complete ? 'is-complete' : ''} ${currentNode?.key === node.key ? 'is-current' : ''}`}
          data-node-key={node.key}
          aria-current={currentNode?.key === node.key ? 'step' : undefined}
          onClick={() => updateRoadshow({ currentNode: node.key })}
        >
          <span className="phase58-node__index">{node.complete ? <CheckCircleFilled /> : index + 1}</span>
          <strong>{node.label}</strong>
          <small>{node.number || '等待生成'}</small>
          <Tag color={tagColor(node.status)}>{statusLabel[node.status] || node.status}</Tag>
        </button>)}
      </div>

      <div className="phase58-layout">
        <main className="phase58-main">
          <div className="phase58-stat-grid">
            <Card><Statistic title="合约确认" value={`${detail.facts.contract.signatures}/${detail.facts.contract.required_signatures}`} prefix={<SafetyCertificateOutlined />} /></Card>
            <Card><Statistic title="受控运行" value={detail.facts.execution.run_status || '未开始'} prefix={<ClockCircleOutlined />} /></Card>
            <Card><Statistic title="结果审核" value={`${detail.facts.result.approved_reviews}/${detail.facts.result.required_reviews}`} prefix={<AuditOutlined />} /></Card>
            <Card><Statistic title="下载授权" value={detail.facts.result.grant_status || '未创建'} prefix={<LinkOutlined />} /></Card>
          </div>

          <Card title="核心安全反差" className="phase58-evidence-card">
            <div className="phase58-contrast">
              <div><Text type="secondary">原始计算结果</Text><strong>Artifact</strong><Tag color="orange">{detail.facts.result.artifact_status || '未生成'}</Tag><small>始终不提供原始下载</small></div>
              <ArrowRightOutlined />
              <div><Text type="secondary">多方审核后</Text><strong>Safe Package</strong><Tag color="green">{detail.facts.result.package_status || '未生成'}</Tag><small>{detail.facts.result.package_files.length} 个白名单文件</small></div>
              <ArrowRightOutlined />
              <div><Text type="secondary">一次性授权</Text><strong>Download Grant</strong><Tag color="orange">{detail.facts.result.grant_status || '未创建'}</Tag><small>{detail.facts.result.download_count}/{detail.facts.result.max_downloads || 1} 次消费</small></div>
            </div>
          </Card>

          <Card title="真实后台证据">
            <Descriptions column={{ xs: 1, md: 2, xl: 3 }} size="small">
              <Descriptions.Item label="数据产品">{detail.facts.data_product.name} · {detail.facts.data_product.version}</Descriptions.Item>
              <Descriptions.Item label="数据摘要"><Text code>{detail.facts.data_product.digest}</Text></Descriptions.Item>
              <Descriptions.Item label="模型产品">{detail.facts.model_product.name} · {detail.facts.model_product.version}</Descriptions.Item>
              <Descriptions.Item label="模型摘要"><Text code>{detail.facts.model_product.digest}</Text></Descriptions.Item>
              <Descriptions.Item label="合约">{detail.facts.contract.number} · {detail.facts.contract.status}</Descriptions.Item>
              <Descriptions.Item label="合约摘要"><Text code>{detail.facts.contract.digest}</Text></Descriptions.Item>
              <Descriptions.Item label="就绪事实">{detail.facts.execution.readiness.length}/3</Descriptions.Item>
              <Descriptions.Item label="Eligibility">{detail.facts.execution.eligibility ? '已生成' : '未生成'}</Descriptions.Item>
              <Descriptions.Item label="处理样本">{detail.facts.execution.sample_count || 20} 张公开 PathMNIST 图像</Descriptions.Item>
            </Descriptions>
          </Card>

          {roadshow.mode === '15min' && <Card
            title="组件链与事件"
            extra={<Radio.Group
              size="small"
              value={roadshow.eventView}
              onChange={(event) => updateRoadshow({ eventView: event.target.value })}
              options={[{ label: '关键事件', value: 'critical' }, { label: '全部技术事件', value: 'all' }]}
            />}
          >
            <div className="phase58-component-chain">
              {['Platform', 'Dispatcher', 'Coordinator', 'Fixed Executor', 'Scanner', 'Callback', 'Audit'].map((item) => <span key={item}>{item}</span>)}
            </div>
            <Timeline items={(events?.items || []).slice(0, 18).map((event) => ({
              color: event.result === 'success' ? 'green' : 'red',
              children: <div className="phase58-event"><strong>{event.event_type}</strong><span>{event.actor} · {event.result}</span><small>#{event.sequence} · {new Date(event.occurred_at).toLocaleString('zh-CN')}</small></div>,
            }))} />
          </Card>}
        </main>

        <aside className="phase58-side">
          {!roadshow.guideHidden ? <Card
            title="当前讲解"
            extra={<Tooltip title="隐藏讲解"><Button type="text" icon={<EyeInvisibleOutlined />} onClick={() => updateRoadshow({ guideHidden: true })} /></Tooltip>}
          >
            <Tag color="blue">{currentNode?.label}</Tag>
            <Title level={5}>要证明什么</Title><Paragraph>{guide?.prove}</Paragraph>
            <Title level={5}>建议话术</Title><Paragraph>{guide?.talk}</Paragraph>
            <Title level={5}>预期变化</Title><Paragraph>{guide?.expected}</Paragraph>
            <Text type="secondary">下一步：{detail.next_action}</Text>
            {currentNode?.href && <Button block icon={<LinkOutlined />} onClick={() => navigate(currentNode.href!)}>查看真实详情页</Button>}
          </Card> : <Button icon={<EyeOutlined />} onClick={() => updateRoadshow({ guideHidden: false })}>显示讲解</Button>}

          <Card title="系统健康与预检" extra={health?.status === 'ok' ? <Tag color="green">状态正常</Tag> : <Tag color="red">需处理</Tag>}>
            <div className="phase58-health">
              {health?.services.map((service) => <div key={service.key}>
                <span className={`phase58-health__dot is-${service.status}`} />
                <span>{service.label}</span>
                <Tag color={service.status === 'ok' ? 'green' : service.status === 'not_ready' ? 'red' : 'default'}>
                  {service.status === 'ok' ? '正常' : service.status === 'not_ready' ? '异常' : '未知'}
                </Tag>
              </div>)}
            </div>
          </Card>
        </aside>
      </div>
    </>}
  </div>
}
