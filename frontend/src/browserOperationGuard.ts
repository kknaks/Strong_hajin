import { createContext, useContext, useEffect } from 'react';

export const BrowserOperationScope = createContext<'workspace' | 'chat'>('workspace');
const pending = new Map<symbol, 'workspace' | 'chat'>();

/** Shared by shell navigation and the browser's unload event; every mounted operation releases its own guard. */
export function hasPendingBrowserOperation(scope?: 'workspace' | 'chat'): boolean {
  return scope === undefined ? pending.size > 0 : [...pending.values()].includes(scope);
}

export function useBrowserOperationGuard(active: boolean): void {
  const scope = useContext(BrowserOperationScope);
  useEffect(() => {
    if (!active) return;
    const token = Symbol('browser operation');
    pending.set(token, scope);
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => { pending.delete(token); window.removeEventListener('beforeunload', guard); };
  }, [active, scope]);
}
