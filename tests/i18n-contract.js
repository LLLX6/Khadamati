const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
require(path.join(root, 'assets', 'scripts', 'khadamati-i18n-data.js'));
const rows = globalThis.KHADAMATI_I18N_ROWS;

function assert(value, message) {
  if (!value) throw new Error(message);
}

assert(Array.isArray(rows) && rows.length >= 4800, 'The static translation catalog is unexpectedly small.');
const seen = new Set();
for (const [index, row] of rows.entries()) {
  assert(Array.isArray(row) && row.length === 4, `Translation row ${index} is malformed.`);
  assert(row.every(value => typeof value === 'string' && value.trim()), `Translation row ${index} contains an empty value.`);
  assert(!seen.has(row[0]), `Duplicate English source phrase: ${row[0]}`);
  seen.add(row[0]);
  const sourceTokens = [...row[0].matchAll(/\{\{\d+\}\}/g)].map(match => match[0]).sort().join('|');
  row.slice(1).forEach((translation, offset) => {
    const translatedTokens = [...translation.matchAll(/\{\{\d+\}\}/g)].map(match => match[0]).sort().join('|');
    const grammaticalSuffix = row[0] === 'Services may span up to {{0}} categor{{1}}.' && translatedTokens === '{{0}}';
    assert(translatedTokens === sourceTokens || grammaticalSuffix, `Placeholder mismatch in row ${index}, target ${offset + 1}.`);
  });
}

require(path.join(root, 'assets', 'scripts', 'khadamati-i18n.js'));
const i18n = globalThis.KhadamatiI18n;
assert(JSON.stringify(i18n.languages) === JSON.stringify(['ar', 'en', 'hi', 'bn', 'ur']), 'Supported-language order is incorrect.');
assert(i18n.direction('ar') === 'rtl' && i18n.direction('ur') === 'rtl', 'Arabic and Urdu must use RTL.');
assert(['en', 'hi', 'bn'].every(language => i18n.direction(language) === 'ltr'), 'English, Hindi, and Bengali must use LTR.');
assert(i18n.locale('ar') === 'ar-OM' && i18n.locale('en') === 'en-OM' && i18n.locale('hi') === 'hi-IN' && i18n.locale('bn') === 'bn-BD' && i18n.locale('ur') === 'ur-PK', 'Locale mapping is incomplete.');
assert(i18n.translate('Choose language', 'hi') === 'भाषा चुनें', 'Hindi sentinel translation is missing.');
assert(i18n.translate('Choose language', 'bn') === 'ভাষা নির্বাচন করুন', 'Bengali sentinel translation is missing.');
assert(i18n.translate('Choose language', 'ur') === 'زبان منتخب کریں', 'Urdu sentinel translation is missing.');
assert(!i18n.translate('Entitlement ends in 12 days.', 'ur').includes('{{'), 'Dynamic translation placeholders were not resolved.');

for (const language of ['ar', 'en', 'hi', 'bn', 'ur']) {
  const name = language === 'ar' ? 'manifest.webmanifest' : `manifest.${language}.webmanifest`;
  const manifest = JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'));
  assert(manifest.lang === language, `${name} has the wrong language.`);
  assert(manifest.dir === (['ar', 'ur'].includes(language) ? 'rtl' : 'ltr'), `${name} has the wrong direction.`);
  assert(manifest.version === '1.3.1', `${name} has the wrong release version.`);
}

const indexSource = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
assert(indexSource.includes("const SUPPORTED_LANGUAGES = I18N.languages"), 'The application is not using the shared language contract.');
assert(indexSource.includes('data-language-picker') && indexSource.includes("if(a==='toggleLang'){languageSheet();return;}"), 'Language buttons do not open the five-language picker.');
assert(indexSource.includes('S.lang=normalizeLang(incoming.lang)'), 'Imported device settings still reject one of the supported languages.');
assert(indexSource.includes("type:'KHADAMATI_LANGUAGE',language:S.lang"), 'The selected language is not synchronized to push notifications.');

const workerSource = fs.readFileSync(path.join(root, 'service-worker.js'), 'utf8');
assert(workerSource.includes('const PUSH_COPY = Object.freeze({'), 'Localized push notification copy is missing.');
assert(workerSource.includes("event.data.type === 'KHADAMATI_LANGUAGE'"), 'The service worker does not persist language changes.');
for (const language of ['ar', 'en', 'hi', 'bn', 'ur']) {
  assert(workerSource.includes(`${language}: Object.freeze({`), `Push copy is missing for ${language}.`);
}

console.log(JSON.stringify({ ok: true, languages: i18n.languages, catalogRows: rows.length }));
