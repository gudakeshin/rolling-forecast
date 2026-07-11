/**
 * WCAG AA contrast audit for theme tokens.
 *
 * Relative luminance / contrast ratio per WCAG 2.1 §1.4.3.
 * AA requires ≥4.5:1 for normal text and ≥3:1 for large text / UI chrome.
 */

import { describe, expect, it } from 'vitest';

type RGB = [number, number, number];

function hexToRgb(hex: string): RGB {
  const h = hex.replace('#', '').trim();
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrastRatio(fg: string, bg: string): number {
  const L1 = relativeLuminance(fg);
  const L2 = relativeLuminance(bg);
  const lighter = Math.max(L1, L2);
  const darker = Math.min(L1, L2);
  return (lighter + 0.05) / (darker + 0.05);
}

/** Dark theme tokens (from index.css data-theme=dark) */
const DARK = {
  bg: '#0d0d0d',
  bgElevated: '#1a1a1a',
  text: '#f5f5f5',
  textMuted: '#b0b2b4',
  surface500: '#75787b',
  deloitteGreen: '#86BC25',
  deloitteTeal: '#0076A8',
  white: '#ffffff',
  black: '#000000',
} as const;

/** Light theme tokens */
const LIGHT = {
  bg: '#f4f5f7',
  bgElevated: '#ffffff',
  text: '#1a1a1a',
  textMuted: '#53565a',
  surface500: '#97999b',
  deloitteGreen: '#86BC25',
  deloitteTeal: '#0076A8',
  white: '#ffffff',
  black: '#000000',
} as const;

const AA_NORMAL = 4.5;
const AA_LARGE = 3.0;

describe('WCAG AA contrast — dark theme', () => {
  it('primary text on page background meets AA', () => {
    expect(contrastRatio(DARK.text, DARK.bg)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('primary text on elevated surface meets AA', () => {
    expect(contrastRatio(DARK.text, DARK.bgElevated)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('muted text on page background meets AA for large/UI (≥3:1)', () => {
    // surface-400 (#b0b2b4) is used for secondary labels at ≥12px
    expect(contrastRatio(DARK.textMuted, DARK.bg)).toBeGreaterThanOrEqual(AA_LARGE);
  });

  it('muted text on elevated surface meets AA for large/UI', () => {
    expect(contrastRatio(DARK.textMuted, DARK.bgElevated)).toBeGreaterThanOrEqual(AA_LARGE);
  });

  it('Deloitte green on dark bg meets AA for large text / icons', () => {
    expect(contrastRatio(DARK.deloitteGreen, DARK.bg)).toBeGreaterThanOrEqual(AA_LARGE);
  });

  it('white on Deloitte teal meets AA for button labels', () => {
    expect(contrastRatio(DARK.white, DARK.deloitteTeal)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('black on Deloitte green meets AA for primary CTA labels', () => {
    expect(contrastRatio(DARK.black, DARK.deloitteGreen)).toBeGreaterThanOrEqual(AA_NORMAL);
  });
});

describe('WCAG AA contrast — light theme', () => {
  it('primary text on page background meets AA', () => {
    expect(contrastRatio(LIGHT.text, LIGHT.bg)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('primary text on elevated surface meets AA', () => {
    expect(contrastRatio(LIGHT.text, LIGHT.bgElevated)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('muted text on page background meets AA for large/UI', () => {
    expect(contrastRatio(LIGHT.textMuted, LIGHT.bg)).toBeGreaterThanOrEqual(AA_LARGE);
  });

  it('muted text on elevated surface meets AA for large/UI', () => {
    expect(contrastRatio(LIGHT.textMuted, LIGHT.bgElevated)).toBeGreaterThanOrEqual(AA_LARGE);
  });

  it('Deloitte teal on light bg meets AA for links/accents', () => {
    expect(contrastRatio(LIGHT.deloitteTeal, LIGHT.bg)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('black on Deloitte green meets AA for primary CTA labels', () => {
    expect(contrastRatio(LIGHT.black, LIGHT.deloitteGreen)).toBeGreaterThanOrEqual(AA_NORMAL);
  });
});

describe('contrast helpers', () => {
  it('white on black is ~21:1', () => {
    expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 0);
  });

  it('identical colors are 1:1', () => {
    expect(contrastRatio('#86BC25', '#86BC25')).toBeCloseTo(1, 5);
  });
});
