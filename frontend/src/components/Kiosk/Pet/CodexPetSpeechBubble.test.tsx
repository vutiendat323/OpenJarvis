import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  CodexPetSpeechBubble,
  formatSpeechText,
  getBubblePath,
  parseSpeechContent,
} from './CodexPetSpeechBubble';

describe('formatSpeechText', () => {
  it('truncates text beyond maxChars with ellipsis', () => {
    expect(formatSpeechText('Ngắn', 90)).toBe('Ngắn');
    expect(formatSpeechText('A'.repeat(100), 90)).toBe('A'.repeat(87) + '...');
  });

  it('returns text as-is when within maxChars', () => {
    expect(formatSpeechText('Hello Jarvis', 20)).toBe('Hello Jarvis');
  });

  it('returns empty string for empty or undefined input', () => {
    expect(formatSpeechText('')).toBe('');
    expect(formatSpeechText(undefined)).toBe('');
    expect(formatSpeechText('   ')).toBe('');
  });
});

describe('CodexPetSpeechBubble component', () => {
  it('renders null when not visible and no active voice status or text', () => {
    const html = renderToStaticMarkup(
      React.createElement(CodexPetSpeechBubble, {})
    );
    expect(html).toBe('');
  });

  it('renders typing indicator when speaking without text', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble voiceStatus="speaking" text="" />
    );
    expect(html).toContain('data-testid="speech-bubble-typing"');
  });

  it('renders typing indicator when typing is explicitly true', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble typing={true} />
    );
    expect(html).toContain('data-testid="speech-bubble-typing"');
  });

  it('does not render when visible is explicitly false even if text is present', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble visible={false} text="Hidden message" />
    );
    expect(html).toBe('');
  });

  it('renders speech text with proper wrapping container classes', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble voiceStatus="speaking" text="Chào bạn nhé!" />
    );
    expect(html).toContain('w-max');
    expect(html).toContain('min-w-');
    expect(html).toContain('break-words');
    expect(html).toContain('Chào bạn nhé!');
  });

  it('defaults to maxChars=140 truncation in component', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble voiceStatus="speaking" text={'B'.repeat(160)} />
    );
    expect(html).toContain('B'.repeat(137) + '...');
  });

  it('applies position alignment classes for top-left and top-right', () => {
    const htmlLeft = renderToStaticMarkup(
      <CodexPetSpeechBubble text="Left bubble" position="top-left" />
    );
    expect(htmlLeft).toContain('data-position="top-left"');

    const htmlRight = renderToStaticMarkup(
      <CodexPetSpeechBubble text="Right bubble" position="top-right" />
    );
    expect(htmlRight).toContain('data-position="top-right"');
  });

  it('supports custom className and role attribute', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble
        text="Custom styled bubble"
        className="custom-bubble-class"
        role="assistant"
      />
    );
    expect(html).toContain('custom-bubble-class');
    expect(html).toContain('data-role="assistant"');
  });

  it('renders greeting as bold title and inquiry as body matching sample image', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble text="Hello! What would you like to order today?" />
    );
    expect(html).toContain('Hello!');
    expect(html).toContain('What would you like to order today?');
    expect(html).toContain('font-bold');
    expect(html).toContain('data-testid="speech-bubble-tail"');
  });

  it('renders seamless SVG border with glowing cyan stroke, transparent background, and integrated tail', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble text="Xin chào bạn!" position="top" />
    );
    expect(html).toContain('data-testid="speech-bubble-border-svg"');
    expect(html).toContain('data-testid="speech-bubble-tail"');
    expect(html).toContain('stroke="#22d3ee"');
    expect(html).toContain('fill="none"');
    expect(html).toContain('background:transparent');
  });

  it('uses left-aligned tail path when position is top-right', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble text="Sample" position="top-right" />
    );
    expect(html).toContain('data-testid="speech-bubble-tail"');
    expect(html).toContain('fill="none"');
  });

  it('defaults to position="left" (Bubble on left, Robot on right matching sample photo)', () => {
    const html = renderToStaticMarkup(
      <CodexPetSpeechBubble text="Hello" />
    );
    expect(html).toContain('data-position="left"');
    expect(html).toContain('right-full');
    expect(html).toContain('-mr-7');
  });
});

describe('getBubblePath', () => {
  it('generates a closed SVG path with tail at bottom-right for right position', () => {
    const path = getBubblePath(260, 76, 24, 'right');
    expect(path).toContain('M 24 0');
    expect(path).toContain('Z');
    expect(path).toContain('267 84');
  });

  it('generates a closed SVG path with tail at bottom-left for left position', () => {
    const path = getBubblePath(260, 76, 24, 'left');
    expect(path).toContain('M 24 0');
    expect(path).toContain('Z');
    expect(path).toContain('-7 84');
  });
});

describe('parseSpeechContent', () => {
  it('splits greeting and question into title and body', () => {
    expect(parseSpeechContent('Hello! What would you like to order today?')).toEqual({
      title: 'Hello!',
      body: 'What would you like to order today?',
    });
    expect(parseSpeechContent('Xin chào! Tôi có thể giúp gì cho bạn?')).toEqual({
      title: 'Xin chào!',
      body: 'Tôi có thể giúp gì cho bạn?',
    });
    expect(parseSpeechContent('Xin chào bạn, mình có thể giúp gì ạ?')).toEqual({
      title: 'Xin chào bạn,',
      body: 'mình có thể giúp gì ạ?',
    });
  });

  it('splits explicit newlines into title and body', () => {
    expect(parseSpeechContent('Hello!\nWhat would you like to order today?')).toEqual({
      title: 'Hello!',
      body: 'What would you like to order today?',
    });
  });

  it('handles single sentences without title', () => {
    expect(parseSpeechContent('Chào bạn nhé!')).toEqual({
      body: 'Chào bạn nhé!',
    });
    expect(parseSpeechContent('')).toEqual({
      body: '',
    });
  });
});
