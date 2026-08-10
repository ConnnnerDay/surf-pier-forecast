import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'

type Units = 'imperial' | 'metric'

interface UnitsContextValue {
  units: Units
  setUnits: (units: Units) => void
}

const UnitsContext = createContext<UnitsContextValue | undefined>(undefined)
const STORAGE_KEY = 'ff.units'

export function UnitsProvider({ children }: { children: ReactNode }) {
  const [units, setUnitsState] = useState<Units>(
    () => (localStorage.getItem(STORAGE_KEY) as Units | null) ?? 'imperial',
  )

  const setUnits = (next: Units) => {
    localStorage.setItem(STORAGE_KEY, next)
    setUnitsState(next)
  }

  const value = useMemo(() => ({ units, setUnits }), [units])
  return <UnitsContext.Provider value={value}>{children}</UnitsContext.Provider>
}

export function useUnits(): UnitsContextValue {
  const ctx = useContext(UnitsContext)
  if (!ctx) throw new Error('useUnits must be used within UnitsProvider')
  return ctx
}
