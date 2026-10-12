import { useEffect, useRef, type CSSProperties } from 'react';
import type { LocalVoiceStatus } from '@/hooks/voiceStatus';
import './kioskThemes.css';

export function SpeechGlow({ status, getAudioLevel, lightBackground }: {
  status: LocalVoiceStatus;
  getAudioLevel: () => number;
  lightBackground: boolean;
}) {
  const glowRef = useRef<HTMLDivElement>(null);
  const active = status === 'listening' || status === 'speaking';
  const agent = status === 'speaking';

  useEffect(() => {
    const glow = glowRef.current;
    if (!glow) return;
    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let lastPaint = -Infinity;

    const paint = (level: number) => {
      glow.style.opacity = (level * (lightBackground ? 0.18 : 0.14)).toFixed(3);
      glow.style.transform = `scale(${1 + level * 0.12})`;
    };
    const tick = (time: number) => {
      if (time - lastPaint >= 32) {
        const volume = getAudioLevel();
        // Ignore quiet room noise, then gently boost conversational levels.
        paint(Number.isFinite(volume) ? Math.min(1, Math.max(0, (volume - 0.008) * 8)) : 0);
        lastPaint = time;
      }
      frame = requestAnimationFrame(tick);
    };
    const sync = () => {
      cancelAnimationFrame(frame);
      paint(0);
      lastPaint = -Infinity;
      if (active && !motion?.matches && !document.hidden) frame = requestAnimationFrame(tick);
    };

    sync();
    motion?.addEventListener('change', sync);
    document.addEventListener('visibilitychange', sync);
    return () => {
      cancelAnimationFrame(frame);
      motion?.removeEventListener('change', sync);
      document.removeEventListener('visibilitychange', sync);
    };
  }, [active, getAudioLevel, lightBackground]);

  return (
    <div
      ref={glowRef}
      aria-hidden="true"
      data-testid="kiosk-center-glow"
      data-speaking={active}
      className="kiosk-speech-glow absolute -inset-[12%] pointer-events-none z-0"
      style={{
        opacity: 0,
        '--speech-primary': agent ? '#8585d9' : '#38bca8',
        '--speech-secondary': agent ? '#559cce' : '#5e9abb',
        '--speech-position': agent ? '58%' : '42%',
      } as CSSProperties}
    />
  );
}
