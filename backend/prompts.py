"""Prompt texts for the Gemini calls, in Arabic.

Kept apart from the calling code so wording changes are reviewable on their own.
Judge prompts (probe + synthesis) arrive with the judging slice.
"""

# Transcription (per turn, Flash, thinking off). The segments are the app's
# time authority: fallacy audio-proof anchors resolve against them, so the
# instructions insist on short sentence-level segments with tight timestamps.
# Dialect is kept verbatim by design — the judge rubric treats dialect as fully
# valid speech, and quotes must match what a listener actually hears.
TRANSCRIBE_PROMPT = """\
انسخ هذا التسجيل الصوتي، وهو مداخلة في مناظرة موضوعها: «{topic}».

القواعد:
- اكتب ما قيل حرفيًا وباللهجة كما نُطقت، دون تصحيح لغوي ودون تحويل إلى الفصحى.
- قسّم الكلام إلى مقاطع قصيرة: جملة واحدة أو وحدة كلام متصلة في كل مقطع، وبحد أقصى ١٥ ثانية للمقطع الواحد.
- حدّد زمن بداية ونهاية كل مقطع بدقة بصيغة MM:SS.
- لا تكتب شيئًا غير كلام المتحدث: لا وصف أصوات، ولا تعليقات، ولا علامات مثل [موسيقى].
- إن كان مقطع ما غير مسموع فاكتب نصه «غير واضح» ولا تخترع كلامًا.
"""
