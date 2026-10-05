import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { RecordValue } from './api';

type LoadState = { path: string | null; data: RecordValue | null; error: string; loading: boolean };

/** Keep one read in flight; realtime updates queue at most one follow-up read. */
export function useApiData(path: string | null, revision = 0) {
  const [state, setState] = useState<LoadState>({ path: null, data: null, error: '', loading: false });
  const refresh = useRef<(() => void) | null>(null);
  const seenRevision = useRef(revision);

  // Only a changed query or unmount cancels the request. Each effect owns its
  // controller and active flag, including StrictMode's initial cleanup cycle.
  useEffect(() => {
    seenRevision.current = revision;
    if (!path) {
      refresh.current = null;
      setState({ path: null, data: null, error: '', loading: false });
      return;
    }
    let active = true;
    let controller: AbortController | null = null;
    let queued = false;
    async function read() {
      if (!active) return;
      if (controller) { queued = true; return; }
      controller = new AbortController();
      setState((previous) => ({ path, data: previous.path === path ? previous.data : null, error: previous.path === path ? previous.error : '', loading: true }));
      try {
        const data = await api(path!, { signal: controller.signal });
        if (active) setState({ path, data, error: '', loading: true });
      } catch (reason) {
        if (active && (reason as Error).name !== 'AbortError') {
          setState((previous) => ({ ...previous, error: (reason as Error).message }));
        }
      } finally {
        controller = null;
        if (active) {
          if (queued) { queued = false; void read(); }
          else setState((previous) => ({ ...previous, loading: false }));
        }
      }
    }
    refresh.current = read;
    void read();
    return () => {
      active = false;
      queued = false;
      controller?.abort();
      if (refresh.current === read) refresh.current = null;
    };
  }, [path]);

  useEffect(() => {
    if (seenRevision.current === revision) return;
    seenRevision.current = revision;
    refresh.current?.();
  }, [revision]);

  // A render for a new query must never expose the previous query's data or
  // error, even before React runs the path effect.
  return state.path === path
    ? { data: state.data, error: state.error, loading: state.loading }
    : { data: null, error: '', loading: Boolean(path) };
}
