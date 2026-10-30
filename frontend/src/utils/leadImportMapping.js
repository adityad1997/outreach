/** Client-side CSV/TSV parse + column mapping for campaign lead import. */

export const RESERVED_HEADERS = new Set([
  'status',
  'interest',
  'email_verification_status',
]);

/** Mapping target kinds (values stored in the mapping map). */
export const TARGET = {
  EMAIL: 'email',
  FIRST_NAME: 'first_name',
  LAST_NAME: 'last_name',
  NAME: 'name',
  COMPANY: 'company',
  WEBSITE: 'website',
  LINKEDIN: 'linkedin',
  CITY: 'city',
  CUSTOM: 'custom',
  SKIP: 'skip',
};

export const TARGET_OPTIONS = [
  { value: TARGET.EMAIL, label: 'Email' },
  { value: TARGET.FIRST_NAME, label: 'First Name' },
  { value: TARGET.LAST_NAME, label: 'Last Name' },
  { value: TARGET.NAME, label: 'Name' },
  { value: TARGET.COMPANY, label: 'Company Name' },
  { value: TARGET.WEBSITE, label: 'Website' },
  { value: TARGET.LINKEDIN, label: 'LinkedIn Profile' },
  { value: TARGET.CITY, label: 'Location (city)' },
  { value: TARGET.CUSTOM, label: 'Custom field' },
  { value: TARGET.SKIP, label: 'Skip' },
];

/** Targets that store into custom_data under a fixed key (= target value). */
export const FIXED_CUSTOM_TARGETS = new Set([
  TARGET.FIRST_NAME,
  TARGET.LAST_NAME,
  TARGET.COMPANY,
  TARGET.WEBSITE,
  TARGET.LINKEDIN,
  TARGET.CITY,
]);

const EMAIL_ALIASES = new Set(['email', 'e-mail', 'email address', 'email_address', 'e_mail']);
const FIRST_ALIASES = new Set(['first name', 'first_name', 'firstname', 'first']);
const LAST_ALIASES = new Set(['last name', 'last_name', 'lastname', 'last', 'surname']);
const NAME_ALIASES = new Set(['name', 'full name', 'full_name', 'fullname']);
const COMPANY_ALIASES = new Set(['company', 'company name', 'company_name', 'organization', 'org']);
const WEBSITE_ALIASES = new Set(['website', 'web', 'url', 'site', 'company website', 'company_website']);
const LINKEDIN_ALIASES = new Set([
  'linkedin',
  'linkedin profile',
  'linkedin_profile',
  'linkedin url',
  'linkedin_url',
  'company linkedin url',
  'company_linkedin_url',
  'company linkedin',
  'company_linkedin',
]);
const CITY_ALIASES = new Set(['city', 'location', 'town']);

/**
 * Convert a header to a valid \\w+ template key when needed.
 * Keeps the header as-is when it already matches /^\\w+$/.
 */
export function toSnakeCase(header) {
  const raw = String(header || '').trim();
  if (!raw) return 'field';
  if (/^\w+$/.test(raw)) return raw;
  let s = raw
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[^a-zA-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_+/g, '_')
    .toLowerCase();
  if (!s) s = 'field';
  if (/^\d/.test(s)) s = `f_${s}`;
  return s;
}

/** Friendly label for a key (First Name for first_name). */
export function friendlyLabel(key) {
  const presets = {
    name: 'Name',
    email: 'Email',
    first_name: 'First Name',
    last_name: 'Last Name',
    company: 'Company Name',
    website: 'Website',
    linkedin: 'LinkedIn Profile',
    city: 'Location (city)',
    unsubscribe_link: 'Unsubscribe Link',
  };
  if (presets[key]) return presets[key];
  return String(key || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

export function resolvedCustomKey(header) {
  return toSnakeCase(header);
}

function normalizeHeaderKey(h) {
  return String(h || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ');
}

function isReservedHeader(h) {
  const spaced = normalizeHeaderKey(h);
  const underscored = spaced.replace(/ /g, '_');
  return RESERVED_HEADERS.has(spaced) || RESERVED_HEADERS.has(underscored);
}

/**
 * Auto-map headers. Unrecognised → skip.
 * Returns { [header]: TARGET.* } and which were auto-mapped.
 */
export function autoMap(headers) {
  const mapping = {};
  const autoMapped = {};
  const used = new Set();

  const tryAssign = (header, target) => {
    if (used.has(target) && target !== TARGET.SKIP && target !== TARGET.CUSTOM) return false;
    mapping[header] = target;
    autoMapped[header] = true;
    if (target !== TARGET.SKIP && target !== TARGET.CUSTOM) used.add(target);
    return true;
  };

  for (const header of headers) {
    if (isReservedHeader(header)) {
      mapping[header] = TARGET.SKIP;
      continue;
    }
    const n = normalizeHeaderKey(header);
    if (EMAIL_ALIASES.has(n) && tryAssign(header, TARGET.EMAIL)) continue;
    if (FIRST_ALIASES.has(n) && tryAssign(header, TARGET.FIRST_NAME)) continue;
    if (LAST_ALIASES.has(n) && tryAssign(header, TARGET.LAST_NAME)) continue;
    if (NAME_ALIASES.has(n) && tryAssign(header, TARGET.NAME)) continue;
    if (COMPANY_ALIASES.has(n) && tryAssign(header, TARGET.COMPANY)) continue;
    if (WEBSITE_ALIASES.has(n) && tryAssign(header, TARGET.WEBSITE)) continue;
    if (LINKEDIN_ALIASES.has(n) && tryAssign(header, TARGET.LINKEDIN)) continue;
    if (CITY_ALIASES.has(n) && tryAssign(header, TARGET.CITY)) continue;
    mapping[header] = TARGET.SKIP;
    autoMapped[header] = false;
  }
  return { mapping, autoMapped };
}

/** Split a CSV/TSV line respecting quoted fields. */
function splitCsvLine(line, delimiter) {
  const out = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

function detectDelimiter(text) {
  const sample = text.slice(0, 4096);
  const firstLine = sample.split(/\r?\n/).find(l => l.trim()) || '';
  const tabs = (firstLine.match(/\t/g) || []).length;
  const commas = (firstLine.match(/,/g) || []).length;
  const semis = (firstLine.match(/;/g) || []).length;
  if (tabs >= commas && tabs >= semis && tabs > 0) return '\t';
  if (semis > commas) return ';';
  return ',';
}

/**
 * Parse CSV/TSV text into { headers, rows, samples }.
 * `rows` are objects keyed by original header strings.
 * Reserved headers are excluded from mappable headers.
 */
export function parseDelimitedText(text) {
  const cleaned = String(text || '').replace(/^\uFEFF/, '');
  const lines = cleaned.split(/\r?\n/).filter(l => l.trim().length > 0);
  if (!lines.length) {
    return { headers: [], rows: [], samples: {}, error: 'File appears empty' };
  }

  const delimiter = detectDelimiter(cleaned);
  const rawHeaders = splitCsvLine(lines[0], delimiter).map(h => h.trim());
  if (!rawHeaders.some(Boolean)) {
    return { headers: [], rows: [], samples: {}, error: 'No header row found' };
  }

  // Deduplicate empty / duplicate headers
  const seen = new Map();
  const headers = rawHeaders.map((h, i) => {
    let name = h || `column_${i + 1}`;
    if (seen.has(name)) {
      const n = seen.get(name) + 1;
      seen.set(name, n);
      name = `${name}_${n}`;
    } else {
      seen.set(name, 1);
    }
    return name;
  });

  const rows = [];
  for (let li = 1; li < lines.length; li++) {
    const parts = splitCsvLine(lines[li], delimiter);
    const row = {};
    let any = false;
    headers.forEach((h, i) => {
      const v = (parts[i] ?? '').trim();
      row[h] = v;
      if (v) any = true;
    });
    if (any) rows.push(row);
  }

  const samples = {};
  for (const h of headers) {
    const vals = [];
    for (const row of rows) {
      if (vals.length >= 3) break;
      const v = row[h];
      if (v) vals.push(v);
    }
    samples[h] = vals;
  }

  const mappableHeaders = headers.filter(h => !isReservedHeader(h));

  return { headers: mappableHeaders, allHeaders: headers, rows, samples, delimiter, error: null };
}

/**
 * Validate mapping. Returns { ok, error }.
 */
export function validateMapping(headers, mapping) {
  const targets = headers.map(h => mapping[h] || TARGET.SKIP);
  const emails = targets.filter(t => t === TARGET.EMAIL);
  if (emails.length === 0) {
    return { ok: false, error: 'Map exactly one column to Email' };
  }
  if (emails.length > 1) {
    return { ok: false, error: 'Only one column can map to Email' };
  }
  const counts = {};
  for (const t of targets) {
    if (t === TARGET.SKIP || t === TARGET.CUSTOM) continue;
    counts[t] = (counts[t] || 0) + 1;
    if (counts[t] > 1) {
      return { ok: false, error: `Duplicate mapping: ${t.replace(/_/g, ' ')}` };
    }
  }
  return { ok: true, error: null };
}

/**
 * Apply mapping to parsed rows → bulk-add payload objects.
 */
export function applyMapping(rows, headers, mapping) {
  const { ok, error } = validateMapping(headers, mapping);
  if (!ok) throw new Error(error);

  const emailHeader = headers.find(h => mapping[h] === TARGET.EMAIL);
  const nameHeader = headers.find(h => mapping[h] === TARGET.NAME);
  const firstHeader = headers.find(h => mapping[h] === TARGET.FIRST_NAME);
  const lastHeader = headers.find(h => mapping[h] === TARGET.LAST_NAME);
  const companyHeader = headers.find(h => mapping[h] === TARGET.COMPANY);
  const websiteHeader = headers.find(h => mapping[h] === TARGET.WEBSITE);
  const linkedinHeader = headers.find(h => mapping[h] === TARGET.LINKEDIN);
  const cityHeader = headers.find(h => mapping[h] === TARGET.CITY);
  const customHeaders = headers.filter(h => mapping[h] === TARGET.CUSTOM);

  const payload = [];
  for (const row of rows) {
    const email = String(row[emailHeader] || '').trim().toLowerCase();
    if (!email || !email.includes('@')) continue;

    const first = firstHeader ? String(row[firstHeader] || '').trim() : '';
    const last = lastHeader ? String(row[lastHeader] || '').trim() : '';
    let name = nameHeader ? String(row[nameHeader] || '').trim() : '';
    if (!name && (first || last)) {
      name = [first, last].filter(Boolean).join(' ');
    }

    const custom_data = {};
    if (first) custom_data.first_name = first;
    if (last) custom_data.last_name = last;
    const company = companyHeader ? String(row[companyHeader] || '').trim() : '';
    const website = websiteHeader ? String(row[websiteHeader] || '').trim() : '';
    const linkedin = linkedinHeader ? String(row[linkedinHeader] || '').trim() : '';
    const city = cityHeader ? String(row[cityHeader] || '').trim() : '';
    if (company) custom_data.company = company;
    if (website) custom_data.website = website;
    if (linkedin) custom_data.linkedin = linkedin;
    if (city) custom_data.city = city;
    for (const h of customHeaders) {
      const v = String(row[h] || '').trim();
      if (!v) continue;
      custom_data[resolvedCustomKey(h)] = v;
    }

    payload.push({
      email,
      ...(name ? { name } : {}),
      ...(Object.keys(custom_data).length ? { custom_data } : {}),
    });
  }
  return payload;
}

/**
 * Parse paste/bulk text that may be emails-only or tabular with headers.
 * Returns either { mode: 'emails', emails } or { mode: 'table', ...parseDelimitedText }.
 */
export function parsePasteOrCsv(text) {
  const cleaned = String(text || '').replace(/^\uFEFF/, '');
  const nonEmpty = cleaned.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  if (!nonEmpty.length) {
    return { mode: 'empty', error: 'No data entered' };
  }

  const first = nonEmpty[0];
  const hasDelim = first.includes('\t') || first.includes(',') || first.includes(';');
  if (hasDelim) {
    const parsed = parseDelimitedText(cleaned);
    if (parsed.error) return { mode: 'empty', error: parsed.error };
    if (!parsed.headers.length && !parsed.rows.length) {
      return { mode: 'empty', error: 'No columns found' };
    }
    // If no mappable headers but we have rows with email-like content, fall through
    if (parsed.headers.length >= 1) {
      return { mode: 'table', ...parsed };
    }
  }

  // Emails only (comma or newline)
  const emails = cleaned.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
  return { mode: 'emails', emails };
}
