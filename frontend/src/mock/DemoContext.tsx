import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import { demoRoles, roleById } from './data'
import type { DemoRole, DemoStage, RoleId } from './types'

const ROLE_KEY = 'medtrust.demo.role'
const STAGE_KEY = 'medtrust.demo.stage'

interface DemoContextValue {
  role: DemoRole
  stage: DemoStage
  setRole: (role: RoleId) => void
  setStage: (stage: DemoStage) => void
  resetDemo: () => void
}

const DemoContext = createContext<DemoContextValue | undefined>(undefined)

const storedStage = (): DemoStage => {
  const value = window.localStorage.getItem(STAGE_KEY)
  const allowed: DemoStage[] = [
    'catalog',
    'application-submitted',
    'application-approved',
    'contract-active',
    'compute-running',
    'output-review',
    'result-released',
  ]
  return allowed.includes(value as DemoStage) ? (value as DemoStage) : 'catalog'
}

export function DemoProvider({ children }: { children: ReactNode }) {
  const [roleId, setRoleId] = useState<RoleId>(() => roleById(window.localStorage.getItem(ROLE_KEY)).id)
  const [stage, setStageState] = useState<DemoStage>(storedStage)

  const setRole = (nextRole: RoleId) => {
    window.localStorage.setItem(ROLE_KEY, nextRole)
    setRoleId(nextRole)
  }

  const setStage = (nextStage: DemoStage) => {
    window.localStorage.setItem(STAGE_KEY, nextStage)
    setStageState(nextStage)
  }

  const resetDemo = () => {
    const defaultRole = demoRoles[0].id
    window.localStorage.setItem(ROLE_KEY, defaultRole)
    window.localStorage.setItem(STAGE_KEY, 'catalog')
    setRoleId(defaultRole)
    setStageState('catalog')
  }

  const value = useMemo(
    () => ({ role: roleById(roleId), stage, setRole, setStage, resetDemo }),
    [roleId, stage],
  )

  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>
}

export function useDemo() {
  const context = useContext(DemoContext)
  if (!context) {
    throw new Error('useDemo must be used inside DemoProvider')
  }
  return context
}
