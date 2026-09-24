import type { ResearchEvent, SSEEvent } from '../types';
import { getBase, authHeaders } from './api';

// The server owns the conversation scope that tool evidence is filed under.
// It issues an opaque id in this response header; sending the same id back on
// the next turn rejoins that working set. The frontend never invents a value —
// an id the server did not issue is replaced with a fresh one.
export const CONVERSATION_HEADER = 'X-OpenJarvis-Conversation';

export interface ChatRequest {
  model: string;
  messages: Array<{ role: string; content: string }>;
  stream: true;
  temperature?: number;
  max_tokens?: number;
  /** Server-issued scope id. Travels as a header, never in the OpenAI body. */
  conversation_id?: string;
}

export async function* streamChat(
  request: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const base = getBase();
  const { conversation_id: conversationId, ...body } = request;
  const headers = authHeaders({ 'Content-Type': 'application/json' });
  if (conversationId) {
    headers[CONVERSATION_HEADER] = conversationId;
  }
  const response = await fetch(`${base}/v1/chat/completions`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Chat request failed: ${response.status}`);
  }

  const issued = response.headers.get(CONVERSATION_HEADER);
  if (issued) {
    // Before any content: an aborted turn must still leave the caller able to
    // rejoin this scope on the next one.
    yield { event: 'conversation_scope', data: issued };
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let currentEvent: string | undefined;

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') return;
          yield { event: currentEvent, data };
          currentEvent = undefined;
        } else if (line.trim() === '') {
          currentEvent = undefined;
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function* streamResearch(
  query: string,
  model?: string,
  signal?: AbortSignal,
): AsyncGenerator<ResearchEvent> {
  // /api/research is mounted at the server root — strip any trailing /v1
  // from the base so configurations like "http://host:8000/v1" still resolve.
  const base = getBase().replace(/\/v1\/?$/, '');
  const response = await fetch(`${base}/api/research`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ query, ...(model ? { model } : {}) }),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Research request failed: ${response.status}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') return;
        try {
          const parsed = JSON.parse(data) as ResearchEvent;
          yield parsed;
          if (parsed.type === 'done') return;
        } catch {
          // skip malformed chunks
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
