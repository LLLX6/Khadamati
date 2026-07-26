# جاهزية Google Play

**تاريخ التحقق:** 26 يوليو 2026
**الحالة:** 🟡 تجهيز فقط، لا يوجد AAB أو نشر

## النتيجة المختصرة

المشروع PWA وليس مشروع Android. لا توجد ملفات Gradle أو Android Manifest أو
App Bundle. يلزم إنشاء تطبيق Android أصلي أو غلاف موثق، ثم بناء `.aab` موقع
واختباره. لا يجوز رفع WebView فارغ لا يقدم تجربة تطبيق مستقرة أو لا يعمل عند
فشل الخادم.

## ما هو جاهز

- واجهة عربية وإنجليزية ومتجاوبة.
- Manifest PWA وأيقونة 512×512.
- حذف الحساب داخل إعدادات الحساب.
- مسودات الخصوصية وحذف البيانات.
- اختبارات UI عند 320 و390 بكسل.
- معالجة رفض الموقع مع اختيار يدوي.

## موانع الرفع الحالية

1. لا يوجد مشروع Android أو Package Name محجوز.
2. لا يوجد Android App Bundle أو Upload Key.
3. لا يوجد Play App Signing أو Play Console مملوك للمالك.
4. لا يوجد Target SDK لأن المشروع ليس Android بعد.
5. لا يوجد رابط ويب عام لطلب حذف الحساب.
6. لا يوجد Data Safety نهائي مبني على SDKs فعلية.
7. لا توجد بيئة staging وحسابات مراجعة.
8. تخزين الخادم الحالي غير مثبت كإنتاج دائم.

## اسم حزمة مقترح غير محجوز

```text
om.khadamati.app
```

🔴 يجب التحقق من توفره واعتماده في حساب المؤسسة قبل إدخاله في مشروع Android.

## متطلبات الإصدار

- استخدام Android App Bundle.
- تفعيل Play App Signing، مع Upload Key محفوظ في مخزن أسرار يملكه المالك.
- استهداف مستوى API المطلوب وقت التقديم. وفق وثائق Android الحالية، بدءًا من
  31 أغسطس 2026 يلزم للتطبيقات والتحديثات الجديدة استهداف Android 16
  (API 36) أو أعلى، مع استثناءات لفئات أجهزة محددة لا تنطبق مبدئيًا هنا.
- إكمال Data Safety بدقة، بما فيها بيانات أي WebView وSDKs.
- توفير سياسة خصوصية حتى إن كان الادعاء أن جمع البيانات محدود.
- توفير مسار حذف داخل التطبيق ورابط ويب لحذف الحساب أو طلبه.
- عدم طلب SMS أو Call Log أو Contacts أو Background Location؛ لا تحتاجها
  وظائف خدماتي الحالية.
- إذا أضاف الغلاف إذنًا عالي الخطورة، يلزم تبريره وقد يطلب Google نموذجًا
  وفيديو مراجعة.

## بيانات المتجر

- اسم عربي وإنجليزي.
- وصف مختصر وكامل دقيقان.
- أيقونة متجر 512×512 PNG بحد أقصى 1024KB وفق المصدر الرسمي.
- Feature Graphic مطلوبة لصفحة المتجر.
- Screenshots حقيقية بالعربية والإنجليزية للهاتف.
- بريد دعم ورابط سياسة ورابط حذف حساب.
- تصنيف محتوى وفئة تطبيق يراجعان في Play Console.

## المصادر الرسمية

- [Target API](https://developer.android.com/google/play/requirements/target-sdk)
- [Data Safety](https://support.google.com/googleplay/android-developer/answer/10787469)
- [حذف الحساب](https://support.google.com/googleplay/android-developer/answer/13327111)
- [Play App Signing](https://support.google.com/googleplay/android-developer/answer/9842756)
- [التصريح عن الصلاحيات](https://support.google.com/googleplay/android-developer/answer/9214102)
- [أصول صفحة المتجر](https://support.google.com/googleplay/android-developer/answer/9866151)

تم التحقق في 26 يوليو 2026، ويعاد التحقق يوم التقديم.

## ما يجب أن يفعله المالك

1. إنشاء Play Console باسم المالك أو المؤسسة والتحقق من بياناته.
2. اعتماد Package Name.
3. إنشاء وحماية Upload Key؛ لا يُرسل بالبريد أو Git.
4. اختيار تقنية Android وبناء التطبيق.
5. اعتماد Data Safety والسياسات وروابط الحذف.
6. توفير حسابات staging للمراجعة.

> هذه الوثيقة لا تضمن قبول التطبيق، ولا تعني أن الإصدار الحالي قابل للرفع.
