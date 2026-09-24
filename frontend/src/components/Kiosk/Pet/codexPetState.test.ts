import { describe, it, expect } from 'vitest';
import { resolvePetState } from './codexPetState';

describe('resolvePetState', () => {
  describe('dragging priority', () => {
    it('returns running-left when dragging to the left (dragDeltaX < 0)', () => {
      expect(resolvePetState('speaking', null, true, -10)).toBe('running-left');
      expect(resolvePetState('idle', null, true, -1)).toBe('running-left');
    });

    it('returns running-right when dragging to the right (dragDeltaX > 0)', () => {
      expect(resolvePetState('speaking', null, true, 10)).toBe('running-right');
      expect(resolvePetState('idle', null, true, 1)).toBe('running-right');
    });

    it('returns running-right when dragging with zero or undefined delta', () => {
      expect(resolvePetState('idle', null, true, 0)).toBe('running-right');
      expect(resolvePetState('idle', null, true, undefined)).toBe('running-right');
    });
  });

  describe('voice status mappings', () => {
    it('maps idle / ended / disconnected / null / undefined to idle', () => {
      expect(resolvePetState('idle')).toBe('idle');
      expect(resolvePetState('ended')).toBe('idle');
      expect(resolvePetState('disconnected')).toBe('idle');
      expect(resolvePetState(null)).toBe('idle');
      expect(resolvePetState(undefined)).toBe('idle');
      expect(resolvePetState('')).toBe('idle');
    });

    it('maps approaching to waving', () => {
      expect(resolvePetState('approaching')).toBe('waving');
    });

    it('maps listening to alert', () => {
      expect(resolvePetState('listening')).toBe('alert');
    });

    it('maps connecting to alert', () => {
      expect(resolvePetState('connecting')).toBe('alert');
    });

    it('maps speaking to waving', () => {
      expect(resolvePetState('speaking')).toBe('waving');
    });

    it('maps inference / processing / busy / tool / tool_call to running-right', () => {
      expect(resolvePetState('inference')).toBe('running-right');
      expect(resolvePetState('processing')).toBe('running-right');
      expect(resolvePetState('busy')).toBe('running-right');
      expect(resolvePetState('tool')).toBe('running-right');
      expect(resolvePetState('tool_call')).toBe('running-right');
    });

    it('maps error to failed', () => {
      expect(resolvePetState('error')).toBe('failed');
    });

    it('maps review / completed / cheer to review', () => {
      expect(resolvePetState('review')).toBe('review');
      expect(resolvePetState('completed')).toBe('review');
      expect(resolvePetState('cheer')).toBe('review');
    });
  });

  describe('activityDetail overrides', () => {
    it('maps review / completed in activityDetail to review state', () => {
      expect(resolvePetState('idle', 'review')).toBe('review');
      expect(resolvePetState('idle', 'completed')).toBe('review');
      expect(resolvePetState('inference', 'review')).toBe('review');
      expect(resolvePetState('processing', 'completed')).toBe('review');
    });

    it('maps cheer in activityDetail to review', () => {
      expect(resolvePetState('idle', 'cheer')).toBe('review');
    });

    it('maps failed / error in activityDetail to failed', () => {
      expect(resolvePetState('idle', 'failed')).toBe('failed');
      expect(resolvePetState('inference', 'error')).toBe('failed');
    });

    it('maps running-left / left in activityDetail to running-left', () => {
      expect(resolvePetState('idle', 'running-left')).toBe('running-left');
      expect(resolvePetState('idle', 'left')).toBe('running-left');
    });

    it('maps running-right / right in activityDetail to running-right', () => {
      expect(resolvePetState('idle', 'running-right')).toBe('running-right');
      expect(resolvePetState('idle', 'right')).toBe('running-right');
    });

    it('ignores general activity detail descriptions and relies on voiceStatus', () => {
      expect(resolvePetState('inference', 'Searching knowledge base...')).toBe('running-right');
      expect(resolvePetState('tool', 'Browser search')).toBe('running-right');
      expect(resolvePetState('speaking', 'Hello there')).toBe('waving');
    });
  });

  describe('fallback and resilience', () => {
    it('falls back to idle for unknown status values', () => {
      expect(resolvePetState('unknown_status_xyz')).toBe('idle');
    });

    it('handles uppercase and trimmed status values', () => {
      expect(resolvePetState('LISTENING')).toBe('alert');
      expect(resolvePetState(' speaking ')).toBe('waving');
    });
  });
});
