(function attachKhadamatiI18n(root) {
  'use strict';

  const LANGUAGE_ORDER = Object.freeze(['ar', 'en', 'hi', 'bn', 'ur']);
  const LANGUAGE_META = Object.freeze({
    ar: Object.freeze({ code: 'ar', name: 'العربية', short: 'ع', dir: 'rtl', locale: 'ar-OM', hint: 'عرض التطبيق بالعربية' }),
    en: Object.freeze({ code: 'en', name: 'English', short: 'EN', dir: 'ltr', locale: 'en-OM', hint: 'Show the app in English' }),
    hi: Object.freeze({ code: 'hi', name: 'हिन्दी', short: 'हि', dir: 'ltr', locale: 'hi-IN', hint: 'ऐप को हिन्दी में दिखाएँ' }),
    bn: Object.freeze({ code: 'bn', name: 'বাংলা', short: 'বা', dir: 'ltr', locale: 'bn-BD', hint: 'অ্যাপটি বাংলায় দেখুন' }),
    ur: Object.freeze({ code: 'ur', name: 'اردو', short: 'ار', dir: 'rtl', locale: 'ur-PK', hint: 'ایپ اردو میں دکھائیں' }),
  });
  const TARGET_INDEX = Object.freeze({ hi: 1, bn: 2, ur: 3 });
  const BRAND_COPY = Object.freeze({
    Khadamati: 'Khadamati',
    'Khadamati App': 'Khadamati App',
    WhatsApp: 'WhatsApp',
  });

  function normalizeLanguage(value) {
    const code = String(value || '').trim().toLowerCase().split(/[-_]/)[0];
    return LANGUAGE_META[code] ? code : 'ar';
  }

  function direction(value) {
    return LANGUAGE_META[normalizeLanguage(value)].dir;
  }

  function locale(value) {
    return LANGUAGE_META[normalizeLanguage(value)].locale;
  }

  function normalizePhrase(value) {
    return String(value ?? '').replace(/\s+/g, ' ').trim();
  }

  function escapePattern(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  const exact = { hi: new Map(), bn: new Map(), ur: new Map() };
  const templates = { hi: [], bn: [], ur: [] };
  const rows = Array.isArray(root.KHADAMATI_I18N_ROWS) ? root.KHADAMATI_I18N_ROWS : [];
  rows.forEach(row => {
    const source = normalizePhrase(row?.[0]);
    if (!source) return;
    Object.keys(TARGET_INDEX).forEach(language => {
      const translated = normalizePhrase(row?.[TARGET_INDEX[language]]);
      if (!translated) return;
      if (!/\{\{\d+\}\}/.test(source)) {
        exact[language].set(source, translated);
        return;
      }
      const placeholders = [];
      const marker = /\{\{(\d+)\}\}/g;
      let cursor = 0;
      let expression = '^';
      let match;
      while ((match = marker.exec(source))) {
        expression += escapePattern(source.slice(cursor, match.index));
        expression += '(.+?)';
        placeholders.push(Number(match[1]));
        cursor = match.index + match[0].length;
      }
      expression += `${escapePattern(source.slice(cursor))}$`;
      templates[language].push({ expression: new RegExp(expression, 'u'), placeholders, translated, source });
    });
  });
  Object.values(templates).forEach(items => items.sort((a, b) => b.source.length - a.source.length));
  try { delete root.KHADAMATI_I18N_ROWS; } catch (_) { root.KHADAMATI_I18N_ROWS = undefined; }

  function translate(value, language) {
    const selected = normalizeLanguage(language);
    const original = String(value ?? '');
    if (selected === 'ar' || selected === 'en' || !original) return original;
    const source = normalizePhrase(original);
    if (Object.prototype.hasOwnProperty.call(BRAND_COPY, source)) return BRAND_COPY[source];
    const direct = exact[selected].get(source);
    if (direct) return direct;
    for (const template of templates[selected]) {
      const match = source.match(template.expression);
      if (!match) continue;
      const values = {};
      template.placeholders.forEach((placeholder, index) => { values[placeholder] = match[index + 1]; });
      return template.translated.replace(/\{\{(\d+)\}\}/g, (_, index) => values[Number(index)] ?? '');
    }
    return original;
  }

  function localize(arabic, english, language) {
    const selected = normalizeLanguage(language);
    if (selected === 'ar') return String(arabic ?? english ?? '');
    const source = String(english ?? arabic ?? '');
    return selected === 'en' ? source : translate(source, selected);
  }

  function value(record, language, fallback = '') {
    if (!record || typeof record !== 'object') return fallback;
    const selected = normalizeLanguage(language);
    const direct = record[selected] ?? record[`name_${selected}`] ?? record[`label_${selected}`];
    if (direct != null && direct !== '') return String(direct);
    const english = record.en ?? record.nameEn ?? record.labelEn ?? record.name_en ?? record.label_en;
    const arabic = record.ar ?? record.nameAr ?? record.labelAr ?? record.name_ar ?? record.label_ar;
    if (selected === 'ar') return String(arabic ?? english ?? record.name ?? fallback);
    const source = String(english ?? record.name ?? arabic ?? fallback);
    return selected === 'en' ? source : translate(source, selected);
  }

  function applyDocument(language) {
    if (!root.document?.documentElement) return;
    const selected = normalizeLanguage(language);
    root.document.documentElement.lang = selected;
    root.document.documentElement.dir = direction(selected);
  }

  root.KhadamatiI18n = Object.freeze({
    languages: LANGUAGE_ORDER,
    meta: LANGUAGE_META,
    normalizeLanguage,
    direction,
    locale,
    translate,
    localize,
    value,
    applyDocument,
    catalogSize: rows.length,
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
