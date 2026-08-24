import { apiFetch } from './api';

export async function ensurePresentationSession(displayOrigin: string): Promise<string> {
  const response = await apiFetch('/api/kiosk/presentation/ensure', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_origin: displayOrigin }),
  });
  if (!response.ok) throw new Error('Unable to start customer display');

  const payload = await response.json() as { presentation_session_id?: unknown };
  if (typeof payload.presentation_session_id !== 'string' || !payload.presentation_session_id) {
    throw new Error('Unable to start customer display');
  }
  return payload.presentation_session_id;
}

export async function resetPresentationSession(sessionId: string): Promise<void> {
  const response = await apiFetch(
    `/api/kiosk/presentation/${encodeURIComponent(sessionId)}/reset`,
    { method: 'POST' },
  );
  if (!response.ok) throw new Error('Unable to reset customer display');
}

export interface KioskPresentationLifecycle {
  endVoiceThenReset: (endVoice: () => Promise<void>) => Promise<void>;
  markActive: () => void;
  requestReset: () => void;
  setSessionId: (sessionId: string) => void;
}

export function createKioskPresentationLifecycle(
  resetSession: (sessionId: string) => Promise<void> = resetPresentationSession,
): KioskPresentationLifecycle {
  let sessionId: string | undefined;
  let generation = 0;
  let resetRequired = true;
  let resetPending = false;
  let teardown: { generation: number; promise: Promise<void> } | null = null;

  const runReset = (id: string) => {
    void resetSession(id).catch(() => {});
  };

  const commitReset = () => {
    if (!resetRequired) return;
    resetRequired = false;
    if (sessionId) {
      runReset(sessionId);
    } else {
      resetPending = true;
    }
  };

  return {
    endVoiceThenReset: (endVoice) => {
      if (teardown?.generation === generation) return teardown.promise;
      const teardownGeneration = generation;
      const promise = endVoice()
        .then(() => {
          if (generation === teardownGeneration) commitReset();
        })
        .finally(() => {
          if (teardown?.promise === promise) teardown = null;
        });
      teardown = { generation: teardownGeneration, promise };
      return promise;
    },
    markActive: () => {
      generation += 1;
      resetRequired = true;
    },
    requestReset: () => {
      if (teardown?.generation === generation) return;
      commitReset();
    },
    setSessionId: (id: string) => {
      sessionId = id;
      if (!resetPending) return;
      resetPending = false;
      runReset(id);
    },
  };
}
