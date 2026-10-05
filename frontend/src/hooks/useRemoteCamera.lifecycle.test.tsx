// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRemoteCamera } from './useRemoteCamera';
import { RemoteCameraButton } from '@/components/Kiosk/RemoteCameraButton';

vi.mock('@/lib/api', () => ({ authHeaders: () => ({}), getBase: () => '' }));

class Peer {
  static peers: Peer[] = [];
  connectionState = 'new';
  iceGatheringState = 'complete';
  localDescription = { sdp: 'camera-sdp', type: 'offer' };
  onconnectionstatechange: (() => void) | null = null;
  addTransceiver = vi.fn();
  createOffer = vi.fn(async () => this.localDescription);
  setLocalDescription = vi.fn(async () => {});
  setRemoteDescription = vi.fn(async () => {});
  close = vi.fn(() => { this.connectionState = 'closed'; this.onconnectionstatechange?.(); });
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  constructor() { Peer.peers.push(this); }
  state(value: string) { this.connectionState = value; this.onconnectionstatechange?.(); }
}

const stop = vi.fn();
const track = { stop, onended: null as (() => void) | null };
const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
const getUserMedia = vi.fn();
const offer = vi.fn();

async function settle() {
  await act(async () => { await Promise.resolve(); });
}

beforeEach(() => {
  window.history.replaceState({}, '', '/kiosk');
  Peer.peers = [];
  stop.mockClear();
  track.onended = null;
  getUserMedia.mockReset().mockResolvedValue(stream);
  offer.mockReset().mockResolvedValue({ ok: true, json: async () => ({ sdp: 'answer', type: 'answer' }) });
  vi.stubGlobal('RTCPeerConnection', Peer);
  vi.stubGlobal('fetch', offer);
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('remote camera lifecycle', () => {
  it('renders a neutral camera button, toggles streaming, and shows accent only once connected', async () => {
    function DockCamera() {
      const camera = useRemoteCamera();
      return <RemoteCameraButton {...camera} language="en" onToggle={camera.toggle} />;
    }
    render(<DockCamera />);
    const button = screen.getByRole('button', { name: 'Start remote camera' });
    expect(button.getAttribute('aria-pressed')).toBe('false');
    expect(button.querySelector('svg')).not.toBeNull();
    expect(button.className).toContain('h-[48px] w-[48px]');
    expect(button.className).toContain('bg-[#263c43]/90');
    fireEvent.click(button);
    await settle();
    expect(screen.getByRole('button', { name: 'Stop remote camera' })).toBe(button);
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(button.className).toContain('bg-[#263c43]/90');
    act(() => Peer.peers[0].state('connected'));
    expect(button.className).toContain('bg-[#7090ec]/25');
    expect(button.getAttribute('aria-busy')).toBe('false');
    fireEvent.click(button);
    expect(screen.getByRole('button', { name: 'Start remote camera' })).toBe(button);
    expect(button.getAttribute('aria-pressed')).toBe('false');
    expect(button.className).toContain('bg-[#263c43]/90');
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it('labels the camera toggle in Vietnamese and announces permission failures', async () => {
    getUserMedia.mockRejectedValueOnce(new Error('Permission denied'));
    function DockCamera() {
      const camera = useRemoteCamera();
      return <RemoteCameraButton {...camera} language="vi" onToggle={camera.toggle} />;
    }
    render(<DockCamera />);
    fireEvent.click(screen.getByRole('button', { name: 'Bật camera từ xa' }));
    await settle();
    expect(screen.getByRole('status').textContent).toBe('Permission denied');
    expect(screen.getByRole('button', { name: 'Bật camera từ xa' }).getAttribute('aria-pressed')).toBe('false');
  });

  it('starts video without a URL flag, reports connection, and stops tracks when toggled off', async () => {
    const { result } = renderHook(() => useRemoteCamera());
    expect(result.current).toHaveProperty('toggle');
    expect(result.current.enabled).toBe(false);
    expect(getUserMedia).not.toHaveBeenCalled();
    act(() => result.current.toggle());
    await settle();
    expect(getUserMedia).toHaveBeenCalledWith({
      video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 25 } }, audio: false,
    });
    const peer = Peer.peers[0];
    expect(peer.addTransceiver).toHaveBeenCalledWith(track, { direction: 'sendonly', streams: [stream] });
    expect(offer).toHaveBeenCalledWith(expect.stringContaining('/api/kiosk/remote-camera/offer'), expect.objectContaining({
      method: 'POST', body: JSON.stringify({ sdp: 'camera-sdp', type: 'offer' }),
    }));
    expect(result.current.active).toBe(false);
    act(() => peer.state('connected'));
    expect(result.current.active).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.enabled).toBe(false);
    expect(result.current.active).toBe(false);
    expect(stop).toHaveBeenCalledTimes(1);
    expect(peer.close).toHaveBeenCalledTimes(1);
    act(() => result.current.toggle());
    await settle();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
  });

  it('keeps the URL opt-in and releases resources on unmount', async () => {
    window.history.replaceState({}, '', '/kiosk?camera=remote');
    const { result, unmount } = renderHook(() => useRemoteCamera());
    expect(result.current).toHaveProperty('enabled', true);
    await settle();
    unmount();
    expect(stop).toHaveBeenCalledTimes(1);
    expect(Peer.peers[0].close).toHaveBeenCalledTimes(1);
  });

  it('releases a camera granted after the user already turned it off', async () => {
    let grant!: (value: typeof stream) => void;
    getUserMedia.mockReturnValue(new Promise((resolve) => { grant = resolve; }));
    const { result } = renderHook(() => useRemoteCamera());
    expect(result.current).toHaveProperty('toggle');
    act(() => result.current.toggle());
    act(() => result.current.toggle());
    await act(async () => { grant(stream); });
    expect(stop).toHaveBeenCalledTimes(1);
    expect(Peer.peers).toHaveLength(0);
  });

  it('returns to off after permission denial and allows another attempt', async () => {
    getUserMedia.mockRejectedValueOnce(new Error('Permission denied'));
    const { result } = renderHook(() => useRemoteCamera());
    expect(result.current).toHaveProperty('toggle');
    act(() => result.current.toggle());
    await settle();
    expect(result.current.enabled).toBe(false);
    expect(result.current.error).toBeTruthy();
    act(() => result.current.toggle());
    await settle();
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(result.current.error).toBeNull();
  });

  it('cancels reconnect timers when turned off', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRemoteCamera());
    expect(result.current).toHaveProperty('toggle');
    act(() => result.current.toggle());
    await settle();
    act(() => Peer.peers[0].state('connected'));
    act(() => Peer.peers[0].state('disconnected'));
    expect(result.current.active).toBe(false);
    act(() => result.current.toggle());
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(Peer.peers).toHaveLength(1);
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it('ignores a late SDP answer after being turned off', async () => {
    let answer!: (value: unknown) => void;
    offer.mockReturnValue(new Promise((resolve) => { answer = resolve; }));
    const { result } = renderHook(() => useRemoteCamera());
    expect(result.current).toHaveProperty('toggle');
    act(() => result.current.toggle());
    await settle();
    const peer = Peer.peers[0];
    act(() => result.current.toggle());
    await act(async () => { answer({ ok: true, json: async () => ({ type: 'answer', sdp: 'late' }) }); });
    expect(peer.setRemoteDescription).not.toHaveBeenCalled();
    expect(peer.close).toHaveBeenCalledTimes(1);
  });

  it('turns off when the captured camera track ends', async () => {
    const { result } = renderHook(() => useRemoteCamera());
    expect(result.current).toHaveProperty('toggle');
    act(() => result.current.toggle());
    await settle();
    act(() => track.onended?.());
    expect(result.current.enabled).toBe(false);
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it('cancels ICE gathering and its timer when stopped before signalling', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRemoteCamera());
    act(() => result.current.toggle());
    // getUserMedia resolves before connect creates its peer.
    await act(async () => {
      await Promise.resolve();
      Peer.peers[0].iceGatheringState = 'gathering';
    });
    expect(Peer.peers[0].addEventListener).toHaveBeenCalled();
    act(() => result.current.toggle());
    expect(vi.getTimerCount()).toBe(0);
    expect(Peer.peers[0].removeEventListener).toHaveBeenCalled();
    expect(offer).not.toHaveBeenCalled();
  });

  it('stays off with a useful error if camera capture is unsupported', () => {
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: undefined });
    const { result } = renderHook(() => useRemoteCamera());
    act(() => result.current.toggle());
    expect(result.current.enabled).toBe(false);
    expect(result.current.error).toContain('unavailable');
  });
});
