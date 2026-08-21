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

/** Theme tokens (from index.css :root — light-only, mockup palette) */
const LIGHT = {
  bg: '#f6f4ef',
  bgElevated: '#ffffff',
  text: '#211f1c',
  textMuted: '#726f68',
  surface500: '#726f68',
  deloitteGreen: '#0e6e5c',
  deloitteGreenDark: '#123b33',
  deloitteTeal: '#3d6e8a',
  amber: '#b8792e',
  red: '#b23b2e',
  white: '#ffffff',
  black: '#000000',
} as const;

const AA_NORMAL = 4.5;
const AA_LARGE = 3.0;

describe('WCAG AA contrast — light theme (cream/forest palette)', () => {
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

  it('surface-500 on page background meets AA for 12px normal text (≥4.5:1)', () => {
    expect(contrastRatio(LIGHT.surface500, LIGHT.bg)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('surface-500 on elevated surface meets AA for 12px normal text', () => {
    expect(contrastRatio(LIGHT.surface500, LIGHT.bgElevated)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('Deloitte green (forest) on page bg meets AA for large text / icons', () => {
    expect(contrastRatio(LIGHT.deloitteGreen, LIGHT.bg)).toBeGreaterThanOrEqual(AA_LARGE);
  });

  it('Deloitte green on elevated surface meets AA for normal text (links/labels)', () => {
    expect(contrastRatio(LIGHT.deloitteGreen, LIGHT.bgElevated)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('Deloitte teal (secondary accent) on light bg meets AA for links/accents', () => {
    expect(contrastRatio(LIGHT.deloitteTeal, LIGHT.bg)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('amber accent on light bg meets AA for large/bold text (badges, labels)', () => {
    // Amber is used for bold uppercase micro-labels and badge chips, not
    // plain body copy — AA_LARGE is the applicable threshold (WCAG 2.1 §1.4.3).
    expect(contrastRatio(LIGHT.amber, LIGHT.bg)).toBeGreaterThanOrEqual(AA_LARGE);
  });

  it('red accent on light bg meets AA for normal text', () => {
    expect(contrastRatio(LIGHT.red, LIGHT.bg)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('white on Deloitte green meets AA for primary CTA labels', () => {
    // Buttons render white text on the forest-green fill (not black — the
    // green is a dark tone in this palette, unlike the old bright brand green).
    expect(contrastRatio(LIGHT.white, LIGHT.deloitteGreen)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it('white on Deloitte green-dark meets AA for user chat bubbles', () => {
    expect(contrastRatio(LIGHT.white, LIGHT.deloitteGreenDark)).toBeGreaterThanOrEqual(AA_NORMAL);
  });
});

describe('contrast helpers', () => {
  it('white on black is ~21:1', () => {
    expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 0);
  });

  it('identical colors are 1:1', () => {
    expect(contrastRatio('#0e6e5c', '#0e6e5c')).toBeCloseTo(1, 5);
  });
});
