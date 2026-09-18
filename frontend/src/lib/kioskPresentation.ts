import { apiFetch } from './api';
import type { KioskState } from '@/hooks/useKioskState';
import {
  reduceCustomerDisplay,
  waitingState,
  type CustomerDisplayState,
} from '@/pages/customerDisplayState';

const PRESENTATION_RETRY_DELAY_MS = 500;
const PRESENTATION_MAX_ATTEMPTS = 20;

export function shouldEnsurePresentationSession(state: KioskState): boolean {
  return state === 'approaching' || state === 'prompting' || state === 'active';
}

export async function ensurePresentationSession(
  displayOrigin: string,
  language?: string,
): Promise<string> {
  for (let attempt = 1; attempt <= PRESENTATION_MAX_ATTEMPTS; attempt += 1) {
    const response = await apiFetch('/api/kiosk/presentation/ensure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_origin: displayOrigin, language }),
    });
    if (response.ok) {
      const payload = await response.json() as { presentation_session_id?: unknown };
      if (typeof payload.presentation_session_id === 'string' && payload.presentation_session_id) {
        return payload.presentation_session_id;
      }
      throw new Error('Unable to start customer display');
    }
    if (response.status !== 503 || attempt === PRESENTATION_MAX_ATTEMPTS) {
      throw new Error('Unable to start customer display');
    }
    await new Promise((resolve) => setTimeout(resolve, PRESENTATION_RETRY_DELAY_MS));
  }
  throw new Error('Unable to start customer display');
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

export interface TouchCartOrder {
  variant_id: string;
  quantity: number;
  note: string;
}

export type PickupMinutes = 0 | 5 | 10 | 15 | 30 | 45 | 60;
export const PICKUP_MINUTES: readonly PickupMinutes[] = [0, 5, 10, 15, 30, 45, 60];

export type TouchCartEdit =
  | ({ action: 'add' } & TouchCartOrder)
  | { action: 'update'; line_id: string; quantity: number }
  | { action: 'remove'; line_id: string }
  | { action: 'clear' }
  | { action: 'set_order_type'; order_type: 'at-table' | 'take-out' }
  | { action: 'set_table'; table: string }
  | { action: 'set_pickup_time'; pickup_minutes: PickupMinutes };

export interface TouchTable {
  slug: string;
  name: string;
  status: string;
}

type TouchCart = Extract<CustomerDisplayState, { view: 'cart' }>;

/** Call a touch endpoint; a refusal throws its server reason as the message. */
async function touchRequest(sessionId: string, path: string, init: RequestInit = {}) {
  const response = await apiFetch(
    `/api/kiosk/presentation/${encodeURIComponent(sessionId)}/${path}`,
    init,
  );
  const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
  if (!response.ok) {
    throw new Error(typeof payload.detail === 'string' ? payload.detail : 'touch_unavailable');
  }
  return payload;
}

/** Apply a tap to the live Voice session's draft cart and return that draft. */
export async function editTouchCart(sessionId: string, edit: TouchCartEdit): Promise<TouchCart> {
  const payload = await touchRequest(sessionId, 'cart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(edit),
  });
  // The reducer is the one gate every cart shown to the customer passes.
  const cart = reduceCustomerDisplay(waitingState, {
    type: 'display_update',
    timestamp: 0,
    data: {
      ...(payload.cart as Record<string, unknown>),
      view: 'cart',
      presentation_session_id: sessionId,
    },
  }, sessionId);
  if (cart.view !== 'cart') throw new Error('cart_unavailable');
  return cart;
}

/** The merchant's tables as of now, for the receipt's table picker. */
export async function fetchTouchTables(sessionId: string): Promise<TouchTable[]> {
  const payload = await touchRequest(sessionId, 'tables');
  const rows = Array.isArray(payload.tables) ? payload.tables : [];
  return rows.flatMap((row) => (
    typeof row === 'object' && row !== null
    && typeof row.slug === 'string' && typeof row.name === 'string' && typeof row.status === 'string'
      ? [{ slug: row.slug, name: row.name, status: row.status }]
      : []
  ));
}

/** Place the saved draft; the display then receives the bill and payment QR. */
export async function checkoutTouchCart(sessionId: string): Promise<void> {
  await touchRequest(sessionId, 'checkout', { method: 'POST' });
}

/** Tell the Voice agent which menu rows a typed search shows; [] clears it. */
export async function shareScreenSearch(sessionId: string, itemIds: string[]): Promise<void> {
  await touchRequest(sessionId, 'search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item_ids: itemIds }),
  });
}

/** The merchant's status for the order on screen ("paid" once it is). */
export async function fetchTouchPaymentStatus(sessionId: string): Promise<string> {
  const payload = await touchRequest(sessionId, 'payment');
  return typeof payload.status === 'string' ? payload.status : 'unknown';
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
  let resetRequired = false;
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
