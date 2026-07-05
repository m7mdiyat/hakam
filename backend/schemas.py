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

# Verdict v2 — internal-soundness taxonomy (closed; closed is what makes
# cross-probe consensus clustering well-defined, exactly like fallacies).
SOUNDNESS_NAMES = {
    "self_contradiction": "تناقض ذاتي",
    "unsupported_load_bearing": "ادعاء مفصلي بلا سند",
    "premise_conclusion_drift": "انزياح عن المقدمات",
    "claim_abandonment": "التخلي عن الدعوى",
}
SOUNDNESS_TYPES = list(SOUNDNESS_NAMES)

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


def _arg_eval(arg_ids: list) -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "argument_id": {"type": "STRING", "enum": list(arg_ids)},
            "analysis_ar": {"type": "STRING", "description": "حلّل قبل أن تحكم"},
            "verdict": {"type": "STRING", "enum": ["valid", "invalid", "strong", "weak"],
                        "description": "valid/invalid للاستنباطي، strong/weak للاستقرائي"},
            "failure_point_ar": {"type": "STRING",
                                 "description": "موضع الخلل بدقة إن كان الحكم سلبيًا، وإلا فارغ"},
            "classification_agree": {"type": "BOOLEAN"},
            "alt_classification": {"type": "STRING", "enum": ["deductive", "inductive"],
                                   "description": "التصنيف البديل إن خالفت؛ كرر الحالي إن وافقت"},
            "rebuttal_effect": {"type": "STRING",
                                "enum": ["defeated", "weakened", "unaffected", "not_applicable"],
                                "description": "أثر هذه الحجة في هدفها إن كانت ردًا"},
        },
        "required": ["argument_id", "analysis_ar", "verdict", "failure_point_ar",
                     "classification_agree", "alt_classification", "rebuttal_effect"],
        "propertyOrdering": ["argument_id", "analysis_ar", "verdict", "failure_point_ar",
                             "classification_agree", "alt_classification", "rebuttal_effect"],
    }


def _preemption_item(arg_ids: list) -> dict:
    """التحصين المسبق: the opponent's EARLIER speech already answered a
    late-turn argument nobody could rebut. Receipt discipline as fallacies:
    verbatim quote + segment ids, server-validated (owner + strictly earlier
    turn + answerability) before anything scores."""
    return {
        "type": "OBJECT",
        "properties": {
            "argument_id": {"type": "STRING", "enum": list(arg_ids),
                            "description": "الحجة المتأخرة التي عولجت مسبقًا"},
            "quote": {"type": "STRING",
                      "description": "اقتباس حرفي من كلام الخصم السابق للحجة"},
            "segment_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
            "explanation_ar": {"type": "STRING",
                               "description": "وجه المعالجة المسبقة بإيجاز"},
            "effect": {"type": "STRING", "enum": ["defeated", "weakened"],
                       "description": "أثر المعالجة المسبقة في الحجة"},
        },
        "required": ["argument_id", "quote", "segment_ids", "explanation_ar", "effect"],
        "propertyOrdering": ["argument_id", "quote", "segment_ids",
                             "explanation_ar", "effect"],
    }


def _soundness_item(arg_ids: list) -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "speaker": {"type": "STRING", "enum": ["a", "b"]},
            "argument_id": {"type": "STRING", "enum": list(arg_ids) + ["none"],
                            "description": "الحجة المرتبطة أو none"},
            "quotes": {"type": "ARRAY", "items": _QUOTED,
                       "description": "اقتباس واحد؛ اقتباسان للتناقض الذاتي"},
            "explanation_ar": {"type": "STRING"},
            "type": {"type": "STRING", "enum": list(SOUNDNESS_TYPES)},
        },
        "required": ["speaker", "argument_id", "quotes", "explanation_ar", "type"],
        "propertyOrdering": ["speaker", "argument_id", "quotes", "explanation_ar", "type"],
    }


_EXTRACTION_ISSUE = {
    "type": "OBJECT",
    "properties": {
        "kind": {"type": "STRING", "enum": ["missed_argument", "misread_argument"]},
        "segment_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
        "note_ar": {"type": "STRING"},
    },
    "required": ["kind", "segment_ids", "note_ar"],
    "propertyOrdering": ["kind", "segment_ids", "note_ar"],
}


def probe_schema(turn_ids: list, arg_ids: list, label_order: str = "ab") -> dict:
    """Judge-probe schema v2: evaluates the shared argument map. `label_order`
    flips which label's axes come first, mirroring that probe's claims order.
    dropped_points is GONE — «بقيت بلا رد» is derived from the rebuttal map."""
    order = ["a", "b"] if label_order == "ab" else ["b", "a"]
    fallacy = _fallacy_item(turn_ids)
    fallacy["properties"]["argument_id"] = {
        "type": "STRING", "enum": list(arg_ids) + ["none"],
        "description": "الحجة التي تطعن فيها المغالطة أو none"}
    fallacy["required"] = fallacy["required"] + ["argument_id"]
    fallacy["propertyOrdering"] = fallacy["propertyOrdering"][:-1] + \
        ["argument_id", fallacy["propertyOrdering"][-1]]
    return {
        "type": "OBJECT",
        "properties": {
            "argument_evals": {"type": "ARRAY", "items": _arg_eval(arg_ids)},
            "preemptions": {"type": "ARRAY", "items": _preemption_item(arg_ids)},
            "soundness": {"type": "ARRAY", "items": _soundness_item(arg_ids)},
            "fallacies": {"type": "ARRAY", "items": fallacy},
            "extraction_issues": {"type": "ARRAY", "items": _EXTRACTION_ISSUE},
            "axes": {
                "type": "OBJECT",
                "properties": {"a": _AXES_OBJ, "b": _AXES_OBJ},
                "required": ["a", "b"],
                "propertyOrdering": order,
            },
            "winner": {"type": "STRING", "enum": ["a", "b"]},
            "confidence": {"type": "STRING", "enum": ["low", "medium", "high"]},
        },
        "required": ["argument_evals", "preemptions", "soundness", "fallacies",
                     "extraction_issues", "axes", "winner", "confidence"],
        "propertyOrdering": ["argument_evals", "preemptions", "soundness", "fallacies",
                             "extraction_issues", "axes", "winner", "confidence"],
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
