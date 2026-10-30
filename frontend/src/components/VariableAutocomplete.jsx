import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { friendlyLabel } from '../utils/leadImportMapping';

export const BUILTIN_VARIABLES = [
  { key: 'email', label: 'Email', insert: '{{email}}' },
  { key: 'unsubscribe_link', label: 'Unsubscribe Link', insert: '{{unsubscribe_link}}' },
  { key: 'random', label: 'Random choice', insert: '{{random: a | b}}' },
];

/** Build suggestion list from built-ins + custom keys. */
export function buildVariableSuggestions(customKeys = []) {
  const seen = new Set(BUILTIN_VARIABLES.map(v => v.key));
  const list = [...BUILTIN_VARIABLES];
  for (const k of [...customKeys].sort()) {
    if (!k || seen.has(k)) continue;
    seen.add(k);
    list.push({ key: k, label: friendlyLabel(k), insert: `{{${k}}}` });
  }
  return list;
}

/** Find open `{{` + partial token before caret. Returns { start, query } or null. */
export function findOpenVariableToken(text, caret) {
  const before = text.slice(0, caret);
  const idx = before.lastIndexOf('{{');
  if (idx === -1) return null;
  const afterOpen = before.slice(idx + 2);
  // Closed already or nested braces
  if (afterOpen.includes('}}')) return null;
  if (/[{}]/.test(afterOpen)) return null;
  // Only allow \\w and spaces/colons for random prefix while typing
  if (!/^[\w:|\s]*$/.test(afterOpen)) return null;
  return { start: idx, query: afterOpen.trimStart() };
}

function getCaretViewportCoords(el) {
  if (!el || typeof window === 'undefined') return null;
  const { selectionStart } = el;
  if (selectionStart == null) return null;

  // Mirror technique for textarea/input
  const div = document.createElement('div');
  const style = window.getComputedStyle(el);
  const props = [
    'boxSizing', 'width', 'height', 'overflowX', 'overflowY',
    'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
    'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
    'fontStyle', 'fontVariant', 'fontWeight', 'fontStretch', 'fontSize',
    'fontSizeAdjust', 'lineHeight', 'fontFamily', 'textAlign', 'textTransform',
    'textIndent', 'textDecoration', 'letterSpacing', 'wordSpacing',
    'tabSize', 'whiteSpace', 'wordWrap', 'wordBreak',
  ];
  div.style.position = 'absolute';
  div.style.visibility = 'hidden';
  div.style.whiteSpace = el.tagName === 'INPUT' ? 'pre' : 'pre-wrap';
  div.style.wordWrap = 'break-word';
  props.forEach(prop => {
    div.style[prop] = style[prop];
  });
  div.style.overflow = 'hidden';
  div.textContent = el.value.substring(0, selectionStart);
  const span = document.createElement('span');
  span.textContent = el.value.substring(selectionStart) || '.';
  div.appendChild(span);
  document.body.appendChild(div);
  const elRect = el.getBoundingClientRect();
  const spanRect = span.getBoundingClientRect();
  const divRect = div.getBoundingClientRect();
  document.body.removeChild(div);
  return {
    top: elRect.top + (spanRect.top - divRect.top) - el.scrollTop + 20,
    left: elRect.left + (spanRect.left - divRect.left) - el.scrollLeft,
  };
}

/**
 * Dropdown menu for variable suggestions.
 */
export function VariableSuggestMenu({ items, activeIndex, onPick, onHover, style }) {
  if (!items.length) return null;
  return (
    <ul
      className="fixed z-[80] max-h-56 overflow-y-auto min-w-[16rem] max-w-sm bg-white border border-gray-200 rounded-lg shadow-lg py-1 text-sm"
      style={style}
      role="listbox"
    >
      {items.map((item, i) => (
        <li
          key={item.key}
          role="option"
          aria-selected={i === activeIndex}
          className={`px-3 py-1.5 cursor-pointer flex items-center justify-between gap-3 ${
            i === activeIndex ? 'bg-teal-50 text-teal-900' : 'text-gray-700 hover:bg-gray-50'
          }`}
          onMouseDown={e => {
            e.preventDefault();
            onPick(item);
          }}
          onMouseEnter={() => onHover(i)}
        >
          <span className="truncate">{item.label}</span>
          <code className="shrink-0 text-xs text-gray-500">{item.insert}</code>
        </li>
      ))}
    </ul>
  );
}

/**
 * Hook for plain input/textarea variable autocomplete.
 * Returns { menu, bind } where bind spreads onto the field.
 */
export function usePlainVariableAutocomplete({ value, onChange, customKeys = [] }) {
  const suggestions = useMemo(() => buildVariableSuggestions(customKeys), [customKeys]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [tokenStart, setTokenStart] = useState(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const elRef = useRef(null);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    if (!q) return suggestions;
    return suggestions.filter(
      s =>
        s.key.toLowerCase().includes(q) ||
        s.label.toLowerCase().includes(q) ||
        s.insert.toLowerCase().includes(q),
    );
  }, [suggestions, query]);

  const refreshFromCaret = useCallback(() => {
    const el = elRef.current;
    if (!el) return;
    const caret = el.selectionStart ?? 0;
    const found = findOpenVariableToken(el.value, caret);
    if (!found) {
      setOpen(false);
      return;
    }
    setTokenStart(found.start);
    setQuery(found.query);
    setActiveIndex(0);
    setOpen(true);
    const coords = getCaretViewportCoords(el);
    if (coords) {
      const top = Math.min(coords.top, window.innerHeight - 240);
      const left = Math.min(Math.max(8, coords.left), window.innerWidth - 280);
      setPos({ top, left });
    }
  }, []);

  const insertItem = useCallback(
    item => {
      const el = elRef.current;
      if (!el || tokenStart == null) return;
      const caret = el.selectionStart ?? value.length;
      const before = value.slice(0, tokenStart);
      const after = value.slice(caret);
      const next = before + item.insert + after;
      onChange(next);
      setOpen(false);
      requestAnimationFrame(() => {
        el.focus();
        const pos = before.length + item.insert.length;
        el.setSelectionRange(pos, pos);
      });
    },
    [onChange, tokenStart, value],
  );

  const onKeyDown = e => {
    if (!open) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(i => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      if (filtered[activeIndex]) {
        e.preventDefault();
        insertItem(filtered[activeIndex]);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    }
  };

  const bind = {
    ref: elRef,
    value,
    onChange: e => {
      onChange(e.target.value);
      // defer caret read until after value update
      requestAnimationFrame(refreshFromCaret);
    },
    onKeyUp: refreshFromCaret,
    onClick: refreshFromCaret,
    onSelect: refreshFromCaret,
    onKeyDown,
  };

  const menu = open ? (
    <VariableSuggestMenu
      items={filtered}
      activeIndex={activeIndex}
      onPick={insertItem}
      onHover={setActiveIndex}
      style={{ top: pos.top, left: pos.left }}
    />
  ) : null;

  return { menu, bind, open };
}

/**
 * Attach {{ autocomplete to a ReactQuill instance.
 * Call with quill ref getter and customKeys; returns menu element + handlers for onChange.
 */
export function useQuillVariableAutocomplete({ getQuill, customKeys = [], enabled = true }) {
  const suggestions = useMemo(() => buildVariableSuggestions(customKeys), [customKeys]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [tokenStart, setTokenStart] = useState(null); // index in plain getText()
  const [activeIndex, setActiveIndex] = useState(0);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    if (!q) return suggestions;
    return suggestions.filter(
      s =>
        s.key.toLowerCase().includes(q) ||
        s.label.toLowerCase().includes(q) ||
        s.insert.toLowerCase().includes(q),
    );
  }, [suggestions, query]);

  const refresh = useCallback(() => {
    if (!enabled) {
      setOpen(false);
      return;
    }
    const quill = getQuill?.();
    if (!quill) return;
    const sel = quill.getSelection();
    if (!sel) {
      setOpen(false);
      return;
    }
    const text = quill.getText(0, sel.index);
    const found = findOpenVariableToken(text, text.length);
    if (!found) {
      setOpen(false);
      return;
    }
    setTokenStart(found.start);
    setQuery(found.query);
    setActiveIndex(0);
    setOpen(true);
    try {
      const bounds = quill.getBounds(sel.index);
      const rootRect = quill.root.getBoundingClientRect();
      setPos({
        top: rootRect.top + bounds.top + bounds.height + 4,
        left: Math.min(rootRect.left + bounds.left, window.innerWidth - 280),
      });
    } catch {
      /* ignore */
    }
  }, [enabled, getQuill]);

  const insertItem = useCallback(
    item => {
      const quill = getQuill?.();
      if (!quill || tokenStart == null) return;
      const sel = quill.getSelection(true);
      const caret = sel ? sel.index : quill.getLength();
      const deleteLen = caret - tokenStart;
      if (deleteLen > 0) quill.deleteText(tokenStart, deleteLen, 'user');
      quill.insertText(tokenStart, item.insert, 'user');
      quill.setSelection(tokenStart + item.insert.length, 0, 'user');
      setOpen(false);
    },
    [getQuill, tokenStart],
  );

  useEffect(() => {
    if (!open) return undefined;
    const onKey = e => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        e.stopPropagation();
        setActiveIndex(i => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        e.stopPropagation();
        setActiveIndex(i => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        if (filtered[activeIndex]) {
          e.preventDefault();
          e.stopPropagation();
          insertItem(filtered[activeIndex]);
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open, filtered, activeIndex, insertItem]);

  const menu = open ? (
    <VariableSuggestMenu
      items={filtered}
      activeIndex={activeIndex}
      onPick={insertItem}
      onHover={setActiveIndex}
      style={{ top: pos.top, left: pos.left }}
    />
  ) : null;

  return { menu, refresh, open };
}
