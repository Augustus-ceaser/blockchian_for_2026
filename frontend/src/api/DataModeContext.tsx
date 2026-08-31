import { createContext, useContext, type ReactNode } from 'react'

export type DataMode = 'mock' | 'api'

const configuredMode: DataMode = import.meta.env.VITE_DATA_MODE === 'api' ? 'api' : 'mock'
const DataModeContext = createContext<DataMode>(configuredMode)

export function DataModeProvider({ children }: { children: ReactNode }) {
  return <DataModeContext.Provider value={configuredMode}>{children}</DataModeContext.Provider>
}

export function useDataMode() {
  const mode = useContext(DataModeContext)
  return { mode, isApi: mode === 'api' }
}
