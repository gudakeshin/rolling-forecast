import { useEffect, useMemo, useRef, useState } from 'react';
import { Search } from 'lucide-react';
import { useCommandPaletteStore } from '../../store/commandPaletteStore';
import { useNavItems } from '../../hooks/useNavItems';
import { usePanelStore } from '../../store/panelStore';
import { useVersionStore } from '../../store/versionStore';
import { useComposerStore } from '../../store/composerStore';
import { searchLineItems, type LineItemSearchResult } from '../../api/dashboard';
import { useI18n } from '../../i18n/useI18n';

interface PaletteItem {
  id: string;
  section: string;
  label: string;
  sublabel?: string;
  onSelect: () => void;
}

const LINE_ITEM_SEARCH_MIN_CHARS = 2;
const LINE_ITEM_SEARCH_DEBOUNCE_MS = 200;

/**
 * ⌘K / Ctrl+K command palette — open a panel, switch forecast version, jump
 * to a line item's review status, or focus the chat composer, all from one
 * keyboard-driven search box instead of hunting through the sidebar.
 */
export function CommandPalette() {
  const isOpen = useCommandPaletteStore((s) => s.isOpen);
  const close = useCommandPaletteStore((s) => s.close);
  const { t } = useI18n();
  const navGroups = useNavItems();
  const openPanel = usePanelStore((s) => s.openPanel);
  const versions = useVersionStore((s) => s.versions);
  const activeVersionId = useVersionStore((s) => s.activeVersionId);
  const setActiveVersionId = useVersionStore((s) => s.setActiveVersionId);
  const requestComposerFocus = useComposerStore((s) => s.requestFocus);

  const [query, setQuery] = useState('');
  const [highlighted, setHighlighted] = useState(0);
  const [lineItemResults, setLineItemResults] = useState<LineItemSearchResult[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Global toggle — works from anywhere in the app, not just while the
  // palette (or a particular panel) already has focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        useCommandPaletteStore.getState().toggle();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    setQuery('');
    setHighlighted(0);
    setLineItemResults([]);
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [isOpen]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!isOpen || trimmed.length < LINE_ITEM_SEARCH_MIN_CHARS) {
      setLineItemResults([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      searchLineItems(trimmed, 5)
        .then((res) => {
          if (!cancelled) setLineItemResults(res.items || []);
        })
        .catch(() => {
          if (!cancelled) setLineItemResults([]);
        });
    }, LINE_ITEM_SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, isOpen]);

  const staticItems = useMemo<PaletteItem[]>(() => {
    const items: PaletteItem[] = [];
    for (const group of navGroups) {
      for (const navItem of group.items) {
        if (!navItem.show) continue;
        items.push({
          id: `panel:${navItem.key}`,
          section: t('palette.section.panels'),
          label: t(navItem.labelKey),
          onSelect: () => {
            navItem.onClick();
            close();
          },
        });
      }
    }
    for (const v of versions) {
      items.push({
        id: `version:${v.id}`,
        section: t('palette.section.versions'),
        label: v.label || v.name,
        sublabel: v.id === activeVersionId ? t('palette.currentVersion') : undefined,
        onSelect: () => {
          setActiveVersionId(v.id);
          close();
        },
      });
    }
    items.push({
      id: 'action:focus-composer',
      section: t('palette.section.actions'),
      label: t('palette.focusComposer'),
      onSelect: () => {
        requestComposerFocus();
        close();
      },
    });
    return items;
  }, [navGroups, versions, activeVersionId, t, setActiveVersionId, requestComposerFocus, close]);

  const filteredStatic = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return staticItems;
    return staticItems.filter((i) => i.label.toLowerCase().includes(q));
  }, [staticItems, query]);

  const lineItemItems = useMemo<PaletteItem[]>(
    () =>
      lineItemResults.map((li) => ({
        id: `line-item:${li.id}`,
        section: t('palette.section.lineItems'),
        label: li.name,
        sublabel: li.account_code,
        onSelect: () => {
          if (activeVersionId) {
            openPanel('review_dashboard', { version_id: activeVersionId, focus_line_item_id: li.id });
          }
          close();
        },
      })),
    [lineItemResults, activeVersionId, openPanel, close, t],
  );

  const allItems = useMemo(() => [...filteredStatic, ...lineItemItems], [filteredStatic, lineItemItems]);

  useEffect(() => {
    setHighlighted(0);
  }, [query, lineItemItems.length]);

  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-index="${highlighted}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [highlighted]);

  if (!isOpen) return null;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlighted((h) => Math.min(h + 1, allItems.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlighted((h) => Math.max(h - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      allItems[highlighted]?.onSelect();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  };

  const sections: { title: string; items: { item: PaletteItem; index: number }[] }[] = [];
  allItems.forEach((item, index) => {
    let section = sections.find((s) => s.title === item.section);
    if (!section) {
      section = { title: item.section, items: [] };
      sections.push(section);
    }
    section.items.push({ item, index });
  });

  return (
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- backdrop-click dismiss; Escape already covers keyboard/AT
    <div
      className="fixed inset-0 z-[120] flex items-start justify-center bg-black/30 px-4 pt-[12vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t('palette.trigger')}
        className="w-full max-w-lg rounded-xl bg-surface-800 border border-surface-700/50 shadow-xl shadow-black/40 overflow-hidden"
      >
        <div className="flex items-center gap-2 px-3.5 py-3 border-b border-surface-700/50">
          <Search className="w-4 h-4 text-surface-500 shrink-0" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t('palette.placeholder')}
            className="flex-1 bg-transparent text-sm text-white placeholder:text-surface-500 focus:outline-none"
            aria-label={t('palette.placeholder')}
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-list"
            aria-activedescendant={allItems[highlighted]?.id}
          />
          <kbd className="text-[10px] font-mono text-surface-500 border border-surface-700 rounded px-1.5 py-0.5">
            Esc
          </kbd>
        </div>
        <div id="command-palette-list" ref={listRef} role="listbox" className="max-h-80 overflow-y-auto py-1.5">
          {allItems.length === 0 && (
            <p className="px-3.5 py-6 text-center text-sm text-surface-500">{t('palette.empty')}</p>
          )}
          {sections.map((section) => (
            <div key={section.title} className="mb-1">
              <div className="px-3.5 pt-1.5 pb-1 text-[10px] font-bold text-surface-500 uppercase tracking-wider">
                {section.title}
              </div>
              {section.items.map(({ item, index }) => (
                <button
                  key={item.id}
                  id={item.id}
                  type="button"
                  role="option"
                  aria-selected={index === highlighted}
                  data-index={index}
                  onMouseEnter={() => setHighlighted(index)}
                  onClick={() => item.onSelect()}
                  className={`w-full flex items-center justify-between gap-2 px-3.5 py-2 text-left text-sm transition-colors ${
                    index === highlighted ? 'bg-deloitte-green/12 text-deloitte-green' : 'text-surface-200'
                  }`}
                >
                  <span className="truncate">{item.label}</span>
                  {item.sublabel && (
                    <span className="text-xs text-surface-500 shrink-0">{item.sublabel}</span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
