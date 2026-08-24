import { useEffect, useRef } from 'react';
import { getBase } from './api';

export interface AgentEvent {
  type: string;
  timestamp: number;
  data: Record<string, unknown>;
}

function buildWsUrl(agentId?: string, presentationSessionId?: string): string {
  const base = getBase();
  let origin: string;
  if (base) {
    origin = base.replace(/^http/, 'ws');
  } else {
    const loc = window.location;
    origin = `${loc.protocol === 'https:' ? 'wss:' : 'ws:'}//${loc.host}`;
  }
  const params = new URLSearchParams();
  if (agentId) params.set('agent_id', agentId);
  if (presentationSessionId) {
    params.set('presentation_session_id', presentationSessionId);
  }
  const query = params.toString();
  return `${origin}/v1/agents/events${query ? `?${query}` : ''}`;
}

/**
 * Subscribe to agent events over WebSocket.
 * Auto-reconnects with backoff when the socket drops.
 */
export function useAgentEvents(
  agentId: string | undefined,
  onEvent: (event: AgentEvent) => void,
  eventTypes?: readonly string[],
  presentationSessionId?: string,
): void {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const typesRef = useRef(eventTypes);
  typesRef.current = eventTypes;

  useEffect(() => {
    if (!agentId && !presentationSessionId) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closed) return;
      try {
        ws = new WebSocket(buildWsUrl(agentId, presentationSessionId));
      } catch {
        schedule();
        return;
      }
      ws.onopen = () => {
        retry = 0;
      };
      ws.onmessage = (msg) => {
        try {
          const payload = JSON.parse(msg.data) as AgentEvent;
          const allowed = typesRef.current;
          if (allowed && !allowed.includes(payload.type)) return;
          onEventRef.current(payload);
        } catch {
          // ignore malformed payload
        }
      };
      ws.onclose = () => {
        if (!closed) schedule();
      };
      ws.onerror = () => {
        ws?.close();
      };
    };

    const schedule = () => {
      if (closed) return;
      const delay = Math.min(30000, 1000 * 2 ** Math.min(retry, 5));
      retry += 1;
      reconnectTimer = setTimeout(connect, delay);
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [agentId, presentationSessionId]);
}
