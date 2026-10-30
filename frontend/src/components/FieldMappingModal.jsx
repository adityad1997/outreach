import { useMemo, useState } from 'react';
import {
  TARGET,
  TARGET_OPTIONS,
  FIXED_CUSTOM_TARGETS,
  autoMap,
  validateMapping,
  applyMapping,
  resolvedCustomKey,
  friendlyLabel,
} from '../utils/leadImportMapping';

/**
 * Map Your Fields modal — CSV/paste column → Quickly field mapping.
 *
 * props:
 *  - headers, rows, samples
 *  - onBack()
 *  - onContinue(payload)  // mapped bulk-add array
 */
export default function FieldMappingModal({ headers, rows, samples, onBack, onContinue }) {
  const initial = useMemo(() => autoMap(headers), [headers]);
  const [mapping, setMapping] = useState(initial.mapping);
  const [autoMapped] = useState(initial.autoMapped);
  const [error, setError] = useState(null);

  const setTarget = (header, target) => {
    setMapping(prev => ({ ...prev, [header]: target }));
    setError(null);
  };

  const handleContinue = () => {
    const v = validateMapping(headers, mapping);
    if (!v.ok) {
      setError(v.error);
      return;
    }
    try {
      const payload = applyMapping(rows, headers, mapping);
      if (!payload.length) {
        setError('No valid emails found after mapping');
        return;
      }
      onContinue(payload);
    } catch (e) {
      setError(e.message || 'Mapping failed');
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div
        className="bg-white rounded-xl shadow-lg w-full max-w-3xl max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b">
          <h2 className="text-lg font-semibold text-gray-900">Map Your Fields</h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Review and configure how your CSV columns map to Quickly fields. Unmapped columns are skipped.
          </p>
        </div>

        <div className="flex-1 overflow-auto px-6 py-3">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide border-b">
                <th className="py-2 pr-3 w-[28%]">CSV Column</th>
                <th className="py-2 pr-3 w-[8%]" />
                <th className="py-2 pr-3 w-[32%]">Maps To Quickly Field</th>
                <th className="py-2 w-[32%]">Preview</th>
              </tr>
            </thead>
            <tbody>
              {headers.map(header => {
                const target = mapping[header] || TARGET.SKIP;
                const isAuto = autoMapped[header] && target !== TARGET.SKIP;
                const preview = (samples && samples[header]) || [];
                const customKey = resolvedCustomKey(header);
                return (
                  <tr key={header} className="border-b border-gray-100 align-top">
                    <td className="py-3 pr-3">
                      <div className="font-medium text-gray-800 break-all">{header}</div>
                      {isAuto && (
                        <span className="inline-block mt-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">
                          Auto Mapped
                        </span>
                      )}
                    </td>
                    <td className="py-3 pr-3 text-gray-300 text-lg leading-none">→</td>
                    <td className="py-3 pr-3">
                      <select
                        className="w-full border border-gray-300 rounded-lg px-2.5 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-teal-300"
                        value={target}
                        onChange={e => setTarget(header, e.target.value)}
                      >
                        {TARGET_OPTIONS.map(opt => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                            {opt.value === TARGET.CUSTOM ? ` → {{${customKey}}}` : ''}
                          </option>
                        ))}
                      </select>
                      {target === TARGET.CUSTOM && (
                        <p className="mt-1 text-xs text-gray-500">
                          Template: <code className="bg-gray-100 px-1 rounded">{`{{${customKey}}}`}</code>
                          {customKey !== header ? (
                            <span className="text-gray-400"> (from “{header}”)</span>
                          ) : null}
                        </p>
                      )}
                      {FIXED_CUSTOM_TARGETS.has(target) && (
                        <p className="mt-1 text-xs text-gray-500">
                          Stores as <code className="bg-gray-100 px-1 rounded">{`{{${target}}}`}</code>
                          {' '}({friendlyLabel(target)})
                        </p>
                      )}
                    </td>
                    <td className="py-3">
                      <div className="flex flex-wrap gap-1">
                        {preview.length === 0 && (
                          <span className="text-xs text-gray-400">No samples</span>
                        )}
                        {preview.map((v, i) => (
                          <span
                            key={i}
                            className="inline-block max-w-[9rem] truncate text-xs bg-sky-50 text-sky-800 border border-sky-100 rounded-full px-2 py-0.5"
                            title={v}
                          >
                            {v.length > 18 ? `${v.slice(0, 16)}…` : v}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {headers.length === 0 && (
            <p className="text-sm text-gray-500 py-8 text-center">No mappable columns found.</p>
          )}
        </div>

        {error && (
          <div className="px-6 py-2 text-sm text-red-600 border-t border-red-100 bg-red-50">{error}</div>
        )}

        <div className="px-6 py-4 border-t flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={onBack}
            className="px-4 py-2 text-sm font-medium text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50"
          >
            Back
          </button>
          <button
            type="button"
            onClick={handleContinue}
            className="px-5 py-2 text-sm font-medium text-white bg-teal-600 rounded-lg hover:bg-teal-700"
          >
            Import Leads
          </button>
        </div>
      </div>
    </div>
  );
}
