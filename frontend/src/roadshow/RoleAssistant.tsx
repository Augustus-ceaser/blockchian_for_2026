import {
  ArrowRightOutlined,
  FileSearchOutlined,
  RobotOutlined,
  SendOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Input,
  List,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { secureUuid } from '../lib/secureUuid'
import { platformCommand } from './api'
import {
  assistantLauncherBottomForPointer,
  clampAssistantLauncherBottom,
  isAssistantLauncherDrag,
} from './assistantLauncherPosition'
import type {
  DemandAssistantCandidate,
  DemandAssistantHandoff,
  DemandAssistantPairCandidate,
  DemandAssistantPairGateStatus,
  DemandAssistantResponse,
} from './demandAssistant'
import type {
  AssistantCompatibilityEvidence,
  AssistantExecutionLineage,
  AssistantResult,
  AssistantToolTrace,
  RoleAssistantQueryResponse,
} from './roleAssistantContract'
import { useRoadshow } from './RoadshowContext'
import type { DemoIdentity } from './types'

const { Text, Title } = Typography
const avatarPath = '/assets/medtrust-ai-assistant.png'
const assistantRouteActionLabels: Record<string, string> = {
  '/external-catalog/datasets': '查看公共候选数据目录',
  '/external-catalog/models': '查看公共候选模型目录',
  '/data-catalog': '查看数据产品目录',
  '/data-products': '查看数据产品',
  '/model-catalog': '查看模型产品目录',
  '/model-products': '查看模型产品',
  '/applications': '查看计算需求',
  '/applications/new': '新建受控计算申请',
  '/contracts': '查看数字合约',
  '/execution': '查看执行进度',
  '/results': '查看结果',
  '/lifecycle': '查看生命周期事项',
}

function canonicalAssistantRoute(path: string) {
  if (path === '/portal/operator/external-catalog') return '/external-catalog/datasets'
  if (path === '/portal/operator/external-model-catalog') return '/external-catalog/models'
  return path
}

function assistantRouteActionLabel(path: string, identity: DemoIdentity) {
  if (identity === 'space_operator' && path === '/data-products') return '查看数据产品审核'
  if (identity === 'space_operator' && path === '/model-products') return '查看模型产品审核'
  if (identity === 'data_requester' && path === '/data-catalog') return '打开数据商城'
  if (identity === 'data_requester' && path === '/model-catalog') return '打开模型商城'
  return assistantRouteActionLabels[path] ?? '查看相关内容'
}

function serviceIntentRoute(message: string): string | null {
  if (/(脱敏数据|数据授权|数据交付|购买数据)/.test(message)) return '/data-catalog'
  if (/(模型许可|模型授权|模型制品|购买模型)/.test(message)) return '/model-catalog'
  if (/(受控计算|调用计算|组合申请)/.test(message)) return '/applications/new'
  return null
}

type AssistantProfile = {
  name: string
  greeting: string
  capability: string
  placeholder: string
  prompts: string[]
}

type LauncherDragState = {
  pointerId: number
  startY: number
  startBottom: number
  moved: boolean
}

const assistantProfiles: Record<DemoIdentity, AssistantProfile> = {
  space_operator: {
    name: '空间运营助手',
    greeting: '你好，我可以帮你定位当前待审核事项、目录工作和协作链路。',
    capability: '查询平台审核事项、产品状态、协作链路与结果记录',
    placeholder: '例如：查找当前待审核的计算需求',
    prompts: ['查找待审核的计算需求', '查看待上架的数据产品', '查找待处理的生命周期事项'],
  },
  data_provider: {
    name: '医院协作助手',
    greeting: '你好，我可以在医院当前可见范围内帮你找合约、数据、模型、审批、执行与结果。',
    capability: '查询本组织可见的产品、合约、审批、执行准备和结果记录',
    placeholder: '例如：帮我找与骨折项目有关的合约',
    prompts: ['查找我的数字合约', '查看待处理的数据使用审批', '查找医院可用的模型'],
  },
  model_provider: {
    name: '模型协作助手',
    greeting: '你好，我可以帮你定位模型、适配数据、使用审批、合约、执行与结果。',
    capability: '查询本组织可见的产品、审批、合约、执行准备和结果记录',
    placeholder: '例如：查找适合骨折风险模型的数据',
    prompts: ['查找我的模型产品', '查看待处理的模型使用审批', '查找适配的数据产品'],
  },
  data_requester: {
    name: '研究需求助手',
    greeting: '你好，我可以把研究问题拆成结构化需求并推荐可申请的数据与模型，也可以查询申请进度。',
    capability: '理解研究需求，查询真实目录、合约、执行进度和结果',
    placeholder: '例如：我想使用结直肠病理图像验证一个分类模型',
    prompts: [
      '我想使用结直肠组织病理图像验证一个分类模型，输出准确率和混淆矩阵，用于科研分析',
      '我想构建一个骨折患者住院风险预测模型',
      '现在有多少公共数据集',
    ],
  },
}

function CandidateList({ title, kind, items, onOpen }: {
  title: string
  kind: 'data' | 'model'
  items: DemandAssistantCandidate[]
  onOpen: (path: string) => void
}) {
  return <div className="role-assistant-candidates">
    <Text strong>{title}</Text>
    {items.length ? items.slice(0, 3).map((item) => {
      const path = kind === 'data' ? '/data-catalog' : `/model-products/${item.version_id}`
      return <button
        type="button"
        key={item.version_id}
        className="role-assistant-candidate"
        onClick={() => onOpen(path)}
      >
        <span><strong>{item.name}</strong><small>{item.provider} · {item.disease_domain || '领域待确认'}</small></span>
        <Tag color={item.match_level === 'strong' ? 'green' : 'blue'}>{item.score} 分</Tag>
      </button>
    }) : <Text type="secondary">当前可申请目录没有匹配候选</Text>}
  </div>
}

const pairGatePresentation: Record<DemandAssistantPairGateStatus, { label: string; color: string }> = {
  pass: { label: '硬门通过', color: 'green' },
  hold: { label: '条件待补', color: 'gold' },
  fail: { label: '硬门失败', color: 'red' },
}

const pairWorkflowLabels: Record<DemandAssistantPairCandidate['workflow_role'], string> = {
  incompatible: '不兼容',
  training_required: '需要先训练',
  validation_ready: '可进入验证复核',
  metadata_review_required: '需补元数据',
}

const pairStageLabels: Record<DemandAssistantPairCandidate['stage'], string> = {
  catalog_only: '仅目录',
  static_candidate: '静态候选',
  application_candidate: '可申请候选',
  execution_ready: '执行条件已登记',
  verified_pair: '平台已验证组合',
}

function canSelectPair(item: DemandAssistantPairCandidate) {
  return item.actions.can_select
    && item.hard_gate.status !== 'fail'
    && item.workflow_role !== 'incompatible'
}

function PairCandidateList({
  items,
  summary,
  selectedPairKey,
  onSelect,
}: {
  items: DemandAssistantPairCandidate[]
  summary: DemandAssistantResponse['pair_summary']
  selectedPairKey: string | null
  onSelect: (pairKey: string) => void
}) {
  return <section className="role-assistant-pairs" aria-label="数据与模型组合推荐">
    <Flex className="role-assistant-pairs__heading" align="center" justify="space-between" gap={8} wrap>
      <Text strong>数据 × 模型组合</Text>
      <Space size={[4, 4]} wrap>
        <Tag color="green">通过 {summary.pass}</Tag>
        <Tag color="gold">待补 {summary.hold}</Tag>
        <Tag color="red">失败 {summary.fail}</Tag>
      </Space>
    </Flex>
    {items.length ? items.slice(0, 3).map((item) => {
      const gate = pairGatePresentation[item.hard_gate.status]
      const hasMoreDetail = item.reasons.length > 1 || item.limitations.length > 1
      const selectable = canSelectPair(item)
      const selected = item.pair_key === selectedPairKey
      return <article
        className={`role-assistant-pair-card is-${item.hard_gate.status}${selected ? ' is-selected' : ''}`}
        key={item.pair_key}
      >
        <div className="role-assistant-pair-card__heading">
          <span>
            <small>数据 × 模型</small>
            <strong>{item.data_name} × {item.model_name}</strong>
          </span>
          <span className="role-assistant-pair-score">
            <strong>{item.score.total}</strong>
            <small>/{item.score.max_total} 分</small>
          </span>
        </div>
        <Space size={[4, 4]} wrap>
          <Tag color={gate.color}>{gate.label}</Tag>
          <Tag>{pairWorkflowLabels[item.workflow_role]}</Tag>
          <Tag>{pairStageLabels[item.stage]}</Tag>
          <Tag color={selected ? 'geekblue' : selectable ? 'blue' : 'default'}>{selected ? '已选组合' : selectable ? '可选择' : '不可选择'}</Tag>
          <Tag color={item.actions.can_execute ? 'green' : 'default'}>{item.actions.can_execute ? '可执行' : '不可执行'}</Tag>
        </Space>
        {item.reasons[0] && <p className="role-assistant-pair-note"><b>匹配理由</b>{item.reasons[0]}</p>}
        {item.limitations[0] && <p className="role-assistant-pair-note is-limitation"><b>限制</b>{item.limitations[0]}</p>}
        {hasMoreDetail && <details className="role-assistant-pair-detail">
          <summary>查看全部理由与限制</summary>
          {!!item.reasons.length && <div><Text strong>匹配理由</Text><ul>{item.reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}</ul></div>}
          {!!item.limitations.length && <div><Text strong>限制</Text><ul>{item.limitations.map((limitation, index) => <li key={`${index}-${limitation}`}>{limitation}</li>)}</ul></div>}
        </details>}
        <Button
          block
          size="small"
          type={selected ? 'primary' : 'default'}
          disabled={!selectable}
          onClick={() => onSelect(item.pair_key)}
        >{selected ? '已选择这组' : selectable ? '选择这组' : '这组不可选择'}</Button>
      </article>
    }) : <Text type="secondary">当前没有可比较的数据与模型组合。</Text>}
    {items.length > 3 && <Text type="secondary" className="role-assistant-pairs__more">仅展示排序前 3 组，共 {summary.total} 组。</Text>}
  </section>
}

function SingleCandidateDetails({
  dataItems,
  modelItems,
  onOpen,
}: {
  dataItems: DemandAssistantCandidate[]
  modelItems: DemandAssistantCandidate[]
  onOpen: (path: string) => void
}) {
  return <details className="role-assistant-single-candidates">
    <summary>查看单项候选（数据 {dataItems.length} · 模型 {modelItems.length}）</summary>
    <div>
      <CandidateList title="数据候选" kind="data" items={dataItems} onOpen={onOpen} />
      <CandidateList title="模型候选" kind="model" items={modelItems} onOpen={onOpen} />
    </div>
  </details>
}

function ToolTrace({ items }: { items: AssistantToolTrace[] }) {
  if (!items.length) return null
  return <div className="role-assistant-trace" aria-label="平台查询依据">
    <Text type="secondary">已查询平台资源</Text>
    <Space size={[4, 4]} wrap>
      {items.slice(0, 3).map((item) => <Tag
        key={item.tool}
        color={item.status === 'error' ? 'red' : item.status === 'empty' ? 'default' : 'cyan'}
      >
        {item.label} {item.status === 'error' ? '查询失败' : item.result_count}
      </Tag>)}
    </Space>
  </div>
}

const roleLabels: Record<string, string> = {
  space_operator: '平台方',
  data_provider: '医院方',
  model_provider: '模型方',
  data_requester: '需求方',
}

function CompatibilityEvidenceList({
  items,
  onOpen,
}: {
  items: AssistantCompatibilityEvidence[]
  onOpen: (path: string) => void
}) {
  if (!items.length) return null
  return <div className="role-assistant-evidence">
    <Text strong>数据—模型适配证据</Text>
    {items.slice(0, 3).map((item, index) => {
      const content = <>
        <span className="role-assistant-evidence__title">
          <strong>{item.data_name && item.model_name ? `${item.data_name} × ${item.model_name}` : '当前版本配对'}</strong>
          <Tag color={item.status.includes('incompatible') || item.status.includes('failed') ? 'red' : item.status === 'verified' ? 'green' : item.status === 'not_assessed' ? 'default' : 'cyan'}>
            {item.status_label}
          </Tag>
        </span>
        {item.data_version && item.model_version && <small>{item.data_version} × {item.model_version} · {item.evidence_level}</small>}
        {item.evidence_note && <Text type="secondary">{item.evidence_note}</Text>}
        {!!item.transformation_requirements.length && <small>所需转换：{item.transformation_requirements.join('；')}</small>}
        {!!item.blocking_reasons.length && <small className="is-blocked">阻断：{item.blocking_reasons.join('；')}</small>}
      </>
      return item.path ? <button
        type="button"
        className="role-assistant-evidence__item"
        key={item.relation_id || `not-assessed-${index}`}
        onClick={() => onOpen(item.path!)}
      >{content}</button> : <div
        className="role-assistant-evidence__item"
        key={item.relation_id || `not-assessed-${index}`}
      >{content}</div>
    })}
  </div>
}

function ExecutionLineageList({
  items,
  onOpen,
}: {
  items: AssistantExecutionLineage[]
  onOpen: (path: string) => void
}) {
  if (!items.length) return null
  return <div className="role-assistant-lineages">
    <Text strong>申请到结果的执行血缘</Text>
    {items.map((item, index) => <details
      className="role-assistant-lineage"
      key={item.application_id}
      defaultOpen={index === 0}
    >
      <summary>
        <span><strong>{item.application_number}</strong><small>{item.scenario_name}</small></span>
        <Tag>{item.completed_nodes}/{item.total_nodes}</Tag>
      </summary>
      <div className="role-assistant-lineage__nodes">
        {item.nodes.map((node) => <div className={`role-assistant-lineage__node is-${node.state}`} key={node.key}>
          <i aria-hidden="true" />
          <span><strong>{node.label}</strong><small>{node.number || node.status}</small></span>
          <Tag>{node.status}</Tag>
        </div>)}
      </div>
      {(item.next_action || item.next_role) && <Text type="secondary">
        下一步：{item.next_action || '等待后续处理'}{item.next_role ? ` · ${roleLabels[item.next_role] || item.next_role}` : ''}
      </Text>}
      <Button size="small" onClick={() => onOpen(item.path)}>打开申请</Button>
    </details>)}
  </div>
}

function StudyDefinition({ result }: { result: DemandAssistantResponse }) {
  const definition = result.normalized_intent.study_definition
  if (!definition) return null
  const terminology = Object.values(definition.terminology)
  return <details className="role-assistant-study-definition">
    <summary>查看完整研究定义</summary>
    <Descriptions size="small" column={1}>
      <Descriptions.Item label="研究方式">{definition.operation_mode.label}</Descriptions.Item>
      <Descriptions.Item label="就诊场景">{definition.target_population.care_setting.label}</Descriptions.Item>
      <Descriptions.Item label="数据模态">
        {definition.modalities.map((item) => item.label).join('、') || '待补充'}
      </Descriptions.Item>
      <Descriptions.Item label="纳入条件">
        {definition.target_population.inclusion_criteria.join('；') || '待补充'}
      </Descriptions.Item>
      <Descriptions.Item label="排除条件">
        {definition.target_population.exclusion_criteria.join('；') || '待补充'}
      </Descriptions.Item>
      <Descriptions.Item label="术语映射">
        {terminology.length && terminology.every((item) => item.mapping_status === 'not_mapped')
          ? '尚未映射标准医学编码'
          : '已读取登记状态'}
      </Descriptions.Item>
    </Descriptions>
  </details>
}

export function RoleAssistant() {
  const { identity } = useRoadshow()
  const navigate = useNavigate()
  const location = useLocation()
  const profile = assistantProfiles[identity]
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [lastQuery, setLastQuery] = useState('')
  const [answer, setAnswer] = useState('')
  const [results, setResults] = useState<AssistantResult[]>([])
  const [demandResult, setDemandResult] = useState<DemandAssistantResponse | null>(null)
  const [selectedPairKey, setSelectedPairKey] = useState<string | null>(null)
  const [compatibilityEvidence, setCompatibilityEvidence] = useState<AssistantCompatibilityEvidence[]>([])
  const [lineage, setLineage] = useState<AssistantExecutionLineage[]>([])
  const [toolTrace, setToolTrace] = useState<AssistantToolTrace[]>([])
  const [routeHint, setRouteHint] = useState<string | null>(null)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [contextApplied, setContextApplied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [launcherBottom, setLauncherBottom] = useState<number | null>(null)
  const [launcherDragging, setLauncherDragging] = useState(false)
  const requestRevision = useRef(0)
  const launcherRef = useRef<HTMLButtonElement | null>(null)
  const launcherDrag = useRef<LauncherDragState | null>(null)
  const suppressLauncherClick = useRef(false)

  useEffect(() => {
    const showAssistant = () => setOpen(true)
    window.addEventListener('medtrust:open-assistant', showAssistant)
    return () => window.removeEventListener('medtrust:open-assistant', showAssistant)
  }, [])
  useEffect(() => {
    const keepLauncherInViewport = () => {
      const launcherHeight = launcherRef.current?.offsetHeight ?? 72
      setLauncherBottom((current) => current === null
        ? null
        : clampAssistantLauncherBottom(current, launcherHeight, window.innerHeight))
    }
    window.addEventListener('resize', keepLauncherInViewport)
    return () => window.removeEventListener('resize', keepLauncherInViewport)
  }, [])
  useEffect(() => {
    requestRevision.current += 1
    setInput('')
    setLastQuery('')
    setAnswer('')
    setResults([])
    setDemandResult(null)
    setSelectedPairKey(null)
    setCompatibilityEvidence([])
    setLineage([])
    setToolTrace([])
    setRouteHint(null)
    setConversationId(null)
    setContextApplied(false)
    setError('')
    setBusy(false)
  }, [identity])

  const openPath = (path: string) => {
    navigate(path)
    setOpen(false)
  }

  const submit = async (suggested?: string) => {
    const query = (suggested ?? input).trim()
    if (!query) return
    setInput('')
    setLastQuery(query)
    setAnswer('')
    setResults([])
    setDemandResult(null)
    setSelectedPairKey(null)
    setCompatibilityEvidence([])
    setLineage([])
    setToolTrace([])
    setRouteHint(null)
    setError('')
    const revision = ++requestRevision.current
    setBusy(true)
    try {
      const response = await platformCommand<RoleAssistantQueryResponse>(
        '/role-assistant/query',
        identity,
        `role-assistant-query-${secureUuid()}`,
        { message: query, conversation_id: conversationId },
      )
      if (requestRevision.current !== revision) return
      setAnswer(response.answer)
      setResults(response.results)
      setDemandResult(response.demand_result)
      setCompatibilityEvidence(response.compatibility_evidence ?? [])
      setLineage(response.lineage ?? [])
      setToolTrace(response.tool_trace)
      setRouteHint(serviceIntentRoute(query) ?? response.route_hint)
      setConversationId(response.conversation_id)
      setContextApplied(response.context_applied)
    } catch (reason) {
      if (requestRevision.current !== revision) return
      setError(reason instanceof Error ? reason.message : '助手暂时无法完成检索')
    } finally {
      if (requestRevision.current === revision) setBusy(false)
    }
  }

  const handoffDemand = () => {
    if (!demandResult?.can_apply_draft || !demandResult.can_apply_pair_selection || !selectedPairKey) return
    const selectedPair = demandResult.pair_candidates.find((item) => item.pair_key === selectedPairKey)
    if (!selectedPair || !canSelectPair(selectedPair) || !selectedPair.actions.can_apply) return
    const demandAssistant: DemandAssistantHandoff = {
      text: lastQuery,
      result: demandResult,
      selectedPairKey,
      selectedPair,
    }
    setOpen(false)
    navigate('/applications/new', { state: { demandAssistant } })
  }

  const startLauncherDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!event.isPrimary || event.button !== 0) return
    const bounds = event.currentTarget.getBoundingClientRect()
    launcherDrag.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startBottom: window.innerHeight - bounds.bottom,
      moved: false,
    }
    suppressLauncherClick.current = false
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const moveLauncher = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = launcherDrag.current
    if (!drag || drag.pointerId !== event.pointerId) return
    if (!drag.moved && !isAssistantLauncherDrag(drag.startY, event.clientY)) return
    if (!drag.moved) {
      drag.moved = true
      setLauncherDragging(true)
    }
    event.preventDefault()
    setLauncherBottom(assistantLauncherBottomForPointer(
      drag.startBottom,
      drag.startY,
      event.clientY,
      event.currentTarget.offsetHeight,
      window.innerHeight,
    ))
  }

  const finishLauncherDrag = (event: ReactPointerEvent<HTMLButtonElement>, cancelled = false) => {
    const drag = launcherDrag.current
    if (!drag || drag.pointerId !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    launcherDrag.current = null
    setLauncherDragging(false)
    if (cancelled || !drag.moved) return
    suppressLauncherClick.current = true
    window.setTimeout(() => { suppressLauncherClick.current = false }, 0)
  }

  const openAssistant = () => {
    if (suppressLauncherClick.current) {
      suppressLauncherClick.current = false
      return
    }
    setOpen(true)
  }

  return <>
    <button
      ref={launcherRef}
      type="button"
      className={`role-assistant-launcher${launcherDragging ? ' is-dragging' : ''}`}
      style={launcherBottom === null ? undefined : { bottom: launcherBottom }}
      aria-label={`打开${profile.name}，可上下拖动调整位置`}
      aria-expanded={open}
      aria-haspopup="dialog"
      title="上下拖动调整位置，点击打开助手"
      onClick={openAssistant}
      onPointerDown={startLauncherDrag}
      onPointerMove={moveLauncher}
      onPointerUp={finishLauncherDrag}
      onPointerCancel={(event) => finishLauncherDrag(event, true)}
    >
      <img src={avatarPath} alt="" className="role-assistant-avatar" draggable={false} />
      <span className="role-assistant-online" />
    </button>
    <Drawer
      open={open}
      onClose={() => setOpen(false)}
      placement="right"
      size={440}
      rootClassName="role-assistant-drawer"
      title={<div className="role-assistant-title">
        <img src={avatarPath} alt="" />
        <span><strong>{profile.name}</strong><small>{profile.capability}</small></span>
      </div>}
    >
      <div className="role-assistant-body">
        <div className="role-assistant-message role-assistant-message--assistant">
          <RobotOutlined />
          <span>{profile.greeting}</span>
        </div>
        {!lastQuery && <div className="role-assistant-prompts">
          <Text type="secondary">你可以这样问</Text>
          {profile.prompts.map((prompt) => <button type="button" key={prompt} onClick={() => void submit(prompt)}>
            <span>{prompt}</span><ArrowRightOutlined />
          </button>)}
        </div>}
        {lastQuery && <div className="role-assistant-message role-assistant-message--user">{lastQuery}</div>}
        {busy && <Flex className="role-assistant-loading" align="center" gap={10}><Spin size="small" /><Text type="secondary">正在查询当前账号可见信息…</Text></Flex>}
        {error && <Alert type="error" showIcon title="没有完成这次查询" description={error} />}
        {answer && <div className="role-assistant-message role-assistant-message--assistant">
          <RobotOutlined /><span>{answer}</span>
        </div>}
        {!busy && contextApplied && <Tag color="cyan">已结合本次会话中的资源</Tag>}
        {!busy && <ToolTrace items={toolTrace} />}
        {!busy && <CompatibilityEvidenceList items={compatibilityEvidence} onOpen={openPath} />}
        {!busy && <ExecutionLineageList items={lineage} onOpen={openPath} />}
        {results.length > 0 && !compatibilityEvidence.length && !lineage.length && <List
          className="role-assistant-results"
          dataSource={results}
          renderItem={(item) => <List.Item>
            <button type="button" className="role-assistant-result" onClick={() => openPath(item.path)}>
              <span className="role-assistant-result__content">
                <small>{item.label}</small>
                <strong>{item.title}</strong>
                <Text type="secondary" ellipsis>{item.subtitle}</Text>
              </span>
              <span className="role-assistant-result__aside">
                {item.status && <Tag>{item.status}</Tag>}
                <ArrowRightOutlined />
              </span>
            </button>
          </List.Item>}
        />}
        {routeHint
          && canonicalAssistantRoute(location.pathname) !== canonicalAssistantRoute(routeHint)
          && !demandResult && !compatibilityEvidence.length && !lineage.length && !busy && <Button
          block
          icon={<ArrowRightOutlined />}
          onClick={() => openPath(routeHint)}
        >{assistantRouteActionLabel(routeHint, identity)}</Button>}
        {demandResult && <div className="role-assistant-demand">
          <Title level={5}>需求拆解</Title>
          <Descriptions size="small" column={1}>
            <Descriptions.Item label={demandResult.normalized_intent.task_family === 'image_classification' ? '研究对象' : '研究人群'}>
              {demandResult.normalized_intent.population_label || '待补充'}
            </Descriptions.Item>
            <Descriptions.Item label={demandResult.normalized_intent.task_family === 'image_classification' ? '目标任务' : '预测结局'}>
              {demandResult.normalized_intent.outcome_label || '待补充'}
            </Descriptions.Item>
            {demandResult.normalized_intent.task_family !== 'image_classification' && <>
              <Descriptions.Item label="预测时点">{demandResult.normalized_intent.index_time_label || '待补充'}</Descriptions.Item>
              <Descriptions.Item label="时间窗口">{demandResult.normalized_intent.prediction_horizon_label || '待补充'}</Descriptions.Item>
            </>}
          </Descriptions>
          <StudyDefinition result={demandResult} />
          {demandResult.blocking_reasons.map((item) => <Alert key={item.code} type="error" showIcon title={item.message} />)}
          {demandResult.clarifications.length > 0 && <div className="role-assistant-clarifications">
            <Text strong>还需要补充</Text>
            <ul>{demandResult.clarifications.map((item) => <li key={item.code}>{item.question}</li>)}</ul>
          </div>}
          {demandResult.pair_candidates.length === 0
            && demandResult.catalog_gaps.map((item) => <Alert key={item.code} type="warning" showIcon title="当前目录缺少匹配资产" description={item.message} />)}
          <PairCandidateList
            items={demandResult.pair_candidates}
            summary={demandResult.pair_summary}
            selectedPairKey={selectedPairKey}
            onSelect={setSelectedPairKey}
          />
          <SingleCandidateDetails
            dataItems={demandResult.data_recommendations}
            modelItems={demandResult.model_recommendations}
            onOpen={openPath}
          />
          <Button
            type="primary"
            block
            icon={<FileSearchOutlined />}
            disabled={!demandResult.can_apply_draft
              || !demandResult.can_apply_pair_selection
              || !selectedPairKey
              || !demandResult.pair_candidates.some((item) => item.pair_key === selectedPairKey && canSelectPair(item) && item.actions.can_apply)}
            onClick={handoffDemand}
          >
            确认组合并带入申请
          </Button>
        </div>}
        {!busy && lastQuery && !answer && !error && !demandResult && !results.length && !compatibilityEvidence.length && !lineage.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待查询" />}
      </div>
      <div className="role-assistant-composer">
        <Input.TextArea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onPressEnter={(event) => {
            if (!event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              void submit()
            }
          }}
          autoSize={{ minRows: 2, maxRows: 4 }}
          maxLength={1000}
          placeholder={profile.placeholder}
          aria-label={`${profile.name}输入框`}
        />
        <Flex justify="space-between" align="center" gap={10}>
          <Text type="secondary" className="role-assistant-privacy">请勿输入姓名、病历号、联系方式或其他患者身份信息。</Text>
          <Button type="primary" shape="circle" icon={<SendOutlined />} loading={busy} disabled={!input.trim()} aria-label="发送" onClick={() => void submit()} />
        </Flex>
      </div>
    </Drawer>
  </>
}
