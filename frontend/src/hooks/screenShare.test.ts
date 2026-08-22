import { describe, expect, it, vi } from 'vitest';

import {
  classifyShareRejection,
  screenShareAvailable,
  stopStreamTracks,
  DISPLAY_MEDIA_CONSTRAINTS,
} from './screenShare';

describe('classifyShareRejection', () => {
  it('reads a dismissed picker as cancelled, not an error', () => {
    expect(classifyShareRejection(Object.assign(new Error('denied'), { name: 'NotAllowedError' }))).toBe('cancelled');
  });

  it('reads an aborted picker as cancelled', () => {
    expect(classifyShareRejection(Object.assign(new Error('aborted'), { name: 'AbortError' }))).toBe('cancelled');
  });

  it('reads every other Error as a real failure', () => {
    expect(classifyShareRejection(new Error('NotFoundError'))).toBe('error');
  });

  it('reads a non-Error rejection as a real failure', () => {
    expect(classifyShareRejection('nope')).toBe('error');
    expect(classifyShareRejection(undefined)).toBe('error');
  });
});

describe('screenShareAvailable', () => {
  it('is true when getDisplayMedia is callable', () => {
    expect(screenShareAvailable({ mediaDevices: { getDisplayMedia: () => {} } })).toBe(true);
  });

  it('is false when navigator itself is missing', () => {
    expect(screenShareAvailable(undefined)).toBe(false);
  });

  it('is false when mediaDevices is missing', () => {
    expect(screenShareAvailable({})).toBe(false);
  });

  it('is false when getDisplayMedia is missing', () => {
    expect(screenShareAvailable({ mediaDevices: {} })).toBe(false);
  });
});

describe('stopStreamTracks', () => {
  it('stops every track exactly once', () => {
    const stop1 = vi.fn();
    const stop2 = vi.fn();
    stopStreamTracks({ getTracks: () => [{ stop: stop1 }, { stop: stop2 }] });
    expect(stop1).toHaveBeenCalledTimes(1);
    expect(stop2).toHaveBeenCalledTimes(1);
  });

  it('tolerates null', () => {
    expect(() => stopStreamTracks(null)).not.toThrow();
  });

  it('tolerates a stream with no tracks', () => {
    expect(() => stopStreamTracks({ getTracks: () => [] })).not.toThrow();
  });
});

describe('DISPLAY_MEDIA_CONSTRAINTS', () => {
  it('requests video and explicitly refuses audio', () => {
    expect(DISPLAY_MEDIA_CONSTRAINTS).toEqual({ video: true, audio: false });
  });
});
