import { useCallback, useEffect, useRef, useState } from 'react';

import {
  classifyShareRejection,
  screenShareAvailable,
  stopStreamTracks,
  DISPLAY_MEDIA_CONSTRAINTS,
} from './screenShare';

export type ScreenShareStatus = 'idle' | 'starting' | 'live' | 'error';

export function useScreenShare(): {
  status: ScreenShareStatus;
  stream: MediaStream | null;
  error: string | null;
  unavailable: boolean;
  start: () => Promise<void>;
  stop: () => void;
} {
  const [status, setStatus] = useState<ScreenShareStatus>('idle');
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const stop = useCallback(() => {
    stopStreamTracks(streamRef.current);
    streamRef.current = null;
    setStream(null);
    setStatus('idle');
  }, []);

  const start = useCallback(async () => {
    setStatus('starting');
    setError(null);
    try {
      const media = await navigator.mediaDevices.getDisplayMedia(DISPLAY_MEDIA_CONSTRAINTS);
      media.getVideoTracks().forEach((track) => { track.onended = stop; });
      streamRef.current = media;
      setStream(media);
      setStatus('live');
    } catch (err) {
      if (classifyShareRejection(err) === 'cancelled') {
        setStatus('idle');
      } else {
        setStatus('error');
        setError(err instanceof Error ? err.message : 'Screen share failed');
      }
    }
  }, [stop]);

  // A capture left running past unmount keeps the browser's sharing
  // indicator lit with nothing rendering it.
  useEffect(() => () => stopStreamTracks(streamRef.current), []);

  return { status, stream, error, unavailable: !screenShareAvailable(navigator), start, stop };
}
