export type ShareRejection = 'cancelled' | 'error';

// NotAllowedError / AbortError -> 'cancelled' (ordinary: user dismissed the picker)
// anything else -> 'error'
export function classifyShareRejection(reason: unknown): ShareRejection {
  const name = reason instanceof Error ? reason.name : undefined;
  return name === 'NotAllowedError' || name === 'AbortError' ? 'cancelled' : 'error';
}

// true when navigator.mediaDevices?.getDisplayMedia is callable
export function screenShareAvailable(nav: unknown): boolean {
  const mediaDevices = (nav as { mediaDevices?: { getDisplayMedia?: unknown } } | null | undefined)?.mediaDevices;
  return typeof mediaDevices?.getDisplayMedia === 'function';
}

// calls .stop() on every track; tolerates null and a stream with no tracks
export function stopStreamTracks(stream: { getTracks(): { stop(): void }[] } | null): void {
  stream?.getTracks().forEach((track) => track.stop());
}

export const DISPLAY_MEDIA_CONSTRAINTS = { video: true, audio: false } as const;
