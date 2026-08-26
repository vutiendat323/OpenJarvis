import { describe, expect, it, vi } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  CodexPetSpeechBubble,
  formatSpeechText,
} from './CodexPetSpeechBubble';

describe('formatSpeechText', () => {
  it('returns text as-is when within maxChars', () => {
    expect(formatSpeechText('Hello Jarvis', 20)).toBe('Hello Jarvis');
  });

  it('truncates text with ellipsis when exceeding maxChars', () => {
    expect(formatSpeechText('Hello Jarvis, how are you today?', 12)).toBe('Hello Jar...');
  });

  it('returns empty string for empty or whitespace text', () => {
    expect(formatSpeechText('', 10)).toBe('');
    expect(formatSpeechText('   ', 10)).toBe('');
  });
});

describe('CodexPetSpeechBubble', () => {
  it('renders null when not visible and no active voice status or text', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {})
    );
    expect(html).toBe('');
  });

  it('renders text when text is provided', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {
        text: 'I am ready to assist you.',
      })
    );
    expect(html).toContain('I am ready to assist you.');
    expect(html).toContain('data-testid="codex-pet-speech-bubble"');
  });

  it('renders typing indicator when voiceStatus is speaking and text is empty', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {
        voiceStatus: 'speaking',
      })
    );
    expect(html).toContain('data-testid="codex-pet-speech-bubble"');
    expect(html).toContain('data-testid="speech-bubble-typing"');
  });

  it('renders typing indicator when typing is explicitly true', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {
        typing: true,
      })
    );
    expect(html).toContain('data-testid="speech-bubble-typing"');
  });

  it('does not render when visible is explicitly false even if text is present', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {
        visible: false,
        text: 'Hidden message',
      })
    );
    expect(html).toBe('');
  });

  it('truncates long text when maxChars is provided', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {
        text: 'This is a very long speech bubble message that should be truncated.',
        maxChars: 20,
      })
    );
    expect(html).toContain('This is a very lo...');
    expect(html).not.toContain('truncated.');
  });

  it('applies position alignment classes for top-left and top-right', () => {
    const htmlLeft = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {
        text: 'Left bubble',
        position: 'top-left',
      })
    );
    expect(htmlLeft).toContain('data-position="top-left"');

    const htmlRight = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {
        text: 'Right bubble',
        position: 'top-right',
      })
    );
    expect(htmlRight).toContain('data-position="top-right"');
  });

  it('supports custom className and role attribute', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {
        text: 'Custom styled bubble',
        className: 'custom-bubble-class',
        role: 'assistant',
      })
    );
    expect(html).toContain('custom-bubble-class');
    expect(html).toContain('data-role="assistant"');
  });
});
