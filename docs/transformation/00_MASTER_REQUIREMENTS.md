# مصفوفة التحول الشامل لخدماتي

هذه المصفوفة هي سجل التنفيذ الملزم للبروموتات الأربعة. لا تعني عبارة `موجود` أن البند مكتمل؛ ينتقل البند إلى `تم واختبر` فقط بعد ربطه باختبار ودليل قابل لإعادة التشغيل.

## رموز الحالة

- `قيد التحقق`: يوجد تنفيذ سابق ويحتاج إعادة اختبار.
- `قيد التنفيذ`: التعديل جار في فرع التحول.
- `تم واختبر`: نُفذ واجتاز الاختبار المذكور.
- `خارجي`: يتطلب حسابًا أو عقدًا أو خدمة أو قرارًا خارج المستودع، وقد جُهز له مسار آمن دون ادعاء التفعيل.

## المرحلة الأولى: الأساس والأمان

| المعرّف | المتطلب | الحالة | التنفيذ أو الدليل | بوابة الاختبار أو العائق |
|---|---|---|---|---|
| F01 | نسخة احتياطية واستعادة معزولة | تم واختبر | `tests/test_backup_restore.py`, `backups/` | 3 اختبارات Backup/Restore ناجحة |
| F02 | PostgreSQL وتخزين ملفات دائم مع انتقال قابل للرجوع | خارجي | `postgres_schema.sql`, `POSTGRESQL_MIGRATION.md`, `export_khadamati_data.py` | تشغيل PostgreSQL وObject Storage يتطلب خدمة دائمة وبيانات اتصال |
| F03 | الخادم مصدر الحقيقة وLocalStorage للتفضيلات فقط | تم واختبر | `server.py`, طبقة bootstrap والجلسات | Security API + عزل الزائر والحسابات |
| F04 | جلسات قصيرة ومجزأة وإلغاء واستعادة آمنة | تم واختبر | `auth_sessions`, Access/Refresh cookies | Unit + Security API |
| F05 | عامل ثانٍ إلزامي للإدارة وصلاحيات وظيفية | تم واختبر | `khadamati_security.py`, `admin_users` | `tests/test_security_2fa.py` |
| F06 | IDOR وOwnership وInjection وXSS وCSRF وCORS وMass Assignment | تم واختبر | `tests/security-api.py` | جميع فحوص Security API ناجحة |
| F07 | مركز تحقق هوية/منشأة/نشاط مستقل | تم واختبر | `khadamati_trust.py` | Trust Unit + API |
| F08 | مسارات قانونية للفرد والشركة والعامل المرتبط بمنشأة | تم واختبر | تسجيل المزود ومركز التحقق | UI على الهاتف + Trust API |
| F09 | انتهاء الوثائق والتعليق وإعادة التحقق والتدقيق | تم واختبر | دورة حياة التحقق وإشعارات الإدارة | Trust lifecycle tests |
| F10 | موقع تقريبي قبل القبول ودقيق بعده | تم واختبر | `server.py` location precision | `tests/trust-api.py` |
| F11 | حماية الصور والملفات والتوقيع وإعادة التسمية | تم واختبر | signed media + magic bytes | Security API |
| F12 | الإبلاغ والحظر والكتم وإنهاء المحادثة وسجل الأدلة | تم واختبر | Trust blocks/complaints + chat controls | Trust + Platform API + UI |
| F13 | فحص الأسرار والتبعيات والسجلات والمراقبة | تم واختبر | CI + structured logs + readiness | npm/pip/Bandit/secret scan |
| F14 | نسخ يومية وخارجية وRPO/RTO واستعادة شهرية | خارجي | Runbook محلي واختبار استعادة | الجدولة والتخزين الخارجي يحتاجان خدمة تشغيل |
| F15 | اختبارات وحدة وتكامل وصلاحيات وهجرة وحمل منخفض | تم واختبر | `tests/` | 62 Unit + API/E2E/UI/Performance |

## المرحلة الثانية: الثقة والسوق

| المعرّف | المتطلب | الحالة | التنفيذ أو الدليل | بوابة الاختبار أو العائق |
|---|---|---|---|---|
| T01 | فصل الأفراد والشركات في البحث والظهور | تم واختبر | مرشح نوع المزود وبطاقات منفصلة | UI + API smoke |
| T02 | ملف فرد وملف شركة غني وواضح | تم واختبر | جودة متعددة + ملخص فرق وفروع وتغطية | UI screenshots |
| T03 | شركة بمالك ومديرين وفروع وفرق وصلاحيات وتعيين | تم واختبر | team members/branches/roles | Security + company flow |
| T04 | طلبات شركات ومشاريع بأهلية | تم واختبر | مساحة المجتمع والباقات + أهلية المزود | Marketplace smoke |
| T05 | ملف طلب موثق من المشكلة حتى الضمان | تم واختبر | Workflow/lifecycle/evidence/assets | Workflow integration |
| T06 | مقارنة عروض متعددة المؤشرات | تم واختبر | جودة وتحقق وشروط عرض | UI + API |
| T07 | عرض منظم مواد/أجور/مدة/ضمان/صلاحية/PDF | تم واختبر | Structured quote server + UI | Trust API + report export |
| T08 | تقييم مرتبط بطلب مكتمل وجودة متعددة | تم واختبر | review dimensions + quality breakdown | Trust API |
| T09 | شكوى وأدلة وقرار واستئناف وتصعيد | تم واختبر | `khadamati_trust.py` | Trust tests |
| T10 | تمييز ضمان المزود عن ضمان المنصة | تم واختبر | offer warranty wording | UI/content checks |
| T11 | دعوة مزود معروف وطلب مباشر وسجل | تم واختبر | دعوة آمنة وربط عند الانضمام | Growth + Platform integration |
| T12 | دفتر صيانة وأصول وتكلفة وصور وضمان | تم واختبر | `khadamati_workflow.py` service assets | Workflow tests |
| T13 | اتصال داخلي وموافقة قنوات قابلة للسحب | تم واختبر | ContactConsentService | Smoke + Trust API |
| T14 | مركز ثقة بلا شارات مضللة | تم واختبر | Verification/quality/complaints panels | UI + Trust API |
| T15 | Mobile First + RTL/LTR + فاتح/داكن + مبسط | تم واختبر | Design system الحالي | 320/375/390/430/1440 screenshots |
| T16 | رحلة المستخدم والفرد والشركة والإدارة | تم واختبر | E2E suite | UI/API smoke |

## المرحلة الثالثة: الاحتفاظ والنمو والربح

| المعرّف | المتطلب | الحالة | التنفيذ أو الدليل | بوابة الاختبار أو العائق |
|---|---|---|---|---|
| G01 | باقات شركات وظيفية بفرق وفروع وتقارير وSLA | تم واختبر | Entitlements + teams/branches/SLA | Company subscription flow |
| G02 | حساب مؤسسات B2B بعدة مواقع وموافقات وتقارير | تم واختبر | طبقة مؤسسات قابلة للعزل | Platform integration |
| G03 | CRM مزود للعملاء والمواعيد والعروض والفواتير والضمان | تم واختبر | ربط السجل والمهام والفواتير والأصول | Platform tests |
| G04 | عقود صيانة دورية وتذكيرات وتجديد | تم واختبر | recurring contracts | Platform integration |
| G05 | برنامج إحالة مع منع التحايل | تم واختبر | referrals + uniqueness/risk | Growth + Platform tests |
| G06 | مركز تدريب وتقدم واختبارات | تم واختبر | training modules/progress | Platform tests |
| G07 | شارات إنجاز مبنية على البيانات | تم واختبر | provider achievements | Platform tests |
| G08 | أبلغني عند التوفر وخريطة نقص خدمات مجهولة | تم واختبر | demand alerts/gaps | Platform API |
| G09 | دخل شفاف من الاشتراك والترويج والرعاية والعروض | تم واختبر | subscriptions/promotions/campaigns/ads | Domain + UI smoke |
| G10 | منع رسوم المستخدم وبيع الأرقام والشارات | تم واختبر | سياسات وصلاحيات الخادم | Policy + security review |
| G11 | لوحة مؤشرات مالية من بيانات فعلية فقط | تم واختبر | Admin reports | Report/API tests |
| G12 | سيناريوهات مالية قابلة للتعديل ومنفصلة محاسبيًا | تم واختبر | admin scenario planner | Platform unit/UI |
| G13 | إطلاق تدريجي وFeature Flags وفجوات العرض | تم واختبر | platform flags + availability | Admin/UI |
| G14 | منشأة عُمانية ومرشحات وتوعية بلا ادعاء حكومي | تم واختبر | verification/entity filters | Trust UI |
| G15 | تقارير سوق مجهولة ومنع تصدير البيانات الشخصية | تم واختبر | aggregate-only exports | Privacy/report tests |
| G16 | اختبارات الاشتراك والفوترة والعقود والإحالة والأداء | تم واختبر | Growth/Platform suites | Final gate |

## المرحلة الرابعة: التوسع والامتثال والإطلاق

| المعرّف | المتطلب | الحالة | التنفيذ أو الدليل | بوابة الاختبار أو العائق |
|---|---|---|---|---|
| S01 | دفع اختياري خادمي وWebhook وIdempotency ونزاع | تم واختبر | PaymentAdapter + manual approval | التفعيل الحقيقي خارجي ويحتاج بوابة |
| S02 | واجهة شراكة حماية/تأمين بلا وعود قبل العقد | خارجي | Feature flag ونص قانوني معطل | عقد ومراجعة قانونية |
| S03 | طبقة تكامل حكومي قابلة للاستبدال بلا ادعاء اتصال | خارجي | Adapter contract معطل | اتفاق وAPI رسمي |
| S04 | مساعد طلب اختياري منظم يحمي البيانات | تم واختبر | deterministic request assistant | UI + privacy tests |
| S05 | Risk Score ومراجعة بشرية دون حظر آلي | تم واختبر | risk events/service | Platform/security tests |
| S06 | API مؤسسات بمفاتيح دورية ونطاق وحدود وتدقيق | تم واختبر | enterprise API layer | Platform API authorization |
| S07 | تقارير وطنية مجهولة وموافقة قانونية | خارجي | تقارير مجمعة فقط؛ التفعيل الخارجي معطل | مراجعة قانونية |
| S08 | PostgreSQL/Pooling/Queues/CDN/WAF/Cache/Object Storage | خارجي | readiness/config/runbook | خدمات بنية تحتية خارجية |
| S09 | اختبارات اختراق آلية ورفع وScraping وحمل وRollback | تم واختبر | security/performance/backup suites | الاختبار البشري يبقى خارجيًا |
| S10 | متطلبات Apple وGoogle من مصادر رسمية | تم واختبر | `04_SCALE_RELEASE.md`, ملفات المتاجر | تحقق رسمي بتاريخ 1 أغسطس 2026 |
| S11 | الخصوصية والشروط والشكاوى والضمان والحذف والدفع | خارجي | `ملفات-الإطلاق/07-السياسات-والاتفاقيات/` | المسودات جاهزة وتحتاج مراجعة قانونية عُمانية |
| S12 | كل المقاسات واللغات والثيمات وضعف الشبكة | تم واختبر | UI E2E | 320/375/390/430/1440 screenshots |
| S13 | Unit/Integration/API/E2E/Security/A11y/Performance/Build | تم واختبر | CI + local suites | Final gate ناجحة |
| S14 | تدقيق المصفوفة بلا بند مجهول | تم واختبر | هذا الملف | جميع البنود مصنفة |
| S15 | تقرير نهائي وMigrations وRollback ومخاطر | تم واختبر | `05_FINAL_TRANSFORMATION_REPORT.md` | مراجعة نهائية مكتملة |
| S16 | RC وPR وTag وRelease وZIP ونشر واختبار حي | خارجي | يُنفذ بعد نجاح البوابات؛ production يتطلب تخزينًا دائمًا | GitHub/Render/external readiness |
