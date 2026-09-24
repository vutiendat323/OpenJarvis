import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The server owns the conversation scope: it issues an opaque id in the
// X-OpenJarvis-Conversation response header, and a later turn must send that
// exact id back so both turns share one tool working set. The frontend never
// invents the value.

const CONVERSATION_HEADER = 'X-OpenJarvis-Conversation';
const ISSUED = '0123456789abcdef0123456789abcdef';

const fetchMock = vi.fn<typeof fetch>();

class MemoryStorage {
  private store = new Map<string, string>();
  getItem(k: string): string | null {
    return this.store.get(k) ?? null;
  }
  setItem(k: string, v: string): void {
    this.store.set(k, String(v));
  }
  removeItem(k: string): void {
    this.store.delete(k);
  }
  clear(): void {
    this.store.clear();
  }
}

function streamingResponse(conversationId?: string): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      controller.enqueue(encoder.encode('event: inference_start\ndata: {}\n\n'));
      controller.enqueue(encoder.encode('data: {"choices":[{"delta":{}}]}\n\n'));
      controller.enqueue(encoder.encode('data: [DONE]\n\n'));
      controller.close();
    },
  });
  const headers = new Headers({ 'Content-Type': 'text/event-stream' });
  if (conversationId) headers.set(CONVERSATION_HEADER, conversationId);
  return new Response(body, { status: 200, headers });
}

beforeEach(() => {
  vi.resetModules();
  fetchMock.mockReset();
  globalThis.fetch = fetchMock;
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage =
    new MemoryStorage();
});

afterEach(() => {
  (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage =
    undefined;
});

async function freshSse() {
  return await import('./sse');
}

const baseRequest = {
  model: 'test-model',
  messages: [{ role: 'user', content: 'hi' }],
  stream: true as const,
};

async function drain(generator: AsyncGenerator<{ event?: string; data: string }>) {
  const events: Array<{ event?: string; data: string }> = [];
  for await (const event of generator) events.push(event);
  return events;
}

describe('streamChat conversation scope', () => {
  it('surfaces the server-issued id before any content event', async () => {
    fetchMock.mockResolvedValueOnce(streamingResponse(ISSUED));
    const { streamChat } = await freshSse();

    const events = await drain(streamChat(baseRequest));

    expect(events[0]).toEqual({ event: 'conversation_scope', data: ISSUED });
    // It must arrive first: the caller stores it even if the turn is aborted
    // part-way through, so the next turn can still rejoin the same scope.
    expect(events.slice(1).some((e) => e.event === 'conversation_scope')).toBe(
      false,
    );
  });

  it('sends a known id back on the next request', async () => {
    fetchMock.mockResolvedValue(streamingResponse(ISSUED));
    const { streamChat } = await freshSse();

    await drain(streamChat({ ...baseRequest, conversation_id: ISSUED }));

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get(CONVERSATION_HEADER)).toBe(ISSUED);
  });

  it('never serializes the id into the OpenAI-compatible body', async () => {
    fetchMock.mockResolvedValue(streamingResponse(ISSUED));
    const { streamChat } = await freshSse();

    await drain(streamChat({ ...baseRequest, conversation_id: ISSUED }));

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).not.toHaveProperty(
      'conversation_id',
    );
  });

  it('omits the header on a first turn that has no id yet', async () => {
    fetchMock.mockResolvedValue(streamingResponse(ISSUED));
    const { streamChat } = await freshSse();

    await drain(streamChat(baseRequest));

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get(CONVERSATION_HEADER)).toBeNull();
  });

  it('emits no scope event when the server sends no header', async () => {
    fetchMock.mockResolvedValueOnce(streamingResponse(undefined));
    const { streamChat } = await freshSse();

    const events = await drain(streamChat(baseRequest));

    expect(events.some((e) => e.event === 'conversation_scope')).toBe(false);
  });
});
