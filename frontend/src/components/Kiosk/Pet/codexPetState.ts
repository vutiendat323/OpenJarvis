import type { PetState } from './types';
import type { LocalVoiceStatus } from '@/hooks/voiceStatus';

/**
 * Resolves the visual animation state for the Codex Pet based on:
 * 1. User dragging interaction (highest priority)
 * 2. Activity detail overrides (e.g. review, completed, failed, directional wander)
 * 3. Voice / kiosk status mapping
 */
export function resolvePetState(
  voiceStatus?: LocalVoiceStatus | string | null,
  activityDetail?: string | null,
  isDragging?: boolean,
  dragDeltaX?: number,
): PetState {
  if (isDragging) {
    if (typeof dragDeltaX === 'number' && dragDeltaX < 0) {
      return 'running-left';
    }
    return 'running-right';
  }

  const detail = (activityDetail ?? '').trim().toLowerCase();
  if (detail === 'review' || detail === 'completed' || detail === 'cheer') {
    return 'review';
  }
  if (detail === 'failed' || detail === 'error') {
    return 'failed';
  }
  if (detail === 'running-left' || detail === 'left') {
    return 'running-left';
  }
  if (detail === 'running-right' || detail === 'right') {
    return 'running-right';
  }

  const status = (voiceStatus ?? '').trim().toLowerCase();

  switch (status) {
    case 'approaching':
      return 'waving';
    case 'listening':
    case 'connecting':
      return 'alert';
    case 'speaking':
      return 'waving';
    case 'inference':
    case 'processing':
    case 'busy':
    case 'tool':
    case 'tool_call':
      return 'running-right';
    case 'error':
      return 'failed';
    case 'review':
    case 'completed':
    case 'cheer':
      return 'review';
    case 'idle':
    case 'ended':
    case 'disconnected':
    case '':
      return 'idle';
    default:
      return 'idle';
  }
}
