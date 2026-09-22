import { useCallback, useEffect, useRef, useState } from 'react';

import { getApiKey, getBase } from '@/lib/api';

export interface BrowserViewState {
  status: 'connecting' | 'connected' | 'disconnected';
  url: string;
  title: string;
  frame: string | null;
  width: number;
  height: number;
  loading: boolean;
  focusedElement: { tag: string; name: string; type: string; editable?: boolean } | null;
  targetId: string;
  agentAction: string | null;
  error: string | null;
}

export const initialBrowserState: BrowserViewState = {
  status: 'connecting',
  url: 'about:blank',
  title: '',
  frame: null,
  width: 0,
  height: 0,
  loading: false,
  focusedElement: null,
  targetId: '',
  agentAction: null,
  error: null,
};

export type BrowserCommand =
  | { type: 'navigate'; url: string }
  | { type: 'back' | 'forward' | 'reload' }
  | { type: 'resize'; width: number; height: number }
  | { type: 'pointer'; event: 'pressed' | 'released' | 'moved'; x: number; y: number; button: string }
  | { type: 'wheel'; x: number; y: number; delta_x: number; delta_y: number }
  | { type: 'key'; event: 'down' | 'up'; key: string; code: string; text?: string; key_code?: number; modifiers?: number }
  | { type: 'text'; text: string }
  | { type: 'touch'; event: 'start' | 'move' | 'end' | 'cancel'; x: number; y: number };

type BrowserMessage = {
  type: string;
  data?: unknown;
  url?: unknown;
  title?: unknown;
  width?: unknown;
  height?: unknown;
  loading?: unknown;
  focused_element?: unknown;
  target_id?: unknown;
  agent_action?: unknown;
  message?: unknown;
};

export function reduceBrowserMessage(
  current: BrowserViewState,
  message: BrowserMessage,
): BrowserViewState {
  if (message.type === 'error') return { ...current, error: typeof message.message === 'string' ? message.message : 'Browser action failed' };
  if (message.type !== 'state' && message.type !== 'frame') return current;
  return {
    ...current,
    status: 'connected',
    url: typeof message.url === 'string' ? message.url : current.url,
    title: typeof message.title === 'string' ? message.title : current.title,
    width: typeof message.width === 'number' ? message.width : current.width,
    height: typeof message.height === 'number' ? message.height : current.height,
    loading: typeof message.loading === 'boolean' ? message.loading : current.loading,
    focusedElement: message.focused_element && typeof message.focused_element === 'object'
      ? message.focused_element as BrowserViewState['focusedElement']
      : message.focused_element === null ? null : current.focusedElement,
    targetId: typeof message.target_id === 'string' ? message.target_id : current.targetId,
    agentAction: typeof message.agent_action === 'string' ? message.agent_action : message.agent_action === null ? null : current.agentAction,
    error: typeof message.url === 'string' && message.url !== current.url ? null : current.error,
    frame: message.type === 'frame' && typeof message.data === 'string'
      ? `data:image/jpeg;base64,${message.data}`
      : current.frame,
  };
}

export function remotePoint(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  viewportWidth: number,
  viewportHeight: number,
): { x: number; y: number } {
  return {
    x: Math.max(0, Math.min(viewportWidth, Math.round((clientX - rect.left) * viewportWidth / rect.width))),
    y: Math.max(0, Math.min(viewportHeight, Math.round((clientY - rect.top) * viewportHeight / rect.height))),
  };
}

function browserWsUrl(): string {
  const base = getBase();
  if (base) return `${base.replace(/^http/, 'ws')}/api/kiosk/browser/ws`;
  if (window.location.port === '5173') return 'ws://localhost:8000/api/kiosk/browser/ws';
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/kiosk/browser/ws`;
}

export function useSharedBrowser(): BrowserViewState & { send: (command: BrowserCommand) => void } {
  const [state, setState] = useState<BrowserViewState>(initialBrowserState);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const connect = () => {
      if (disposed) return;
      const key = getApiKey();
      const socket = new WebSocket(`${browserWsUrl()}${key ? `?token=${encodeURIComponent(key)}` : ''}`);
      socketRef.current = socket;
      socket.onopen = () => setState((current) => ({ ...current, status: 'connected' }));
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as BrowserMessage;
          setState((current) => reduceBrowserMessage(current, message));
        } catch { /* ignore malformed browser event */ }
      };
      socket.onclose = () => {
        if (!disposed) {
          setState((current) => ({ ...current, status: 'disconnected' }));
          timer = setTimeout(connect, 500);
        }
      };
      socket.onerror = () => socket.close();
    };
    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      socketRef.current?.close();
    };
  }, []);

  const send = useCallback((command: BrowserCommand) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(command));
    }
  }, []);

  return { ...state, send };
}
