import type { KioskTheme } from '../Kiosk/kioskThemes';

/** idle → listening (mic) → thinking (STT+LLM+TTS) → speaking (playback) → idle */
export type VoiceStatus = 'idle' | 'listening' | 'thinking' | 'speaking';
export type VoiceLang = 'vi' | 'en';

export interface VisualizerSettings {
  style: 'screen';
  theme: KioskTheme;
  showCaptions: boolean;
  showPet: boolean;
  petScale: number;
}

export const VISUALIZER_DEFAULTS: VisualizerSettings = {
  style: 'screen',
  theme: 'midnight-blue',
  showCaptions: true,
  showPet: true,
  petScale: 2.5,
};
