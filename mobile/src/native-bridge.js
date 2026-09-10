import { Capacitor } from '@capacitor/core';
import { App } from '@capacitor/app';
import { Browser } from '@capacitor/browser';
import { Filesystem, Directory } from '@capacitor/filesystem';
import { Geolocation } from '@capacitor/geolocation';
import { Share } from '@capacitor/share';
import { createGeolocationAdapter, externalHttpsUrl, safeFilename } from './native-helpers.mjs';

if (Capacitor.isNativePlatform()) {
  const apiBase = __KHADAMATI_MOBILE_API__;
  const geolocation = createGeolocationAdapter(Geolocation);
  const cacheDirectory = 'khadamati-share';
  let sharing = false;

  // Keep a recipient's file available after its share activity returns. Remove
  // expired private cache folders on the next launch (no cloud backup).
  const cleanup = async () => {
    try {
      const result = await Filesystem.readdir({ path: cacheDirectory, directory: Directory.Cache });
      for (const file of result.files) {
        if (/^\d{13}-[a-f0-9-]+$/.test(file.name) && Date.now() - Number(file.name.split('-')[0]) > 86400000) {
          await Filesystem.rmdir({ path: `${cacheDirectory}/${file.name}`, directory: Directory.Cache, recursive: true });
        }
      }
    }
    catch { /* The directory does not exist on first launch. */ }
  };
  const initialCleanup = cleanup();
  const blobData = blob => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(reader.error || new Error('file_read_failed'));
    reader.readAsDataURL(blob);
  });

  async function share(data = {}) {
    if (sharing) throw new Error('share_in_progress');
    const files = Array.from(data.files || []);
    if (files.length > 10 || files.reduce((size, file) => size + file.size, 0) > 20 * 1024 * 1024) {
      throw new Error('share_file_too_large');
    }
    sharing = true;
    const shareDirectory = `${cacheDirectory}/${Date.now()}-${crypto.randomUUID()}`;
    try {
      await initialCleanup;
      const paths = [];
      for (const [index, file] of files.entries()) {
        const result = await Filesystem.writeFile({
          path: `${shareDirectory}/${index}-${safeFilename(file.name)}`,
          directory: Directory.Cache,
          data: await blobData(file),
          recursive: true,
        });
        paths.push(result.uri);
      }
      const url = data.url ? externalHttpsUrl(data.url, location.origin) : undefined;
      await Share.share({ title: data.title || 'خدماتي', text: data.text || '', ...(url ? { url } : {}), ...(paths.length ? { files: paths } : {}) });
    } catch (error) {
      try { await Filesystem.rmdir({ path: shareDirectory, directory: Directory.Cache, recursive: true }); } catch { /* No files may have been written. */ }
      if (/cancel/i.test(String(error?.message))) throw new DOMException('Sharing cancelled', 'AbortError');
      throw error;
    } finally {
      sharing = false;
    }
  }

  const native = Object.freeze({
    platform: Capacitor.getPlatform(), apiBase, geolocation,
    notificationHint(language) {
      return ({
        ar: 'الإشعارات متاحة داخل التطبيق. إشعارات الهاتف غير مفعلة في نسخة الاختبار هذه.',
        en: 'Updates are available inside the app. Phone push notifications are not enabled in this test build.',
        hi: 'अपडेट ऐप के अंदर उपलब्ध हैं। इस परीक्षण संस्करण में फ़ोन पुश सूचनाएँ सक्षम नहीं हैं।',
        bn: 'অ্যাপের ভিতরে আপডেট পাওয়া যায়। এই পরীক্ষামূলক সংস্করণে ফোনের পুশ বিজ্ঞপ্তি চালু নেই।',
        ur: 'اپ ڈیٹس ایپ کے اندر دستیاب ہیں۔ اس آزمائشی ورژن میں فون کی پش اطلاعات فعال نہیں ہیں۔',
      })[language] || 'Updates are available inside the app. Phone push notifications are not enabled in this test build.';
    },
    async download(name, content, type = 'text/plain') {
      const file = new File([content], safeFilename(name), { type: content?.type || type });
      await share({ title: name, files: [file] });
    },
  });
  Object.defineProperty(window, 'KhadamatiNative', { value: native, configurable: false });
  Object.defineProperty(navigator, 'share', { value: share, configurable: true });
  Object.defineProperty(navigator, 'canShare', { value: data => {
    const files = Array.from(data?.files || []);
    return files.length <= 10 && files.every(file => file instanceof Blob) && files.reduce((size, file) => size + file.size, 0) <= 20 * 1024 * 1024;
  }, configurable: true });

  const originalOpen = window.open.bind(window);
  window.open = (value, target, features) => {
    const url = value && externalHttpsUrl(value, location.origin);
    if (!url) return originalOpen(value, target, features);
    void Browser.open({ url }).catch(() => originalOpen(url, '_blank', 'noopener'));
    return null;
  };
  document.addEventListener('click', event => {
    const anchor = event.target.closest?.('a[href]');
    if (!anchor || anchor.hasAttribute('download') || event.defaultPrevented) return;
    const url = externalHttpsUrl(anchor.href, location.origin);
    if (!url) return;
    event.preventDefault();
    void Browser.open({ url }).catch(() => originalOpen(url, '_blank', 'noopener'));
  });
  void App.addListener('appStateChange', state => {
    window.dispatchEvent(new CustomEvent('khadamati:native-state', { detail: state }));
  });
  if (Capacitor.getPlatform() === 'android') {
    void App.addListener('backButton', async () => {
      const event = new CustomEvent('khadamati:native-back', { cancelable: true });
      if (window.dispatchEvent(event)) await App.minimizeApp();
    });
  }
}
