import { beforeEach, describe, expect, it, vi } from 'vitest';

// Exercise the real hook's registered RTVI callbacks without opening media.
const harness = vi.hoisted(() => ({
  values: [] as unknown[],
  cursor: 0,
  listeners: new Map<string, (...args: unknown[]) => void>(),
}));

vi.mock('react', () => ({
  useState(initial: unknown) {
    const slot = harness.cursor++;
    if (!(slot in harness.values)) harness.values[slot] = initial;
    return [harness.values[slot], (next: unknown) => {
      harness.values[slot] = typeof next === 'function'
        ? next(harness.values[slot]) : next;
    }];
  },
  useRef(initial: unknown) {
    const slot = harness.cursor++;
    if (!(slot in harness.values)) harness.values[slot] = { current: initial };
    return harness.values[slot];
  },
  useCallback(callback: unknown) { return callback; },
  useEffect() {},
}));

vi.mock('@pipecat-ai/client-js', () => ({
  RTVIEvent: {
    TrackStarted: 'track-started',
    UserStartedSpeaking: 'user-started-speaking',
    UserStoppedSpeaking: 'user-stopped-speaking',
    ServerMessage: 'server-message',
    BotStartedSpeaking: 'bot-started-speaking',
    BotStoppedSpeaking: 'bot-stopped-speaking',
    BotTtsText: 'bot-tts-text',
    UserTranscript: 'user-transcript',
    Disconnected: 'disconnected',
  },
  PipecatClient: class {
    transport = { pc: { getReceivers: () => [], getSenders: () => [] } };
    on(event: string, handler: (...args: unknown[]) => void) {
      harness.listeners.set(event, handler);
    }
    async connect() {}
    async disconnect() {}
  },
}));

vi.mock('@pipecat-ai/small-webrtc-transport', () => ({
  SmallWebRTCTransport: class {},
}));

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(), authHeaders: () => ({}) }));

import { usePipecatVoiceMode } from './usePipecatVoiceMode';

function renderVoice(onTurn: ReturnType<typeof vi.fn>) {
  harness.cursor = 0;
  return usePipecatVoiceMode({ onTurn });
}

function emit(event: string, payload?: unknown) {
  const handler = harness.listeners.get(event);
  expect(handler).toBeDefined();
  handler?.(payload);
}

beforeEach(() => {
  harness.values.length = 0;
  harness.cursor = 0;
  harness.listeners.clear();
});

describe('live user transcript events', () => {
  it('shows interim revisions immediately and saves only the final message', async () => {
    const onTurn = vi.fn();
    await renderVoice(onTurn).start('thread-1', 'test-model');

    emit('user-transcript', { text: 'Cho tôi cà', final: false });
    expect(renderVoice(onTurn).transcript).toBe('Cho tôi cà');
    expect(onTurn).not.toHaveBeenCalled();

    emit('user-transcript', { text: 'Cho tôi cà phê sữa', final: false });
    expect(renderVoice(onTurn).transcript).toBe('Cho tôi cà phê sữa');
    expect(onTurn).not.toHaveBeenCalled();

    emit('user-transcript', { text: 'Cho tôi một cà phê sữa.', final: true });
    expect(renderVoice(onTurn).transcript).toBe('Cho tôi một cà phê sữa.');
    expect(onTurn).toHaveBeenCalledTimes(1);
    expect(onTurn.mock.calls[0][0]).toMatchObject({
      role: 'user', content: 'Cho tôi một cà phê sữa.',
    });
  });

  it('clears an interim transcript when the voice session ends', async () => {
    const onTurn = vi.fn();
    await renderVoice(onTurn).start('thread-1', 'test-model');
    emit('user-transcript', { text: 'Cho tôi', final: false });
    expect(renderVoice(onTurn).transcript).toBe('Cho tôi');

    await renderVoice(onTurn).end();
    expect(renderVoice(onTurn).transcript).toBe('');
    expect(onTurn).not.toHaveBeenCalled();
  });
});
