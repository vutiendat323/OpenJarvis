import { useCallback, useEffect, useRef, useState } from 'react';

import { getBase } from '@/lib/api';
import { buildWsProtocols } from '@/lib/useAgentEvents';

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

export interface BrowserFrameStore {
  getSnapshot: () => string | null;
  subscribe: (listener: () => void) => () => void;
  publish: (frame: string) => void;
}

export function createBrowserFrameStore(initialFrame: string | null = null): BrowserFrameStore {
  let frame = initialFrame;
  const listeners = new Set<() => void>();

  return {
    getSnapshot: () => frame,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    publish: (nextFrame) => {
      if (nextFrame === frame) return;
      frame = nextFrame;
      listeners.forEach((listener) => listener());
    },
  };
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
  | { type: 'resize'; width: number; height: number; device_scale_factor?: number }
  | { type: 'pointer'; event: 'pressed' | 'released' | 'moved'; x: number; y: number; button: string }
  | { type: 'wheel'; x: number; y: number; delta_x: number; delta_y: number }
  | { type: 'key'; event: 'down' | 'up'; key: string; code: string; text?: string; key_code?: number; modifiers?: number }
  | { type: 'text'; text: string }
  | { type: 'touch'; event: 'start' | 'move' | 'end' | 'cancel'; x: number; y: number };

type BrowserMessage = {
  type: string;
  data?: unknown;
  format?: unknown;
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
      ? `data:image/${message.format === 'png' ? 'png' : 'jpeg'};base64,${message.data}`
      : current.frame,
  };
}

function sameBrowserState(left: BrowserViewState, right: BrowserViewState): boolean {
  const sameFocus = left.focusedElement === right.focusedElement || (
    left.focusedElement !== null
    && right.focusedElement !== null
    && left.focusedElement.tag === right.focusedElement.tag
    && left.focusedElement.name === right.focusedElement.name
    && left.focusedElement.type === right.focusedElement.type
    && left.focusedElement.editable === right.focusedElement.editable
  );

  return left.status === right.status
    && left.url === right.url
    && left.title === right.title
    && left.frame === right.frame
    && left.width === right.width
    && left.height === right.height
    && left.loading === right.loading
    && sameFocus
    && left.targetId === right.targetId
    && left.agentAction === right.agentAction
    && left.error === right.error;
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

export function useSharedBrowser(): Omit<BrowserViewState, 'frame'> & {
  send: (command: BrowserCommand) => void;
  hitTest: (x: number, y: number) => Promise<boolean>;
  frameStore: BrowserFrameStore;
} {
  const [state, setState] = useState<BrowserViewState>(initialBrowserState);
  const stateRef = useRef(state);
  const [frameStore] = useState(createBrowserFrameStore);
  const socketRef = useRef<WebSocket | null>(null);
  const nextProbeRef = useRef(0);
  const probesRef = useRef(new Map<number, (interactive: boolean) => void>());

  const commitState = useCallback((next: BrowserViewState) => {
    if (sameBrowserState(stateRef.current, next)) return;
    stateRef.current = next;
    setState(next);
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const connect = () => {
      if (disposed) return;
      const socket = new WebSocket(browserWsUrl(), buildWsProtocols());
      socketRef.current = socket;
      socket.onopen = () => commitState({ ...stateRef.current, status: 'connected' });
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as BrowserMessage;
          const probe = message as BrowserMessage & { request_id?: number; interactive?: boolean };
          if (probe.type === 'hit_test' && probe.request_id !== undefined) {
            probesRef.current.get(probe.request_id)?.(probe.interactive !== false);
            return;
          }
          const current = stateRef.current;
          const next = reduceBrowserMessage(current, message);
          if (message.type === 'frame' && next.frame !== null) {
            // Only the preview image subscribes to frames, keeping Kiosk state stable.
            frameStore.publish(next.frame);
            commitState({ ...next, frame: current.frame });
          } else {
            commitState(next);
          }
        } catch { /* ignore malformed browser event */ }
      };
      socket.onclose = () => {
        for (const resolve of [...probesRef.current.values()]) resolve(true);
        if (!disposed) {
          commitState({ ...stateRef.current, status: 'disconnected' });
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
      for (const resolve of [...probesRef.current.values()]) resolve(true);
    };
  }, [commitState, frameStore]);

  const send = useCallback((command: BrowserCommand) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(command));
    }
  }, []);

  const hitTest = useCallback((x: number, y: number): Promise<boolean> => {
    const socket = socketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) return Promise.resolve(true);
    return new Promise((resolve) => {
      const requestId = ++nextProbeRef.current;
      const finish = (interactive: boolean) => {
        clearTimeout(timer);
        probesRef.current.delete(requestId);
        resolve(interactive);
      };
      const timer = setTimeout(() => finish(true), 500);
      probesRef.current.set(requestId, finish);
      socket.send(JSON.stringify({ type: 'hit_test', request_id: requestId, x, y }));
    });
  }, []);

  return { ...state, send, hitTest, frameStore };
}
