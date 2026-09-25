import React, { useEffect, useRef, useState } from 'react';
import { Mic } from 'lucide-react';

export interface VoiceWaveformProps {
  speaking?: boolean;
  listening?: boolean;
  voiceStatus?: string;
  getFrequencyData?: () => Uint8Array | Float32Array;
  className?: string;
  onMicClick?: () => void;
}

export function computeWaveformHeights(
  data: Uint8Array | Float32Array | undefined,
  isActive: boolean,
  prevHeights: [number, number, number] = [7, 16, 7],
): [number, number, number] {
  if (!isActive) {
    return [7, 16, 7];
  }
  if (!data || data.length === 0) {
    return [8, 18, 8];
  }

  const getBandAvg = (start: number, end: number): number => {
    const s = Math.min(start, data.length);
    const e = Math.min(end, data.length);
    if (e <= s) return 0;
    let sum = 0;
    for (let i = s; i < e; i++) {
      const val = data[i] ?? 0;
      if (data instanceof Float32Array) {
        sum += Math.max(0, Math.min(1, (val + 100) / 100));
      } else {
        sum += (val as number) / 255;
      }
    }
    return sum / (e - s);
  };

  // 3 Voice Frequency Bands:
  // Low (~100-350Hz)
  const v0 = getBandAvg(2, 9);
  // Mid / Formants (~350-1000Hz)
  const v1 = getBandAvg(9, 25);
  // High (~1000-2500Hz)
  const v2 = getBandAvg(25, 55);

  const minSide = 7;
  const maxSide = 17;
  const minCenter = 16;
  const maxCenter = 28;

  const target0 = Math.round(minSide + v0 * (maxSide - minSide));
  const target2 = Math.round(minSide + v2 * (maxSide - minSide));

  // Center bar is driven by formants and overall intensity, and must ALWAYS be taller than both side bars
  let target1 = Math.round(minCenter + v1 * (maxCenter - minCenter));
  target1 = Math.max(target1, Math.max(target0, target2) + 4);
  target1 = Math.min(maxCenter, target1);

  // Smooth lerp (0.35)
  const lerp = 0.35;
  const h0 = Math.round(prevHeights[0] + (target0 - prevHeights[0]) * lerp);
  const h1 = Math.round(prevHeights[1] + (target1 - prevHeights[1]) * lerp);
  const h2 = Math.round(prevHeights[2] + (target2 - prevHeights[2]) * lerp);

  const finalCenter = Math.max(h1, Math.max(h0, h2) + 3);

  return [h0, finalCenter, h2];
}

export function VoiceWaveform({
  speaking = false,
  listening = false,
  voiceStatus,
  getFrequencyData,
  className = '',
  onMicClick,
}: VoiceWaveformProps) {
  const isActive = speaking || listening || voiceStatus === 'speaking' || voiceStatus === 'listening';
  const [heights, setHeights] = useState<[number, number, number]>(() => (isActive ? [8, 18, 8] : [7, 16, 7]));
  const heightsRef = useRef<[number, number, number]>(heights);
  heightsRef.current = heights;

  useEffect(() => {
    if (!isActive) {
      setHeights([7, 16, 7]);
      return;
    }

    let animId: number;
    const tick = () => {
      const data = getFrequencyData ? getFrequencyData() : undefined;
      const next = computeWaveformHeights(data, isActive, heightsRef.current);
      if (
        next[0] !== heightsRef.current[0] ||
        next[1] !== heightsRef.current[1] ||
        next[2] !== heightsRef.current[2]
      ) {
        setHeights(next);
      }
      animId = requestAnimationFrame(tick);
    };

    animId = requestAnimationFrame(tick);
    return () => {
      if (animId) cancelAnimationFrame(animId);
    };
  }, [getFrequencyData, isActive]);

  return (
    <div
      data-testid="voice-dock-pill"
      role="status"
      aria-label={isActive ? 'Microphone active and listening' : 'Microphone ready'}
      className={`inline-flex items-center justify-between w-[112px] h-[54px] rounded-[15px] bg-[#17252a]/90 p-[3px] pl-[22px] pr-[3px] shadow-[0_8px_28px_rgba(0,0,0,0.38)] backdrop-blur-md select-none transition-all duration-200 ${className}`}
      style={{ borderRadius: '15px' }}
    >
      {/* Nửa bên trái - Sóng âm (Audio Waveform): 3 cột sóng âm thanh màu xanh dương nhạt/cyan dựng đứng */}
      <div
        data-testid="voice-dock-waveform"
        className="flex h-full items-center justify-center gap-[3.5px]"
        aria-hidden="true"
      >
        <span
          data-testid="voice-dock-waveform-bar-0"
          className="w-[3.5px] rounded-full bg-[#7090ec] transition-[height] duration-75"
          style={{ height: `${heights[0]}px` }}
        />
        <span
          data-testid="voice-dock-waveform-bar-1"
          className="w-[3.5px] rounded-full bg-[#7090ec] transition-[height] duration-75"
          style={{ height: `${heights[1]}px` }}
        />
        <span
          data-testid="voice-dock-waveform-bar-2"
          className="w-[3.5px] rounded-full bg-[#7090ec] transition-[height] duration-75"
          style={{ height: `${heights[2]}px` }}
        />
      </div>

      {/* Nửa bên phải - Nút Micro (Active Mic): Biểu tượng micro màu trắng trong khối bo góc xám xanh */}
      <button
        type="button"
        data-testid="active-mic-btn"
        onClick={onMicClick}
        disabled={!onMicClick}
        aria-label="Active microphone"
        className={`flex h-[48px] w-[48px] items-center justify-center rounded-[13px] bg-[#263c43] text-white shadow-sm transition-transform ${
          onMicClick ? 'cursor-pointer hover:scale-105 active:scale-95' : 'cursor-default'
        }`}
        style={{ borderRadius: '13px' }}
      >
        <Mic size={18} strokeWidth={2.2} className="text-white" />
      </button>
    </div>
  );
}
