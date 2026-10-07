import { describe, expect, it } from 'vitest';

describe('Cloud Models catalogue', () => {
  it('offers V4.1 Flash using the canonical DeepSeek API ID', async () => {
    globalThis.localStorage = {
      getItem: () => null,
      setItem: () => undefined,
    } as unknown as Storage;
    const { CLOUD_PROVIDERS } = await import('./CommandPalette');
    const deepSeek = CLOUD_PROVIDERS.find((provider) => provider.name === 'DeepSeek');
    expect(deepSeek?.envKey).toBe('DEEPSEEK_API_KEY');
    expect(deepSeek?.models).toEqual([{
      id: 'deepseek-flash',
      name: 'DeepSeek V4.1 Flash',
      desc: 'DeepSeek V4.1 Flash: ultra cost-efficient, extremely cheap caching, ideal for high-volume workloads.',
    }]);
  });
  it('offers both gpt-6-luna and gpt-5.6-luna under OpenAI', async () => {
    globalThis.localStorage = {
      getItem: () => null,
      setItem: () => undefined,
    } as unknown as Storage;
    const commandPalette = await import('./CommandPalette');
    const providers = (
      commandPalette as unknown as {
        CLOUD_PROVIDERS?: Array<{
          name: string;
          models: Array<{ id: string }>;
        }>;
      }
    ).CLOUD_PROVIDERS;

    const openAI = providers?.find((provider) => provider.name === 'OpenAI');
    expect(openAI?.models.map((model) => model.id) ?? []).toContain('gpt-6-luna');
    expect(openAI?.models.map((model) => model.id) ?? []).toContain('gpt-5.6-luna');
  });
});
