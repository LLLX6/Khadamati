(function attachKhadamatiUiState(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.KhadamatiUI = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function buildKhadamatiUiState() {
  'use strict';

  const COPY = {
    ar: {
      confirmed: ['تم الحفظ', 'تم تأكيد العملية من الخادم.', 'ok'],
      pending: ['بانتظار الاتصال', 'العملية محفوظة على هذا الجهاز وستُرسل تلقائياً عند عودة الاتصال.', 'pending'],
      failed: ['لم تصل العملية', 'لم يستلم الخادم هذه العملية. راجع الاتصال ثم حاول مرة أخرى.', 'failed'],
      requestPending: ['قيد الإرسال', 'طلبك محفوظ على هذا الجهاز وسنرسله تلقائياً عند عودة الاتصال.', 'pending'],
      requestFailed: ['لم يصل الطلب', 'لم يستلم الخادم طلبك. راجع الاتصال ثم أعد الإرسال.', 'failed'],
      waitlisted: ['نبحث عن مزود مناسب', 'تم حفظ الطلب، ونبحث عن مزود يطابق الخدمة والمنطقة. يمكنك تعديل الخدمة أو المنطقة في أي وقت.', 'waiting'],
      matching: ['نبحث عن الأنسب', 'تم حفظ الطلب ويجري الآن البحث عن مزود مناسب.', 'waiting'],
      received: ['تم استلام الطلب', 'وصل الطلب إلى خدماتي وسيظهر لك أي تحديث هنا.', 'ok']
    },
    en: {
      confirmed: ['Saved', 'The server confirmed this action.', 'ok'],
      pending: ['Waiting for connection', 'This action is saved on this device and will be sent automatically when the connection returns.', 'pending'],
      failed: ['Action not received', 'The server did not receive this action. Check your connection and try again.', 'failed'],
      requestPending: ['Sending', 'Your request is saved on this device and will be sent automatically when the connection returns.', 'pending'],
      requestFailed: ['Request not received', 'The server did not receive your request. Check your connection and send it again.', 'failed'],
      waitlisted: ['Finding a suitable provider', 'Your request is saved while we look for a provider matching the service and area. You can edit either at any time.', 'waiting'],
      matching: ['Finding the best match', 'Your request is saved and matching is in progress.', 'waiting'],
      received: ['Request received', 'Khadamati received the request. Updates will appear here.', 'ok']
    }
  };

  const STATUS_LABELS = {
    ar: {
      received: 'تم الاستلام', matching: 'بانتظار العروض', viewed: 'قيد المراجعة',
      accepted: 'تم اختيار المزود', appointmentConfirmed: 'الموعد مؤكد', inProgress: 'قيد التنفيذ',
      awaitingConfirmation: 'راجع الإنجاز', qualityReview: 'مراجعة الجودة', closed: 'مكتمل',
      archived: 'مؤرشف', cancelled: 'ملغي', paused: 'متوقف مؤقتاً', unavailable: 'نبحث عن مزود',
      contacted: 'تم التواصل', claimed: 'تم استلام المهمة', expired: 'انتهت الصلاحية', deleted: 'محذوف',
      open: 'مفتوح', submitted: 'تم الإرسال', approved: 'معتمد', rejected: 'مرفوض',
      pending: 'بانتظار المراجعة', under_review: 'قيد المراجعة', suspended: 'موقوف',
      draft: 'مسودة', scheduled: 'مجدول', active: 'نشط', completed: 'مكتمل', posted: 'مرحّل', voided: 'ملغي', inactive: 'موقوف'
    },
    en: {
      received: 'Received', matching: 'Awaiting offers', viewed: 'Under review',
      accepted: 'Provider selected', appointmentConfirmed: 'Appointment confirmed', inProgress: 'In progress',
      awaitingConfirmation: 'Review completion', qualityReview: 'Quality review', closed: 'Completed',
      archived: 'Archived', cancelled: 'Cancelled', paused: 'Temporarily paused', unavailable: 'Finding a provider',
      contacted: 'Contacted', claimed: 'Task claimed', expired: 'Expired', deleted: 'Deleted',
      open: 'Open', submitted: 'Submitted', approved: 'Approved', rejected: 'Rejected',
      pending: 'Pending review', under_review: 'Under review', suspended: 'Suspended',
      draft: 'Draft', scheduled: 'Scheduled', active: 'Active', completed: 'Completed', posted: 'Posted', voided: 'Voided', inactive: 'Inactive'
    }
  };

  function language(value) { return value === 'en' ? 'en' : 'ar'; }
  function normalizeMutationState(value) {
    return value === 'failed' ? 'failed' : value === 'pending' ? 'pending' : 'confirmed';
  }
  function mutationPresentation(value, lang, options) {
    const state = normalizeMutationState(value);
    const dictionary = COPY[language(lang)];
    const request = !!(options && options.request);
    const key = request && state === 'pending' ? 'requestPending' : request && state === 'failed' ? 'requestFailed' : state;
    const copy = dictionary[key];
    return { state, label: copy[0], message: copy[1], tone: copy[2], retry: state === 'failed', pending: state === 'pending', confirmed: state === 'confirmed' };
  }
  function requestPresentation(request, lang) {
    const item = request || {};
    const selectedLanguage = language(lang);
    const syncState = normalizeMutationState(item.syncState || 'confirmed');
    if (syncState !== 'confirmed') return mutationPresentation(syncState, selectedLanguage, { request: true });
    const waitlisted = item.waitlisted === true || item.waitlistState === 'active' || item.status === 'unavailable';
    if (waitlisted) {
      const copy = COPY[selectedLanguage].waitlisted;
      return { state: 'confirmed', label: copy[0], message: copy[1], tone: copy[2], retry: false, waitlisted: true };
    }
    const status = String(item.status || 'received');
    if (status === 'matching' || status === 'viewed') {
      const copy = COPY[selectedLanguage].matching;
      return { state: 'confirmed', label: STATUS_LABELS[selectedLanguage][status] || copy[0], message: copy[1], tone: copy[2], retry: false };
    }
    const copy = COPY[selectedLanguage].received;
    return { state: 'confirmed', label: STATUS_LABELS[selectedLanguage][status] || copy[0], message: copy[1], tone: 'ok', retry: false };
  }
  function humanStatus(status, lang) {
    const selectedLanguage = language(lang);
    return STATUS_LABELS[selectedLanguage][String(status || '')] || (selectedLanguage === 'ar' ? 'بانتظار التحديث' : 'Awaiting update');
  }
  function notificationKind(item) {
    const route = String((item && item.actionRoute) || '');
    const type = String((item && item.type) || 'update');
    if (type === 'chat' || route.includes(':chat:')) return 'messages';
    if (['request', 'unavailable', 'offer', 'provider_suggestion', 'community'].includes(type) || route.includes(':request:')) return 'requests';
    return 'updates';
  }
  function notificationGroupKey(item) {
    const current = item || {};
    const kind = notificationKind(current);
    return [kind, current.relatedId || current.related_id || current.threadId || current.type || 'update', current.target || '', current.targetId || current.target_id || ''].join(':');
  }
  function groupNotifications(items) {
    const groups = new Map();
    (Array.isArray(items) ? items : []).forEach(function add(item) {
      if (!item) return;
      const key = notificationGroupKey(item);
      const current = groups.get(key);
      const createdAt = String(item.createdAt || item.created_at || '');
      if (!current) {
        groups.set(key, Object.assign({}, item, {
          groupKey: key,
          groupKind: notificationKind(item),
          groupIds: [item.id].filter(Boolean),
          groupCount: 1,
          unreadCount: item.read ? 0 : 1,
          createdAt
        }));
        return;
      }
      current.groupCount += 1;
      current.unreadCount += item.read ? 0 : 1;
      if (item.id) current.groupIds.push(item.id);
      if (createdAt > String(current.createdAt || '')) {
        const preserved = { groupKey: current.groupKey, groupKind: current.groupKind, groupIds: current.groupIds, groupCount: current.groupCount, unreadCount: current.unreadCount };
        Object.assign(current, item, preserved, { createdAt });
      }
      current.read = current.unreadCount === 0;
    });
    return Array.from(groups.values()).sort(function newestFirst(a, b) { return String(b.createdAt || '').localeCompare(String(a.createdAt || '')); });
  }
  function stableValue(value) {
    if (Array.isArray(value)) return value.map(stableValue);
    if (value && typeof value === 'object') {
      return Object.keys(value).sort().reduce(function reduce(result, key) {
        if (typeof value[key] !== 'function' && value[key] !== undefined) result[key] = stableValue(value[key]);
        return result;
      }, {});
    }
    return value;
  }
  function stableSignature(value) { return JSON.stringify(stableValue(value)); }

  return {
    language,
    normalizeMutationState,
    mutationPresentation,
    requestPresentation,
    humanStatus,
    notificationKind,
    notificationGroupKey,
    groupNotifications,
    stableSignature
  };
});
