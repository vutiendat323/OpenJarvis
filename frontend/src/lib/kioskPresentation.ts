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
