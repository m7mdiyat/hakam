"""responseSchema objects + closed taxonomies for the judge ensemble.

Design invariants encoded here:
- propertyOrdering everywhere: analysis text is generated BEFORE scores, scores
  before fallacies, and winner dead last (reason-before-decide).
- Probe schemas come in two variants that only differ in which label's sections
  come first — matching the flipped claims block in the prompt (the docs warn
  schema order and prompt order must agree).
- The fallacy `type` is a closed enum; Arabic/English display names resolve
  from FALLACY_NAMES server-side, so the model can never invent a fallacy.
- Labels here ("a"/"b") mean the probe's «أ»/«ب» — the server owns un-mapping
  to real debaters. Turn refs are neutral ("t1".."tN") so nothing in the input
  or output leaks which real debater is which.
"""
from __future__ import annotations

AXES = ["logic", "relevance", "rebuttal", "clarity", "composure"]

AXIS_NAMES_AR = {
    "logic": "الاتساق المنطقي",
    "relevance": "الالتزام بالموضوع",
    "rebuttal": "الرد على النقاط",
    "clarity": "الوضوح",
    "composure": "الهدوء والعقلانية",
}

# type slug -> (name_ar, name_en). Closed list — no "other" escape hatch.
FALLACY_NAMES = {
    "ad_hominem": ("الشخصنة", "Ad Hominem"),
    "straw_man": ("رجل القش", "Straw Man"),
    "appeal_to_emotion": ("الاحتكام إلى العاطفة", "Appeal to Emotion"),
    "appeal_to_authority": ("الاحتكام إلى السلطة", "Appeal to Authority"),
    "ad_populum": ("الاحتكام إلى الأغلبية", "Ad Populum"),
    "hasty_generalization": ("التعميم المتسرع", "Hasty Generalization"),
    "slippery_slope": ("المنحدر الزلق", "Slippery Slope"),
    "false_dilemma": ("المعضلة الزائفة", "False Dilemma"),
    "begging_the_question": ("المصادرة على المطلوب", "Begging the Question"),
    "post_hoc": ("ما بعده فهو بسببه", "Post Hoc"),
    "red_herring": ("التشتيت", "Red Herring"),
    "tu_quoque": ("وأنت كذلك", "Tu Quoque"),
    "appeal_to_ignorance": ("الاحتكام إلى الجهل", "Appeal to Ignorance"),
    "poisoning_the_well": ("تسميم البئر", "Poisoning the Well"),
}
FALLACY_TYPES = list(FALLACY_NAMES)

# One-line Arabic definitions rendered into the probe prompt's closed list.
FALLACY_DEFS_AR = {
    "ad_hominem": "هجوم على شخص المتحدث بدل حجته",
    "straw_man": "تشويه حجة الخصم ثم الرد على الصورة المشوهة",
    "appeal_to_emotion": "استدرار المشاعر بدل تقديم دليل",
    "appeal_to_authority": "الاحتجاج بسلطة غير مختصة أو دون دليل",
    "ad_populum": "اعتبار انتشار الرأي دليلًا على صحته",
    "hasty_generalization": "تعميم من أمثلة قليلة لا تكفي",
    "slippery_slope": "افتراض سلسلة نتائج كارثية دون إثبات الروابط",
    "false_dilemma": "حصر الخيارات في اثنين مع وجود غيرهما",
    "begging_the_question": "افتراض صحة المطلوب إثباته ضمن المقدمات",
    "post_hoc": "اعتبار التعاقب الزمني وحده دليلًا على السببية",
    "red_herring": "طرح موضوع جانبي لصرف النقاش عن النقطة الأصلية",
    "tu_quoque": "رد الاتهام بدل الرد على الحجة («وأنت تفعل مثله»)",
    "appeal_to_ignorance": "اعتبار غياب الدليل على النفي إثباتًا",
    "poisoning_the_well": "تسفيه مصدر الحجة سلفًا لمنع سماعها",
}

SEVERITIES = ["low", "medium", "high"]
# Emotional-register fallacies feed the derived emotionality meter.
EMOTIONAL_FALLACIES = ("appeal_to_emotion", "ad_hominem")

# ---------------------------------------------------------------------------
# Verdict v2 — argument extraction (Phase A). The quoted/reconstructed premise
# distinction is TWO ARRAY TYPES: an implicit premise has no field that could
# hold a segment id, so an audio-anchor leak is structurally impossible.
# Models never mint argument ids — the server assigns them by array order.
_QUOTED = {
    "type": "OBJECT",
    "properties": {
        "segment_ids": {"type": "ARRAY", "items": {"type": "STRING"},
                        "description": "معرفات المقاطع مثل t2-03"},
        "quote": {"type": "STRING", "description": "اقتباس حرفي دون تغيير"},
    },
    "required": ["segment_ids", "quote"],
    "propertyOrdering": ["segment_ids", "quote"],
}

_SPOKEN_PREMISE = {
    "type": "OBJECT",
    "properties": {
        "segment_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
        "quote": {"type": "STRING", "description": "اقتباس حرفي دون تغيير"},
        "external": {"type": "BOOLEAN",
                     "description": "هل تستند إلى واقعة من خارج المناظرة؟"},
        "external_claim_ar": {"type": "STRING",
                              "description": "صياغة الادعاء الخارجي بجملة إن وُجد"},
    },
    "required": ["segment_ids", "quote", "external"],
    "propertyOrdering": ["segment_ids", "quote", "external", "external_claim_ar"],
}

_IMPLICIT_PREMISE = {
    "type": "OBJECT",
    "properties": {
        "why_needed_ar": {"type": "STRING", "description": "لماذا يلزم هذه الحجةَ افتراضُها"},
        "text_ar": {"type": "STRING", "description": "المقدمة غير المنطوقة بصياغتك"},
    },
    "required": ["why_needed_ar", "text_ar"],
    "propertyOrdering": ["why_needed_ar", "text_ar"],
}

_ARGUMENT = {
    "type": "OBJECT",
    "properties": {
        "rebuts_segments": {"type": "ARRAY", "items": {"type": "STRING"},
                            "description": "مقاطع نقطة الخصم التي ترد عليها؛ فارغة إن لم تكن ردًا"},
        "conclusion": _QUOTED,
        "premises": {"type": "ARRAY", "items": _SPOKEN_PREMISE},
        "implicit_premises": {"type": "ARRAY", "items": _IMPLICIT_PREMISE},
        "classification": {
            "type": "OBJECT",
            "properties": {
                "rationale_ar": {"type": "STRING"},
                "type": {"type": "STRING", "enum": ["deductive", "inductive"]},
                "tentative": {"type": "BOOLEAN"},
            },
            "required": ["rationale_ar", "type", "tentative"],
            "propertyOrdering": ["rationale_ar", "type", "tentative"],
        },
        "weight": {"type": "STRING", "enum": ["primary", "secondary"]},
    },
    "required": ["rebuts_segments", "conclusion", "premises",
                 "implicit_premises", "classification", "weight"],
    "propertyOrdering": ["rebuts_segments", "conclusion", "premises",
                         "implicit_premises", "classification", "weight"],
}

EXTRACTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "arguments": {"type": "ARRAY", "items": _ARGUMENT,
                      "description": "بحد أقصى 4، الرئيسية أولًا"},
        "unsupported_assertions": {"type": "ARRAY", "items": _QUOTED,
                                   "description": "آراء أُعلنت بلا مقدمات تدعمها"},
        "orphan_premises": {"type": "ARRAY", "items": _QUOTED,
                            "description": "شواهد ومقدمات لم تصل إلى نتيجة"},
    },
    "required": ["arguments", "unsupported_assertions", "orphan_premises"],
    "propertyOrdering": ["arguments", "unsupported_assertions", "orphan_premises"],
}

ARG_CAP = 4


_AXIS = {
    "type": "OBJECT",
    "properties": {
        "analysis": {"type": "STRING",
                     "description": "أجب عن أسئلة المعيار المرقمة بإيجاز ثم علّل الدرجة"},
        "score": {"type": "INTEGER", "minimum": 0, "maximum": 100},
    },
    "required": ["analysis", "score"],
    "propertyOrdering": ["analysis", "score"],
}

_AXES_OBJ = {
    "type": "OBJECT",
    "properties": {k: _AXIS for k in AXES},
    "required": list(AXES),
    "propertyOrdering": list(AXES),
}


def _fallacy_item(turn_ids: list) -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "speaker": {"type": "STRING", "enum": ["a", "b"],
                        "description": "من ارتكب المغالطة: a=«أ» b=«ب»"},
            "turn": {"type": "STRING", "enum": list(turn_ids)},
            "segment_ids": {"type": "ARRAY", "items": {"type": "STRING"},
                            "description": "معرفات المقاطع مثل t2-03"},
            "quote": {"type": "STRING",
                      "description": "اقتباس حرفي من النص دون أي تغيير"},
            "explanation_ar": {"type": "STRING",
                               "description": "لماذا يُعد هذا الاقتباس تحديدًا مغالطة"},
            "fallacy_type": {"type": "STRING", "enum": list(FALLACY_TYPES)},
            "severity": {"type": "STRING", "enum": list(SEVERITIES)},
        },
        "required": ["speaker", "turn", "segment_ids", "quote",
                     "explanation_ar", "fallacy_type", "severity"],
        # locate -> quote -> explain -> only THEN classify and weigh
        "propertyOrdering": ["speaker", "turn", "segment_ids", "quote",
                             "explanation_ar", "fallacy_type", "severity"],
    }


def _dropped_item(turn_ids: list) -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "raised_turn": {"type": "STRING", "enum": list(turn_ids)},
            "segment_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "point_ar": {"type": "STRING", "description": "النقطة التي بقيت بلا رد، بجملة واحدة"},
            "speaker": {"type": "STRING", "enum": ["a", "b"],
                        "description": "من تركها بلا رد"},
        },
        "required": ["raised_turn", "segment_ids", "point_ar", "speaker"],
        "propertyOrdering": ["raised_turn", "segment_ids", "point_ar", "speaker"],
    }


def probe_schema(turn_ids: list, label_order: str = "ab") -> dict:
    """The judge-probe schema. `label_order` flips which label's axes are
    generated first, mirroring the claims order in that probe's prompt."""
    order = ["a", "b"] if label_order == "ab" else ["b", "a"]
    return {
        "type": "OBJECT",
        "properties": {
            "axes": {
                "type": "OBJECT",
                "properties": {"a": _AXES_OBJ, "b": _AXES_OBJ},
                "required": ["a", "b"],
                "propertyOrdering": order,
            },
            "fallacies": {"type": "ARRAY", "items": _fallacy_item(turn_ids)},
            "dropped_points": {"type": "ARRAY", "items": _dropped_item(turn_ids)},
            "winner": {"type": "STRING", "enum": ["a", "b"]},
            "confidence": {"type": "STRING", "enum": ["low", "medium", "high"]},
        },
        "required": ["axes", "fallacies", "dropped_points", "winner", "confidence"],
        "propertyOrdering": ["axes", "fallacies", "dropped_points",
                             "winner", "confidence"],
    }


_PROFILE = {
    "type": "OBJECT",
    "properties": {
        "strongest_ar": {"type": "STRING"},
        "weakest_ar": {"type": "STRING"},
        "tip_ar": {"type": "STRING"},
    },
    "required": ["strongest_ar", "weakest_ar", "tip_ar"],
    "propertyOrdering": ["strongest_ar", "weakest_ar", "tip_ar"],
}


def synthesis_schema(turn_ids: list) -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "key_moment": {
                "type": "OBJECT",
                "properties": {
                    "turn": {"type": "STRING", "enum": list(turn_ids)},
                    "segment_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "description_ar": {"type": "STRING"},
                },
                "required": ["turn", "segment_ids", "description_ar"],
                "propertyOrdering": ["turn", "segment_ids", "description_ar"],
            },
            "profiles": {
                "type": "OBJECT",
                "properties": {"a": _PROFILE, "b": _PROFILE},
                "required": ["a", "b"],
                "propertyOrdering": ["a", "b"],
            },
            "reasoning_ar": {"type": "STRING",
                             "description": "جملتان كحد أقصى، بما يتسق مع النتيجة المعطاة حصرًا"},
        },
        "required": ["key_moment", "profiles", "reasoning_ar"],
        "propertyOrdering": ["key_moment", "profiles", "reasoning_ar"],
    }
