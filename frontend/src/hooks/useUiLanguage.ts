import { useCallback, useEffect, useState } from 'react';

export type UiLanguage = 'vi' | 'en';

export const UI_LANGUAGE_STORAGE_KEY = 'openjarvis.ui-language';
export const UI_LANGUAGE_EVENT = 'openjarvis:ui-language';

export function readUiLanguage(storage: Pick<Storage, 'getItem'>): UiLanguage {
  try {
    return storage.getItem(UI_LANGUAGE_STORAGE_KEY) === 'en' ? 'en' : 'vi';
  } catch {
    return 'vi';
  }
}

export function persistUiLanguage(
  storage: Pick<Storage, 'setItem'>,
  language: UiLanguage,
): void {
  try {
    storage.setItem(UI_LANGUAGE_STORAGE_KEY, language);
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

export function useUiLanguage(): {
  language: UiLanguage;
  setLanguage: (language: UiLanguage) => void;
} {
  const [language, setLanguageState] = useState<UiLanguage>(() => {
    if (typeof window === 'undefined') return 'vi';
    return readUiLanguage(window.localStorage);
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const handleStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === UI_LANGUAGE_STORAGE_KEY) {
        setLanguageState(readUiLanguage(window.localStorage));
      }
    };

    const handleCustom = (event: Event) => {
      const customEvent = event as CustomEvent<UiLanguage>;
      if (customEvent.detail === 'en' || customEvent.detail === 'vi') {
        setLanguageState(customEvent.detail);
      } else {
        setLanguageState(readUiLanguage(window.localStorage));
      }
    };

    window.addEventListener('storage', handleStorage);
    window.addEventListener(UI_LANGUAGE_EVENT, handleCustom);
    return () => {
      window.removeEventListener('storage', handleStorage);
      window.removeEventListener(UI_LANGUAGE_EVENT, handleCustom);
    };
  }, []);

  const setLanguage = useCallback((nextLanguage: UiLanguage) => {
    setLanguageState(nextLanguage);
    if (typeof window !== 'undefined') {
      persistUiLanguage(window.localStorage, nextLanguage);
      window.dispatchEvent(new CustomEvent(UI_LANGUAGE_EVENT, { detail: nextLanguage }));
    }
  }, []);

  return { language, setLanguage };
}
