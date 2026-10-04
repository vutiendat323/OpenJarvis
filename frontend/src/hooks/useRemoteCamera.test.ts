import { describe, expect, it } from 'vitest';

import { remoteCameraOfferUrl, remoteCameraRequested } from './useRemoteCamera';

describe('remoteCameraRequested', () => {
  it('is opt-in per page load through an explicit ?camera=remote', () => {
    expect(remoteCameraRequested('?camera=remote')).toBe(true);
    expect(remoteCameraRequested('?x=1&camera=remote')).toBe(true);
    expect(remoteCameraRequested('?camera=usb')).toBe(false);
    expect(remoteCameraRequested('')).toBe(false);
  });
});

describe('remoteCameraOfferUrl', () => {
  it('reaches the backend directly from the Vite dev server', () => {
    expect(remoteCameraOfferUrl({ origin: 'http://127.0.0.1:5173', port: '5173' }, ''))
      .toBe('http://localhost:8000/api/kiosk/remote-camera/offer');
  });

  it('uses the configured API base, or the page origin in production', () => {
    expect(remoteCameraOfferUrl({ origin: 'http://x', port: '' }, 'https://api.example'))
      .toBe('https://api.example/api/kiosk/remote-camera/offer');
    expect(remoteCameraOfferUrl({ origin: 'https://kiosk.local', port: '' }, ''))
      .toBe('https://kiosk.local/api/kiosk/remote-camera/offer');
  });
});
