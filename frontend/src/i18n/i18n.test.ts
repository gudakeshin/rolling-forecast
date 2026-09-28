import { describe, expect, it } from 'vitest';
import { setLocale, t } from './index';

describe('i18n', () => {
  it('switches locales and interpolates vars', () => {
    setLocale('en');
    expect(t('app.title')).toBe('Rolling Forecast');
    expect(t('sidebar.messages', { count: 3 })).toBe('3 messages');
    setLocale('es');
    expect(t('app.title')).toBe('Pronóstico Continuo');
    expect(t('sidebar.messages', { count: 3 })).toBe('3 mensajes');
    setLocale('en');
  });
});
