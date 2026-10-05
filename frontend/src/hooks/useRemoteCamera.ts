import { useEffect, useState } from 'react';

import { authHeaders, getBase } from '@/lib/api';

/**
 * Send this device's camera to Vision in place of the kiosk's C920.
 *
 * The dock toggle starts this independently of voice; ``?camera=remote``
 * remains an optional page-load opt-in so presence can start before voice.
 *
 * Transport is a WebRTC peer connection carrying one send-only video track:
 * the browser's own (hardware) encoder, congestion control and pacing, which
 * keep running while the tab is in the background. Closing the page ends the
 * connection; Vision falls back to the C920 as soon as frames stop.
 */

const OFFER_PATH = '/api/kiosk/remote-camera/offer';
const RECONNECT_MS = [1000, 2000, 5000];
const ICE_GATHER_TIMEOUT_MS = 2000;

export function remoteCameraRequested(search: string): boolean {
  return new URLSearchParams(search).get('camera') === 'remote';
}

export function remoteCameraOfferUrl(
  location: Pick<Location, 'origin' | 'port'>,
  base: string,
): string {
  if (base) return `${base}${OFFER_PATH}`;
  if (location.port === '5173') return `http://localhost:8000${OFFER_PATH}`;
  return `${location.origin}${OFFER_PATH}`;
}

/** Non-trickle signalling: the offer is posted once with every candidate. */
function iceGathered(pc: RTCPeerConnection, signal: AbortSignal): Promise<void> {
  if (pc.iceGatheringState === 'complete' || signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timeout);
      pc.removeEventListener('icegatheringstatechange', onChange);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const onChange = () => {
      if (pc.iceGatheringState === 'complete') done();
    };
    const timeout = setTimeout(done, ICE_GATHER_TIMEOUT_MS);
    pc.addEventListener('icegatheringstatechange', onChange);
    signal.addEventListener('abort', done, { once: true });
  });
}

export function useRemoteCamera() {
  const [enabled, setEnabled] = useState(() =>
    typeof window !== 'undefined' && remoteCameraRequested(window.location.search));
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setActive(false);
    if (!enabled) return;
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection === 'undefined') {
      setError('Camera sharing is unavailable in this browser.');
      setEnabled(false);
      return;
    }
    let closed = false;
    let stream: MediaStream | null = null;
    let pc: RTCPeerConnection | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    const abort = new AbortController();

    const closePeer = () => {
      const peer = pc;
      pc = null;
      if (peer) {
        peer.onconnectionstatechange = null;
        peer.close();
      }
    };

    const reconnect = () => {
      if (closed || retry) return;
      setActive(false);
      closePeer();
      retry = setTimeout(() => {
        retry = null;
        void connect();
      }, RECONNECT_MS[Math.min(attempts, RECONNECT_MS.length - 1)]);
      attempts += 1;
    };

    const connect = async () => {
      if (closed || !stream) return;
      const peer = new RTCPeerConnection();
      pc = peer;
      for (const track of stream.getVideoTracks()) {
        peer.addTransceiver(track, { direction: 'sendonly', streams: [stream] });
      }
      peer.onconnectionstatechange = () => {
        if (closed || peer !== pc) return;
        if (peer.connectionState === 'connected') {
          attempts = 0;
          setActive(true);
          setError(null);
        }
        if (['failed', 'disconnected', 'closed'].includes(peer.connectionState)) reconnect();
      };
      try {
        await peer.setLocalDescription(await peer.createOffer());
        if (closed || peer !== pc) return;
        await iceGathered(peer, abort.signal);
        const local = peer.localDescription;
        if (!local || closed || peer !== pc) return;
        const response = await fetch(remoteCameraOfferUrl(window.location, getBase()), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ sdp: local.sdp, type: local.type }),
          signal: abort.signal,
        });
        if (!response.ok) throw new Error(`remote camera offer refused: ${response.status}`);
        const answer = await response.json();
        if (closed || peer !== pc) return;
        await peer.setRemoteDescription(answer);
      } catch (error) {
        if (closed || peer !== pc) return;
        setError(error instanceof Error ? error.message : 'Camera connection failed.');
        reconnect();
      }
    };

    void navigator.mediaDevices
      .getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 25 } },
        audio: false,
      })
      .then((media) => {
        if (closed) {
          media.getTracks().forEach((track) => track.stop());
          return;
        }
        stream = media;
        media.getVideoTracks().forEach((track) => {
          track.onended = () => {
            if (!closed) setEnabled(false);
          };
        });
        void connect();
      })
      .catch((error) => {
        if (closed) return;
        setError(error instanceof Error ? error.message : 'Camera unavailable.');
        setEnabled(false);
      });

    return () => {
      closed = true;
      abort.abort();
      if (retry) clearTimeout(retry);
      closePeer();
      stream?.getTracks().forEach((track) => {
        track.onended = null;
        track.stop();
      });
    };
  }, [enabled]);

  return { enabled, active: enabled && active, error, toggle: () => setEnabled((value) => !value) };
}
