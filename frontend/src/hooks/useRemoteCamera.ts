import { useEffect } from 'react';

import { authHeaders, getBase } from '@/lib/api';

/**
 * Send this device's camera to Vision in place of the kiosk's C920.
 *
 * Only a page opened with ``?camera=remote`` does this, and it starts at page
 * load -- not with the voice session -- so Vision's presence detection sees
 * this camera and the kiosk FSM starts a session for someone standing at it.
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
function iceGathered(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener('icegatheringstatechange', onChange);
      resolve();
    };
    const onChange = () => {
      if (pc.iceGatheringState === 'complete') done();
    };
    pc.addEventListener('icegatheringstatechange', onChange);
    setTimeout(done, ICE_GATHER_TIMEOUT_MS);
  });
}

export function useRemoteCamera(): void {
  useEffect(() => {
    if (!remoteCameraRequested(window.location.search)) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection === 'undefined') return;
    let closed = false;
    let stream: MediaStream | null = null;
    let pc: RTCPeerConnection | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;

    const reconnect = () => {
      if (closed || retry) return;
      pc?.close();
      pc = null;
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
        if (peer.connectionState === 'connected') attempts = 0;
        if (['failed', 'disconnected', 'closed'].includes(peer.connectionState)) reconnect();
      };
      try {
        await peer.setLocalDescription(await peer.createOffer());
        await iceGathered(peer);
        const local = peer.localDescription;
        if (!local || closed) return;
        const response = await fetch(remoteCameraOfferUrl(window.location, getBase()), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ sdp: local.sdp, type: local.type }),
        });
        if (!response.ok) throw new Error(`remote camera offer refused: ${response.status}`);
        await peer.setRemoteDescription(await response.json());
      } catch (error) {
        console.error('Remote camera connection failed:', error);
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
        void connect();
      })
      .catch((error) => console.error('Remote camera unavailable:', error));

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      pc?.close();
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, []);
}
