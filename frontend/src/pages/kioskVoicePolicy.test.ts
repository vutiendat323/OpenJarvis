import { describe, expect, it } from 'vitest';

import { kioskVoiceCommand } from './kioskVoicePolicy';

describe('kioskVoiceCommand', () => {
  it('starts Voice once when Vision grants microphone access', () => {
    const firstDecision = kioskVoiceCommand({
      micEnabled: true,
      voiceEnabled: true,
      owner: null,
      grantConsumed: false,
    });

    expect(firstDecision).toEqual({ command: 'start', grantConsumed: true });

    expect(kioskVoiceCommand({
      micEnabled: true,
      voiceEnabled: true,
      owner: null,
      grantConsumed: firstDecision.grantConsumed,
    })).toEqual({ command: 'noop', grantConsumed: true });
  });

  it('ends policy Voice and rearms startup when Vision revokes access', () => {
    expect(kioskVoiceCommand({
      micEnabled: false,
      voiceEnabled: true,
      owner: 'policy',
      grantConsumed: true,
    })).toEqual({ command: 'end', grantConsumed: false });
  });

  it('starts again after a new Vision microphone grant', () => {
    const cleanupDecision = kioskVoiceCommand({
      micEnabled: false,
      voiceEnabled: true,
      owner: null,
      grantConsumed: true,
    });

    expect(kioskVoiceCommand({
      micEnabled: true,
      voiceEnabled: true,
      owner: null,
      grantConsumed: cleanupDecision.grantConsumed,
    })).toEqual({ command: 'start', grantConsumed: true });
  });

  it('waits for Voice availability without consuming the Vision grant', () => {
    expect(kioskVoiceCommand({
      micEnabled: true,
      voiceEnabled: false,
      owner: null,
      grantConsumed: false,
    })).toEqual({ command: 'unavailable', grantConsumed: false });
  });
});
