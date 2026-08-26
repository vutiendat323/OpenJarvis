import React, { useEffect, useState } from 'react';
import { CodexPet, DEFAULT_MINTY_MANIFEST } from './CodexPet';
import { parsePetManifest } from './codexPetAtlas';
import { resolvePetState } from './codexPetState';
import { useFloatingPet } from '@/hooks/useFloatingPet';
import type { PetManifest, PetPosition, PetSize } from './types';
import type { LocalVoiceStatus } from '@/hooks/voiceStatus';

export interface FloatingCodexPetProps {
  /** Voice status from voice assistant */
  voiceStatus?: LocalVoiceStatus | string | null;
  /** Activity detail for task or animation override */
  activityDetail?: string | null;
  /** Speech text for speech bubble */
  speechText?: string | null;
  /** Caption text alias from voice mode */
  assistantCaptionText?: string | null;
  /** Direct manifest object override */
  manifest?: PetManifest;
  /** URL to pet.json manifest (defaults to '/pets/minty/pet.json') */
  manifestUrl?: string;
  /** Initial spawn position */
  initialPosition?: PetPosition;
  /** Scale factor override (defaults to manifest.scale ?? 2) */
  scale?: number;
  /** Render mode: 'css' or 'canvas' */
  renderMode?: 'css' | 'canvas';
  /** Whether to show drop shadow */
  showShadow?: boolean;
  /** Enable autonomous wandering when idle */
  enableWandering?: boolean;
  /** Additional CSS class names */
  className?: string;
  /** Additional inline styles */
  style?: React.CSSProperties;
}

/**
 * Loads a PetManifest from a URL or returns fallback DEFAULT_MINTY_MANIFEST on failure.
 */
export async function fetchPetManifest(
  url: string = '/pets/minty/pet.json',
  fetchFn: typeof fetch = typeof fetch !== 'undefined' ? fetch : (globalThis.fetch as typeof fetch),
): Promise<PetManifest> {
  try {
    if (typeof fetchFn !== 'function') {
      return DEFAULT_MINTY_MANIFEST;
    }
    const res = await fetchFn(url);
    if (!res.ok) {
      return DEFAULT_MINTY_MANIFEST;
    }
    const json = await res.json();
    return parsePetManifest(json);
  } catch {
    return DEFAULT_MINTY_MANIFEST;
  }
}

/**
 * FloatingCodexPet overlay component.
 * Assembles floating physics, state resolution, asynchronous manifest loading,
 * sprite rendering, and speech bubble display in a fixed layer.
 */
export function FloatingCodexPet({
  voiceStatus,
  activityDetail,
  speechText,
  assistantCaptionText,
  manifest: manifestProp,
  manifestUrl = '/pets/minty/pet.json',
  initialPosition,
  scale: scaleProp,
  renderMode = 'css',
  showShadow = true,
  enableWandering = true,
  className = '',
  style,
}: FloatingCodexPetProps) {
  const [activeManifest, setActiveManifest] = useState<PetManifest>(
    () => manifestProp ?? DEFAULT_MINTY_MANIFEST,
  );

  // Synchronize manifest if prop changes
  useEffect(() => {
    if (manifestProp) {
      setActiveManifest(manifestProp);
      return;
    }

    let isMounted = true;
    void fetchPetManifest(manifestUrl).then((loaded) => {
      if (isMounted) {
        setActiveManifest(loaded);
      }
    });

    return () => {
      isMounted = false;
    };
  }, [manifestProp, manifestUrl]);

  const activeScale = scaleProp ?? activeManifest.scale ?? 2;
  const petSize: PetSize = {
    width: activeManifest.size.width * activeScale,
    height: activeManifest.size.height * activeScale,
  };

  const {
    position,
    isDragging,
    dragDeltaX,
    petHandlers,
  } = useFloatingPet({
    initialPosition,
    petSize,
    enableWandering,
  });

  const petState = resolvePetState(
    voiceStatus,
    activityDetail,
    isDragging,
    dragDeltaX,
  );

  const effectiveSpeechText = speechText ?? assistantCaptionText ?? undefined;
  const effectiveVoiceStatus = voiceStatus ?? undefined;

  return (
    <div
      data-testid="floating-codex-pet"
      data-state={petState}
      data-dragging={isDragging ? 'true' : 'false'}
      className={`fixed z-40 touch-none select-none ${className}`}
      style={{
        position: 'fixed',
        left: 0,
        top: 0,
        transform: `translate3d(${position.x}px, ${position.y}px, 0)`,
        zIndex: 40,
        cursor: isDragging ? 'grabbing' : 'grab',
        ...style,
      }}
      {...petHandlers}
    >
      <CodexPet
        manifest={activeManifest}
        state={petState}
        scale={activeScale}
        renderMode={renderMode}
        isDragging={isDragging}
        speechText={effectiveSpeechText}
        voiceStatus={effectiveVoiceStatus}
        showShadow={showShadow}
      />
    </div>
  );
}
