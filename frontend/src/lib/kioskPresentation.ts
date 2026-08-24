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

export async function resetPresentationSession(
  sessionId: string,
  generation?: string,
): Promise<void> {
  const response = await apiFetch(
    `/api/kiosk/presentation/${encodeURIComponent(sessionId)}/reset`,
    generation
      ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ generation }),
        }
      : { method: 'POST' },
  );
  if (!response.ok) throw new Error('Unable to reset customer display');
}

export interface KioskPresentationLifecycle {
  endVoiceThenReset: (endVoice: () => Promise<void>) => Promise<void>;
  markActive: (generation: string) => void;
  requestReset: () => void;
  setSessionId: (sessionId: string) => void;
}

export function createKioskPresentationLifecycle(
  resetSession: (sessionId: string, generation?: string) => Promise<void> = resetPresentationSession,
): KioskPresentationLifecycle {
  let sessionId: string | undefined;
  let lifecycleEpoch = 0;
  let voiceGeneration: string | undefined;
  let resetRequired = true;
  let resetPending: { generation?: string } | null = null;
  let teardown: { generation: number; promise: Promise<void> } | null = null;

  const runReset = (id: string, generation?: string) => {
    const reset = generation ? resetSession(id, generation) : resetSession(id);
    void reset.catch(() => {});
  };

  const commitReset = () => {
    if (!resetRequired) return;
    resetRequired = false;
    if (sessionId) {
      runReset(sessionId, voiceGeneration);
    } else {
      resetPending = { generation: voiceGeneration };
    }
  };

  return {
    endVoiceThenReset: (endVoice) => {
      if (teardown?.generation === lifecycleEpoch) return teardown.promise;
      const teardownGeneration = lifecycleEpoch;
      const promise = endVoice()
        .then(() => {
          if (lifecycleEpoch === teardownGeneration) commitReset();
        })
        .finally(() => {
          if (teardown?.promise === promise) teardown = null;
        });
      teardown = { generation: teardownGeneration, promise };
      return promise;
    },
    markActive: (generation) => {
      lifecycleEpoch += 1;
      voiceGeneration = generation;
      resetRequired = true;
      resetPending = null;
    },
    requestReset: () => {
      if (teardown?.generation === lifecycleEpoch) return;
      commitReset();
    },
    setSessionId: (id: string) => {
      sessionId = id;
      if (!resetPending) return;
      const pending = resetPending;
      resetPending = null;
      runReset(id, pending.generation);
    },
  };
}
