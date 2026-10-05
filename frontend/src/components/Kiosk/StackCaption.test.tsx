import React from 'react';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { StackCaption, getStackCaptionLines } from './StackCaption';

describe('getStackCaptionLines', () => {
  it('handles empty or whitespace-only text', () => {
    expect(getStackCaptionLines('')).toEqual({ top: '', mid: '', bottom: '' });
    expect(getStackCaptionLines('   ')).toEqual({ top: '', mid: '', bottom: '' });
  });

  it('handles single word', () => {
    expect(getStackCaptionLines('Chào')).toEqual({ top: '', mid: '', bottom: 'Chào' });
  });

  it('handles two words', () => {
    expect(getStackCaptionLines('Chào bạn')).toEqual({ top: '', mid: 'Chào', bottom: 'bạn' });
  });

  it('handles three words', () => {
    expect(getStackCaptionLines('Chào bạn nhé')).toEqual({
      top: 'Chào',
      mid: 'bạn',
      bottom: 'nhé',
    });
  });

  it('handles four words with 1 word per line', () => {
    expect(getStackCaptionLines('xem mẫu phù hợp')).toEqual({
      top: 'mẫu',
      mid: 'phù',
      bottom: 'hợp',
    });
  });

  it('handles longer text keeping latest 3 single words', () => {
    expect(getStackCaptionLines('Tôi muốn xem mẫu phù hợp')).toEqual({
      top: 'mẫu',
      mid: 'phù',
      bottom: 'hợp',
    });
  });
});

describe('StackCaption component', () => {
  it('renders nothing when text is empty', () => {
    const markup = renderToStaticMarkup(<StackCaption text="" />);
    expect(markup).toBe('');
  });

  it('renders sr-only accessible text alongside stack spans', () => {
    const markup = renderToStaticMarkup(<StackCaption text="xem mẫu phù hợp" />);
    expect(markup).toContain('xem mẫu phù hợp');
    expect(markup).toContain('sr-only');
    expect(markup).toContain('mẫu');
    expect(markup).toContain('phù');
    expect(markup).toContain('hợp');
  });

  it('applies fading class when isFading is true', () => {
    const markup = renderToStaticMarkup(<StackCaption text="Chào bạn" isFading />);
    expect(markup).toContain('opacity-0');
  });
});
