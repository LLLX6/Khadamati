export function approvedApiBase(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('Mobile API must be an HTTPS origin without credentials or a path.');
  }
  if (['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) {
    throw new Error('Mobile API must be reachable from a physical phone.');
  }
  return url.origin;
}

export function externalHttpsUrl(value, origin) {
  try {
    const url = new URL(value, origin === 'null' ? undefined : origin);
    return url.protocol === 'https:' && !url.username && !url.password && url.origin !== origin ? url.href : null;
  } catch {
    return null;
  }
}

export function safeFilename(value) {
  return String(value || 'khadamati-file').normalize('NFC')
    .replace(/[\\/\u0000-\u001f\u007f]/g, '_').replace(/^\.+/, '_').slice(0, 160) || 'khadamati-file';
}

export function shareDirectoryId(cryptoApi = globalThis.crypto, timestamp = Date.now()) {
  // getRandomValues also works in supported WebViews predating randomUUID.
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  return `${timestamp}-${Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')}`;
}

function locationError(error) {
  const codes = { OS_PLUG_GLOC_0003: 1, OS_PLUG_GLOC_0007: 2, OS_PLUG_GLOC_0010: 3 };
  return { code: codes[error?.code] || error?.code || 2, message: error?.message || 'position_unavailable' };
}

// Preserve the browser callback contract, including cancellation before the
// native watch ID has arrived across the bridge.
export function createGeolocationAdapter(plugin) {
  const watches = new Map();
  let nextId = 0;
  return {
    getCurrentPosition(success, failure = () => {}, options = {}) {
      void plugin.getCurrentPosition(options).then(success, error => failure(locationError(error)));
    },
    watchPosition(success, failure = () => {}, options = {}) {
      const id = ++nextId;
      const watch = { nativeId: null, cancelled: false };
      watches.set(id, watch);
      void plugin.watchPosition(options, (position, error) => {
        if (watch.cancelled) return;
        if (error) failure(locationError(error));
        else if (position) success(position);
      }).then(nativeId => {
        watch.nativeId = nativeId;
        if (watch.cancelled) return plugin.clearWatch({ id: nativeId });
      }).catch(error => {
        watches.delete(id);
        if (!watch.cancelled) failure(locationError(error));
      });
      return id;
    },
    clearWatch(id) {
      const watch = watches.get(id);
      if (!watch) return;
      watch.cancelled = true;
      watches.delete(id);
      if (watch.nativeId !== null) void plugin.clearWatch({ id: watch.nativeId }).catch(() => {});
    },
    async permissionState() {
      try {
        const status = await plugin.checkPermissions();
        if (status.location === 'granted' || status.coarseLocation === 'granted') return 'granted';
        return status.location === 'denied' ? 'denied' : 'prompt';
      } catch {
        return 'prompt';
      }
    },
  };
}
