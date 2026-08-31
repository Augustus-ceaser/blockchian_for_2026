import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { authLogin, authLogout, authMe, roadshowGet } from './api'
import { startAbortableLoad } from './requestLifecycle'
import { isDemoIdentity } from './types'
import type {
  DemoIdentity,
  RoadshowContext as ContextPayload,
  RoadshowEventView,
  RoadshowMode,
} from './types'

const SESSION_KEY = 'medtrust.roadshow.session'

export const roleProfiles: Record<DemoIdentity, {
  label: string
  shortLabel: string
  organization: string
  description: string
  color: string
}> = {
  space_operator: {
    label: 'MedTrust 空间运营方', shortLabel: '空间运营', organization: 'MedTrust Space运营中心（演示）',
    description: '产品上架、需求预审、合同编排、执行检查与合规审核', color: '#1769aa',
  },
  data_provider: {
    label: '医院数据提供方', shortLabel: '医院数据方', organization: '华南肿瘤医学中心（演示）',
    description: '发布数据产品、审批数据使用、确认数据就绪与结果出域', color: '#16856c',
  },
  model_provider: {
    label: 'AI 模型提供方', shortLabel: '模型提供方', organization: '智衡医疗AI（演示）',
    description: '发布白名单模型、审批模型使用、确认模型就绪与技术质量', color: '#6b55b5',
  },
  data_requester: {
    label: '数据需求企业', shortLabel: '需求企业', organization: '远景医药研发（演示）',
    description: '组合数据与模型提出计算需求，并仅获取获批结果包', color: '#b36a14',
  },
}

type RoadshowContextValue = {
  identity: DemoIdentity
  setIdentity: (identity: DemoIdentity) => void
  authenticated: boolean
  authLoading: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  context: ContextPayload | null
  contextError: string
  refreshContext: () => void
  roadshow: {
    enabled: boolean
    applicationId: string
    mode: RoadshowMode
    currentNode: string
    guideHidden: boolean
    eventView: RoadshowEventView
  }
  updateRoadshow: (patch: Partial<RoadshowContextValue['roadshow']>) => void
  exitRoadshow: () => void
}

const Context = createContext<RoadshowContextValue | null>(null)

const defaultRoadshow = {
  enabled: false,
  applicationId: '',
  mode: '8min' as RoadshowMode,
  currentNode: 'data_product',
  guideHidden: false,
  eventView: 'critical' as RoadshowEventView,
}

function initialRoadshow() {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(SESSION_KEY) || '{}')
    return { ...defaultRoadshow, ...stored }
  } catch {
    return defaultRoadshow
  }
}

export function RoadshowProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentityState] = useState<DemoIdentity>('space_operator')
  const [authenticated, setAuthenticated] = useState(false)
  const [authLoading, setAuthLoading] = useState(true)
  const [context, setContext] = useState<ContextPayload | null>(null)
  const [contextError, setContextError] = useState('')
  const [nonce, setNonce] = useState(0)
  const [roadshow, setRoadshow] = useState(initialRoadshow)

  const setIdentity = (next: DemoIdentity) => {
    if (import.meta.env.VITE_ENABLE_DEMO_ROLE_SWITCH === 'true') setIdentityState(next)
  }

  const updateRoadshow = (patch: Partial<RoadshowContextValue['roadshow']>) => {
    setRoadshow((current) => {
      const next = { ...current, ...patch }
      window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(next))
      return next
    })
  }

  const exitRoadshow = () => {
    window.sessionStorage.removeItem(SESSION_KEY)
    setRoadshow(defaultRoadshow)
  }

  const refreshAuth = () => startAbortableLoad(
    (signal) => authMe<{ role: unknown }>(signal),
    {
      onSuccess: (value) => {
        if (!isDemoIdentity(value.role)) {
          void authLogout().catch(() => undefined)
          setAuthenticated(false)
          setContext(null)
          return
        }
        setIdentityState(value.role)
        setAuthenticated(true)
      },
      onError: () => {
        setAuthenticated(false)
        setContext(null)
      },
      onSettled: () => setAuthLoading(false),
    },
  )

  useEffect(() => refreshAuth(), [])

  useEffect(() => {
    if (!authenticated) return
    setContext(null)
    setContextError('')
    return startAbortableLoad(
      (signal) => roadshowGet<ContextPayload>('/context', identity, signal),
      {
        onSuccess: (value) => {
          setContext(value)
          setContextError('')
        },
        onError: (error) => {
          setContextError(error instanceof Error ? error.message : '无法读取演示身份')
        },
      },
    )
  }, [authenticated, identity, nonce])

  const login = async (username: string, password: string) => {
    await authLogin(username, password)
    setAuthLoading(true)
    try {
      const profile = await authMe<{ role: unknown }>()
      if (!isDemoIdentity(profile.role)) {
        throw new Error('该账号不属于当前平台门户')
      }
      setIdentityState(profile.role)
      setAuthenticated(true)
    } catch (reason) {
      await authLogout().catch(() => undefined)
      setAuthenticated(false)
      setContext(null)
      throw reason
    } finally {
      setAuthLoading(false)
    }
  }

  const logout = async () => {
    await authLogout()
    setAuthenticated(false)
    setContext(null)
    exitRoadshow()
  }

  const value = useMemo(() => ({
    identity,
    setIdentity,
    authenticated,
    authLoading,
    login,
    logout,
    context,
    contextError,
    refreshContext: () => setNonce((value) => value + 1),
    roadshow,
    updateRoadshow,
    exitRoadshow,
  }), [identity, authenticated, authLoading, context, contextError, roadshow])
  return <Context.Provider value={value}>{children}</Context.Provider>
}

export function useRoadshow() {
  const value = useContext(Context)
  if (!value) throw new Error('useRoadshow must be used inside RoadshowProvider')
  return value
}
