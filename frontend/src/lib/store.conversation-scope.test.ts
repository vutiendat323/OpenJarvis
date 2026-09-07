import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// A server-issued conversation id belongs to one local conversation. Switching
// chats must not carry another chat's tool working set along with it, and a
// history written before this field existed must still load.

const CONVERSATIONS_KEY = 'openjarvis-conversations';

class MemoryStorage {
  private store = new Map<string, string>();
  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
}

function storage(): MemoryStorage {
  return (globalThis as unknown as { localStorage: MemoryStorage }).localStorage;
}

beforeEach(() => {
  vi.resetModules();
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage =
    new MemoryStorage();
});

afterEach(() => {
  (globalThis as unknown as { localStorage?: MemoryStorage }).localStorage =
    undefined;
});

describe('server conversation id', () => {
  it('keeps a separate server id per local conversation', async () => {
    const { useAppStore } = await import('./store');
    const store = useAppStore.getState();

    const first = store.createConversation('test-model');
    const second = store.createConversation('test-model');
    store.setServerConversationId(first, 'aaaa'.repeat(8));
    store.setServerConversationId(second, 'bbbb'.repeat(8));

    const byId = Object.fromEntries(
      useAppStore.getState().conversations.map((c) => [c.id, c]),
    );
    expect(byId[first].serverConversationId).toBe('aaaa'.repeat(8));
    expect(byId[second].serverConversationId).toBe('bbbb'.repeat(8));
  });

  it('persists the server id across a reload', async () => {
    const { useAppStore } = await import('./store');
    const id = useAppStore.getState().createConversation('test-model');
    useAppStore.getState().setServerConversationId(id, 'cccc'.repeat(8));

    const persisted = JSON.parse(storage().getItem(CONVERSATIONS_KEY) as string);

    expect(persisted.conversations[id].serverConversationId).toBe(
      'cccc'.repeat(8),
    );
  });

  it('loads history written before the field existed', async () => {
    storage().setItem(
      CONVERSATIONS_KEY,
      JSON.stringify({
        version: 1,
        activeId: 'old-1',
        conversations: {
          'old-1': {
            id: 'old-1',
            title: 'Older chat',
            createdAt: 1,
            updatedAt: 1,
            model: 'test-model',
            messages: [],
          },
        },
      }),
    );

    const { useAppStore } = await import('./store');
    useAppStore.getState().loadConversations();

    const [conversation] = useAppStore.getState().conversations;
    expect(conversation.id).toBe('old-1');
    expect(conversation.serverConversationId).toBeUndefined();
  });

  it('ignores an id for a conversation that no longer exists', async () => {
    const { useAppStore } = await import('./store');
    const before = useAppStore.getState().conversations.length;

    useAppStore.getState().setServerConversationId('deleted-1', 'dddd'.repeat(8));

    expect(useAppStore.getState().conversations.length).toBe(before);
  });
});
