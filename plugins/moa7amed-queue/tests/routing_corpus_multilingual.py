"""Evaluation corpus for the model router: Arabic and mixed Arabic/English task prompts.

Each entry is (prompt, expected tier, language, topic).

  language   ar-msa  Modern Standard Arabic
             ar-eg   Egyptian Arabic
             mixed   Arabic with English technical terms, as developers actually write
  topic      docs, ui, feature, architecture, database, debugging, security, short,
             negation, writing

How to read the numbers (`python tests/routing_eval.py` prints the current ones):

DEVELOPMENT  The set the Arabic rules were written against. A perfect score here only
             shows that the rules do what they were written to do.
HELD_OUT     Written before the rules and not inspected while tuning, but drafted in
             the same sitting by the same author, so it is not really independent.
FRESH        Written after the first version of the rules was frozen, in different
             words, and scored once before anything else changed. That first score is
             the honest one and is recorded below. The misses were then used for a
             second round of rules, so FRESH is a tuned-against set from then on.

Before Arabic support (rules of version 0.3.0):  DEVELOPMENT 56/79, HELD_OUT 15/24.
First score on FRESH, rules only:                19/30. Every miss was a hard task
routed to sonnet instead of opus (10) or a trivial one to sonnet instead of haiku (1);
no hard task went to haiku.
First score on FRESH, rules plus the model review (real calls, Claude Code 2.1.206,
haiku as reviewer): 29/30, 23 review calls, $0.28 in total as reported by the CLI.

What this supports: the rules are reliable on phrasing they know and fall back to
sonnet on phrasing they do not; the review step is what catches most of the rest.
It does not support any accuracy claim for Arabic prompts in general.
"""

FRESH_FIRST_SCORE_RULES_ONLY = (19, 30)
FRESH_FIRST_SCORE_WITH_REVIEW = (29, 30)
BEFORE_ARABIC_SUPPORT = {"DEVELOPMENT": (56, 79), "HELD_OUT": (15, 24)}

DEVELOPMENT = [
    # ---------------------------------------------------------------- simple documentation
    ("حدّث ملف README ليذكر أمر التثبيت الجديد.", "haiku", "ar-msa", "docs"),
    ("صحّح الأخطاء الإملائية في ملف CONTRIBUTING.md.", "haiku", "ar-msa", "docs"),
    ("أضف تعليقًا يشرح ما تفعله دالة إعادة المحاولة.", "haiku", "ar-msa", "docs"),
    ("اكتب ملخصًا قصيرًا لبنية المشروع في ملف docs/overview.md.", "haiku", "ar-msa", "docs"),
    ("ترجم ملف README إلى اللغة الإنجليزية.", "haiku", "ar-msa", "docs"),
    ("اشرح ما يفعله كل إعداد في ملف الإعدادات appsettings.json في ملاحظة قصيرة.", "haiku", "ar-msa", "docs"),
    ("حدّث سنة حقوق النشر في ترويسة الترخيص.", "haiku", "ar-msa", "docs"),
    ("أضف قسمًا في التوثيق يشرح طريقة تشغيل الاختبارات.", "haiku", "ar-msa", "docs"),
    ("عدّل الـ README وضيف فيه طريقة التثبيت الجديدة.", "haiku", "ar-eg", "docs"),
    ("صلّح الغلطات الإملائية اللي في ملف التوثيق.", "haiku", "ar-eg", "docs"),
    ("ضيف كومنت يشرح الفانكشن دي بتعمل إيه.", "haiku", "ar-eg", "docs"),
    ("اكتبلي شرح بسيط للمشروع في الـ README.", "haiku", "ar-eg", "docs"),
    ("رتّب الملفات اللي في فولدر scripts في فولدرات فرعية.", "haiku", "ar-eg", "docs"),
    ("Update الـ README وضيف section للـ installation.", "haiku", "mixed", "docs"),
    ("صلّح الـ typo اللي في CHANGELOG.md.", "haiku", "mixed", "docs"),
    ("اعمل rename للملفات اللي في assets وخليها lowercase.", "haiku", "mixed", "docs"),
    ("ضيف docstring لكل function في utils.py.", "haiku", "mixed", "docs"),
    # documentation about a technical subject is still documentation
    ("وثّق أمر ترحيل قاعدة البيانات الجديد في ملف README.", "haiku", "ar-msa", "docs"),
    ("اشرح في الـ README إزاي نشغّل الـ unit tests.", "haiku", "mixed", "docs"),

    # ---------------------------------------------------------------- UI and ordinary features
    ("أضف مربع بحث مع تصفية النتائج إلى صفحة جدول العملاء.", "sonnet", "ar-msa", "ui"),
    ("أنشئ نموذج تسجيل مع التحقق من صحة البريد الإلكتروني وقوة كلمة المرور.", "sonnet", "ar-msa", "ui"),
    ("أضف ترقيم الصفحات والفرز إلى شبكة الفواتير.", "sonnet", "ar-msa", "ui"),
    ("أضف دعم الوضع الداكن إلى صفحة الإعدادات.", "sonnet", "ar-msa", "ui"),
    ("أضف نقطة نهاية REST تُرجع قائمة الطلبات لعميل معيّن.", "sonnet", "ar-msa", "feature"),
    ("اكتب اختبارات وحدة لدوال التاريخ.", "sonnet", "ar-msa", "feature"),
    ("أصلح التحقق بحيث يرفض النموذج البريد الإلكتروني الفارغ.", "sonnet", "ar-msa", "feature"),
    ("نفّذ تصدير نتائج البحث إلى ملف CSV.", "sonnet", "ar-msa", "feature"),
    ("ضيف خانة بحث وفلتر في صفحة العملاء.", "sonnet", "ar-eg", "ui"),
    ("اعمل فورم تسجيل وفيه validation على الإيميل والباسورد.", "sonnet", "ar-eg", "ui"),
    ("زرار الحفظ مش بيعمل حاجة لما الاسم فيه علامة تنصيص، صلّحه.", "sonnet", "ar-eg", "ui"),
    ("ضيف dropdown نفلتر بيه التاسكات حسب الحالة.", "sonnet", "ar-eg", "ui"),
    ("عايز صفحة جديدة تعرض الفواتير في جدول وفيها pagination.", "sonnet", "ar-eg", "ui"),
    ("خلّي الـ sidebar يتقفل ويتفتح ويفتكر حالته.", "sonnet", "ar-eg", "ui"),
    ("اعمل endpoint جديد يرجّع بيانات العميل.", "sonnet", "ar-eg", "feature"),
    ("اكتب unit tests للـ date utils.", "sonnet", "mixed", "feature"),
    ("اعمل implement لـ CSV export في صفحة الـ master data search.", "sonnet", "mixed", "feature"),
    ("ضيف validation على الـ form علشان يرفض الـ email الفاضي.", "sonnet", "mixed", "ui"),
    ("أضف ASP.NET MVC controller و view لتعديل المنتجات.", "sonnet", "mixed", "feature"),
    ("صلّح الـ bug اللي في الـ checkout.", "sonnet", "mixed", "feature"),
    ("اعمل dashboard widget يعرض مبيعات الشهر.", "sonnet", "mixed", "ui"),

    # ---------------------------------------------------------------- architecture
    ("أعد تصميم نظام الصلاحيات ليدعم التحكم في الوصول المبني على الأدوار لكل مستأجر.", "opus", "ar-msa", "architecture"),
    ("أعد هيكلة وحدة الإشعارات إلى تصميم معتمد على الأحداث.", "opus", "ar-msa", "architecture"),
    ("افصل كود الفوترة من التطبيق الأحادي إلى خدمة مستقلة.", "opus", "ar-msa", "architecture"),
    ("صمّم معمارية النظام لدعم تعدد المستأجرين مع عزل البيانات في كل الخدمات.", "opus", "ar-msa", "architecture"),
    ("انقل نظام المصادقة إلى OAuth2.", "opus", "ar-msa", "security"),
    ("عايزين نعيد تصميم نظام الصلاحيات كله علشان يدعم الـ roles لكل tenant.", "opus", "ar-eg", "architecture"),
    ("افصل كود الفواتير من الـ monolith وخليه service لوحده.", "opus", "ar-eg", "architecture"),
    ("عايز أعمل refactor للـ data access layer كله علشان يستخدم الـ repository pattern في كل الموديولات.", "opus", "ar-eg", "architecture"),
    ("نقّل الـ auth بتاعنا لـ OAuth2.", "opus", "ar-eg", "security"),
    ("اعمل re-architect للـ notification module وخليه event-driven.", "opus", "mixed", "architecture"),
    ("صمّم الـ architecture بتاعة الـ multi-tenant data isolation في كل الـ services.", "opus", "mixed", "architecture"),

    # ---------------------------------------------------------------- database migrations
    ("صمّم مخطط قاعدة البيانات واكتب ملفات الترحيل لتقسيم جدول الطلبات إلى جدولين دون توقف الخدمة، وحدّث كل المستودعات والمعاملات.", "opus", "ar-msa", "database"),
    ("اكتب ترحيل البيانات الذي يملأ عمود tenant_id الجديد في قاعدة بيانات الإنتاج دون قفل الجداول.", "opus", "ar-msa", "database"),
    ("أعد تصميم مخطط قاعدة البيانات لتطبيع جدول العملاء ونقل البيانات الحالية.", "opus", "ar-msa", "database"),
    ("عايز migration يقسم جدول الأوردرات لجدولين من غير ما السيستم يقف، وعدّل كل الـ repositories.", "opus", "ar-eg", "database"),
    ("اكتب data migration تملى عمود الـ tenant_id في داتابيز البرودكشن من غير ما نقفل الجداول.", "opus", "ar-eg", "database"),
    ("اعمل database migration تقسم الـ orders table لـ orders و order_items بـ zero downtime.", "opus", "mixed", "database"),
    ("غيّر الـ schema وضيف foreign keys ما بين الجداول واكتب الـ migration مع backfill للبيانات القديمة.", "opus", "mixed", "database"),

    # ---------------------------------------------------------------- short but difficult
    ("أصلح حالة الجمود في معالج المدفوعات.", "opus", "ar-msa", "short"),
    ("صلّح تسريب الذاكرة.", "opus", "ar-msa", "short"),
    ("لماذا يفشل تسجيل الدخول عشوائيًا في بيئة الإنتاج؟", "opus", "ar-msa", "debugging"),
    ("ابحث عن السبب الجذري لأخطاء 500 المتقطعة في واجهة الدفع.", "opus", "ar-msa", "debugging"),
    ("صلّح الـ deadlock اللي في الـ payment worker.", "opus", "mixed", "short"),
    ("في race condition في الكاش.", "opus", "mixed", "short"),
    ("في memory leak في الـ image cache.", "opus", "mixed", "short"),
    ("ليه اللوجين بيفشل ساعات في البرودكشن؟", "opus", "ar-eg", "debugging"),
    ("الصفحة بتاخد 40 ثانية علشان تفتح، شوف الـ bottleneck فين وصلّحه.", "opus", "ar-eg", "debugging"),
    ("المزامنة بين النظامين بتضيّع سجلات ساعات، اعرف السبب وصلّحه.", "opus", "ar-eg", "debugging"),
    ("في ثغرة SQL injection في البحث، صلّحها وراجع باقي الاستعلامات.", "opus", "mixed", "security"),

    # ---------------------------------------------------------------- short, not trivial: never haiku
    ("صلّح الخطأ في صفحة الدفع.", "sonnet", "ar-msa", "short"),
    ("ضيف لوجين.", "sonnet", "ar-eg", "short"),
    ("صلّحه.", "sonnet", "ar-eg", "short"),
    ("خلّي البحث أسرع.", "sonnet", "ar-eg", "short"),
    ("اعمل refactor للـ billing.", "sonnet", "mixed", "short"),

    # ---------------------------------------------------------------- negated mentions are not signals
    ("حدّث الـ README بس، ومتلمسش كود المصادقة ولا قاعدة البيانات.", "haiku", "ar-eg", "negation"),
    ("أضف تلميحًا لزر الحفظ دون تغيير مخطط قاعدة البيانات أو أي ترحيل.", "sonnet", "ar-msa", "negation"),
    ("ضيف tooltip لزرار الحفظ من غير ما تغيّر الـ schema ولا أي migration.", "sonnet", "mixed", "negation"),

    # ---------------------------------------------------------------- prose that needs deep reasoning
    ("اكتب وثيقة تصميم لنقل المصادقة إلى OAuth2 مع مقارنة الخيارات المتاحة.", "opus", "ar-msa", "writing"),
    ("اكتب خطة ترحيل لنقل قاعدة بيانات الطلبات إلى PostgreSQL دون توقف.", "opus", "ar-msa", "writing"),
]

HELD_OUT = [
    ("عدّل ملف CHANGELOG وأضف مدخلًا للإصدار 2.1.", "haiku", "ar-msa", "docs"),
    ("ظبّط صياغة رسالة الخطأ اللي بتظهر لما الملف يبقى كبير.", "haiku", "ar-eg", "docs"),
    ("اكتب شرح مختصر للـ API في ملف docs/api.md.", "haiku", "mixed", "docs"),
    ("شيل المسافات الزيادة من ملفات الـ markdown.", "haiku", "ar-eg", "docs"),
    ("أضف ملاحظة في README عن متطلبات التشغيل.", "haiku", "ar-msa", "docs"),
    ("صحّح عنوان القسم الثاني في ملف الدليل.", "haiku", "ar-msa", "docs"),
    ("ضيف زرار لتصدير الجدول PDF في صفحة التقارير.", "sonnet", "ar-eg", "ui"),
    ("أنشئ صفحة لعرض تفاصيل الطلب مع إمكانية تعديل العنوان.", "sonnet", "ar-msa", "ui"),
    ("اعمل API endpoint لحذف المنتج مع التحقق من الصلاحية.", "sonnet", "mixed", "feature"),
    ("اكتب اختبارات تكامل لواجهة المنتجات.", "sonnet", "ar-msa", "feature"),
    ("الفورم بيقبل تاريخ غلط، ضيف validation.", "sonnet", "ar-eg", "ui"),
    ("أضف خاصية الفرز حسب التاريخ في قائمة المهام.", "sonnet", "ar-msa", "ui"),
    ("اعمل component جديد للـ notifications في الـ navbar.", "sonnet", "mixed", "ui"),
    ("عالج الحالة اللي الملف المرفوع فيها ملوش امتداد.", "sonnet", "ar-eg", "feature"),
    ("حلّل سبب بطء الاستعلامات في تقرير المبيعات وعالج عنق الزجاجة.", "opus", "ar-msa", "debugging"),
    ("في مشكلة concurrency في الـ order processing بتخلي الطلب يتسجل مرتين.", "opus", "mixed", "debugging"),
    ("أعد كتابة طبقة الوصول للبيانات لتستخدم نمط المستودع في جميع الوحدات.", "opus", "ar-msa", "architecture"),
    ("اعمل migration لتغيير نوع عمود السعر في جدول المنتجات في الإنتاج بدون فقدان بيانات.", "opus", "mixed", "database"),
    ("صمّم نظام مصادقة يدعم SSO وتسجيل الدخول الثنائي.", "opus", "ar-msa", "security"),
    ("اليوزرز بيتعملهم logout لوحدهم ساعات، ومش عارفين نكرر المشكلة.", "opus", "ar-eg", "debugging"),
    ("حوّل التطبيق من monolith لـ microservices.", "opus", "mixed", "architecture"),
    ("ابحث لماذا تتلف البيانات عند المزامنة بين الخادمين.", "opus", "ar-msa", "debugging"),
    ("صلّح المشكلة.", "sonnet", "ar-eg", "short"),
    ("حسّن الأداء.", "sonnet", "ar-msa", "short"),
]


# Written after the first rules were frozen, in different words, and scored once before
# any further change (see the top of this file). Tuned against since then.
FRESH = [
    ("ممكن تعدل ملف الـ README وتكتب فيه خطوات تشغيل المشروع؟", "haiku", "ar-eg", "docs"),
    ("راجع التعليقات في الكود وصحح الأخطاء اللغوية فيها.", "haiku", "ar-msa", "docs"),
    ("اكتب وصف مختصر لكل متغير في ملف الإعدادات.", "haiku", "ar-eg", "docs"),
    ("غيّر اسم الملف من old_utils.py لـ helpers.py.", "haiku", "mixed", "docs"),
    ("حدّث رقم الإصدار في ملف package.json.", "haiku", "ar-msa", "docs"),
    ("أضف مثالًا للاستخدام في ملف التوثيق.", "haiku", "ar-msa", "docs"),
    ("اكتب README للـ API الجديد.", "haiku", "mixed", "docs"),
    ("محتاج شاشة تسجيل دخول فيها حقل للإيميل وكلمة السر وزرار دخول.", "sonnet", "ar-eg", "ui"),
    ("أضف عمودًا جديدًا في جدول المستخدمين في الواجهة لعرض تاريخ آخر دخول.", "sonnet", "ar-msa", "ui"),
    ("الصفحة الرئيسية مش بتتحمّل على الموبايل كويس، ظبّط الـ CSS.", "sonnet", "ar-eg", "ui"),
    ("اعمل زرار يمسح كل الفلاتر في صفحة البحث.", "sonnet", "ar-eg", "ui"),
    ("أنشئ خدمة لإرسال إشعارات البريد الإلكتروني عند إنشاء طلب جديد.", "sonnet", "ar-msa", "feature"),
    ("اكتب تيستات للـ login form.", "sonnet", "mixed", "feature"),
    ("عدّل دالة حساب الضريبة لتدعم نسبة مختلفة لكل دولة.", "sonnet", "ar-msa", "feature"),
    ("ضيف تحقق إن رقم التليفون 11 رقم.", "sonnet", "ar-eg", "feature"),
    ("في خطأ في ترتيب النتائج في صفحة المنتجات.", "sonnet", "ar-msa", "feature"),
    ("ضيف caching للـ product list باستخدام Redis.", "sonnet", "mixed", "feature"),
    ("اعمل integration مع Stripe للدفع وخلّي الـ webhooks تتعامل مع الـ retries.", "sonnet", "mixed", "feature"),
    ("السيرفر بيقع كل كام يوم ومحدش عارف ليه.", "opus", "ar-eg", "debugging"),
    ("عندنا تضارب في البيانات لما اتنين يعدلوا نفس الطلب في نفس الوقت.", "opus", "ar-eg", "debugging"),
    ("صمم هيكل قاعدة بيانات لنظام حجز تذاكر يدعم ملايين المستخدمين.", "opus", "ar-msa", "database"),
    ("نحتاج إلى نقل قاعدة البيانات من MySQL إلى PostgreSQL مع الحفاظ على البيانات.", "opus", "ar-msa", "database"),
    ("راجع نظام الصلاحيات كله وتأكد إن مفيش مستخدم يقدر يشوف بيانات مستخدم تاني.", "opus", "ar-eg", "security"),
    ("التطبيق بيستهلك رام كتير وبيزيد مع الوقت لحد ما يقع.", "opus", "ar-eg", "debugging"),
    ("أعد بناء نظام الفواتير بالكامل ليدعم عملات متعددة وضرائب مختلفة.", "opus", "ar-msa", "architecture"),
    ("ثغرة أمنية: أي مستخدم يقدر يعدّل طلبات غيره عن طريق تغيير الـ id في الرابط.", "opus", "ar-eg", "security"),
    ("الـ API بقى بطيء جدًا بعد آخر release ومش عارفين السبب.", "opus", "mixed", "debugging"),
    ("وحّد طريقة التعامل مع الأخطاء في كل الخدمات.", "opus", "ar-msa", "architecture"),
    ("اعمل upgrade للمشروع من .NET Framework 4.8 لـ .NET 8.", "opus", "mixed", "architecture"),
    ("الـ checkout بيرجع 500 error مرة كل عشر مرات تقريبًا.", "opus", "mixed", "debugging"),
]
