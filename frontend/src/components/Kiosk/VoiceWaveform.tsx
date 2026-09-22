import { useEffect, useRef } from 'react';

export function VoiceWaveform({ speaking, getFrequencyData }: {
  speaking: boolean;
  getFrequencyData: () => Uint8Array | Float32Array;
}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const draw = () => {
      const data = speaking ? getFrequencyData() : null;
      Array.from(container.current?.children ?? []).forEach((bar, index) => {
        const value = data?.[Math.floor(index * data.length / 12)] ?? 0;
        const level = data instanceof Float32Array ? Math.max(0, (value + 100) / 100) : value / 255;
        (bar as HTMLElement).style.height = `${4 + Math.min(1, level) * 24}px`;
      });
    };
    draw();
    if (!speaking) return;
    const timer = setInterval(draw, 50);
    return () => clearInterval(timer);
  }, [speaking, getFrequencyData]);
  return <div ref={container} role="img" aria-label={speaking ? 'Assistant speaking' : 'Voice ready'} className="flex h-8 items-center justify-center gap-1">
    {Array.from({ length: 9 }, (_, i) => <span key={i} className="w-1 rounded-full bg-cyan-400 transition-[height] duration-75" style={{ height: 4 }} />)}
  </div>;
}
