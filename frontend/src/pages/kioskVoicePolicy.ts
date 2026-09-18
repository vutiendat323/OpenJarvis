export type KioskVoiceCommand = 'start' | 'end' | 'unavailable' | 'noop';
export type KioskVoiceOwner = 'policy';

export interface KioskVoiceDecision {
  command: KioskVoiceCommand;
  grantConsumed: boolean;
}

export function kioskVoiceCommand({
  micEnabled,
  voiceEnabled,
  owner,
  grantConsumed,
}: {
  micEnabled: boolean;
  voiceEnabled: boolean;
  owner: KioskVoiceOwner | null;
  grantConsumed: boolean;
}): KioskVoiceDecision {
  if (!micEnabled) {
    return {
      command: owner !== null ? 'end' : 'noop',
      grantConsumed: false,
    };
  }
  if (owner !== null || grantConsumed) {
    return { command: 'noop', grantConsumed: true };
  }
  if (!voiceEnabled) {
    return { command: 'unavailable', grantConsumed: false };
  }
  return { command: 'start', grantConsumed: true };
}
