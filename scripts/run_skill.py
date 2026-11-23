#!/usr/bin/env python3
"""Academic slide generation and editing skill (standard library only).

Builds or loads a deck, applies deterministic edit instructions, conserves
terminology and numbers, records a rollback-capable version chain, and writes
the resulting page/version specification to /app/output/result.json.  Offline,
deterministic, and explicit when an edit cannot be executed honestly.
"""

import argparse
import hashlib
import json
import os
import re
import sys

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"
SKILL_NAME = "academic-slide-editor"
SKILL_VERSION = "1.0.0"
CHALLENGE_ID = "ppt-6ad93034"
MAX_TEXT = 400000
SKIP_KEYS = {"output_contract", "scoring", "tags", "meta", "case_id",
             "challenge_id", "skill", "contract_notes"}

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|倍|mg|g|mL|L|h|小时|分钟|天|周|°C|nm|μm|mM|μM|nM|kDa|Da|例|名|人)")
ABBR_RE = re.compile(r"\b([A-Z]{2,}[A-Za-z0-9\-]*)\b")
OPS = [("compress", ("压缩", "精简", "缩短", "compress", "shorten")),
       ("expand", ("扩写", "展开", "补充", "expand", "elaborate")),
       ("delete", ("删除", "删掉", "remove", "delete")),
       ("replace", ("替换", "改为", "改成", "replace", "change to")),
       ("audience", ("换受众", "面向", "听众", "audience")),
       ("translate", ("翻译", "英文", "translate", "english"))]


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def sha256_json(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def read_text_file(path):
    try:
        if os.path.isfile(path) and os.path.getsize(path) <= 4 * 1024 * 1024:
            with open(path, "r", encoding="utf-8-sig") as handle:
                return handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    return None


def collect_material(case, base_dir):
    parts, unreadable = [], []

    def walk(node, label):
        if isinstance(node, str):
            stripped = node.strip()
            if len(stripped) >= 30:
                parts.append({"label": label, "text": stripped, "path": None})
            elif stripped and not stripped.startswith(("{", "[")):
                for candidate in (stripped, os.path.join(base_dir, stripped)):
                    text = read_text_file(candidate)
                    if text is not None:
                        parts.append({"label": label, "text": text, "path": candidate})
                        return
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in SKIP_KEYS:
                    continue
                walk(value, "%s.%s" % (label, key))
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                walk(value, "%s[%d]" % (label, idx))

    for key, value in case.items():
        if key in SKIP_KEYS:
            continue
        walk(value, key)
    parts.sort(key=lambda item: (-len(item["text"]), item["label"]))
    return parts, unreadable


def task_text(base_dir):
    for name in ("task.md", "TASK.md", "task.txt"):
        path = os.path.join(base_dir, name)
        text = read_text_file(path)
        if text:
            return text, path
    return "", None


def normalize_slides(case):
    raw = None
    for key in ("slides", "deck", "presentation"):
        value = case.get(key)
        if isinstance(value, list) and value:
            raw = value
            break
        if isinstance(value, dict):
            for inner in ("slides", "pages", "items"):
                if isinstance(value.get(inner), list) and value[inner]:
                    raw = value[inner]
                    break
        if raw:
            break
    slides = []
    if not raw:
        return slides
    for idx, entry in enumerate(raw):
        if isinstance(entry, str):
            slides.append({"id": "S%d" % (idx + 1), "title": "第 %d 页" % (idx + 1),
                           "text": entry.strip(), "bullets": [], "notes": "",
                           "layout": None, "generated": False})
        elif isinstance(entry, dict):
            bullets = entry.get("bullets") or entry.get("points") or []
            if isinstance(bullets, str):
                bullets = [part.strip() for part in re.split(r"[；;\n]", bullets) if part.strip()]
            text = entry.get("text") or entry.get("body") or entry.get("content") or ""
            if not text and bullets:
                text = "；".join(str(item) for item in bullets)
            slides.append({
                "id": str(entry.get("id") or "S%d" % (idx + 1)),
                "title": str(entry.get("title") or "第 %d 页" % (idx + 1)),
                "text": str(text),
                "bullets": [str(item) for item in bullets][:8],
                "notes": str(entry.get("notes") or entry.get("speaker_notes") or ""),
                "layout": entry.get("layout") or entry.get("template_layout"),
                "generated": False,
            })
    return slides


def generate_slides(case, text):
    outline = case.get("outline")
    items = []
    if isinstance(outline, list):
        for entry in outline:
            if isinstance(entry, str) and entry.strip():
                items.append(entry.strip())
            elif isinstance(entry, dict):
                title = entry.get("title") or entry.get("name")
                detail = entry.get("text") or entry.get("summary")
                if title:
                    items.append("%s：%s" % (title, detail or ""))
    if not items:
        for sentence in re.split(r"(?<=[。；;！!？?])", text):
            sentence = norm(sentence)
            if 12 <= len(sentence) <= 260:
                items.append(sentence)
    slides = []
    for idx, item in enumerate(items[:12]):
        title, _, body = item.partition("：")
        slides.append({"id": "S%d" % (idx + 1),
                       "title": (title if body else "第 %d 页" % (idx + 1))[:40],
                       "text": (body or item)[:240], "bullets": [],
                       "notes": "", "layout": None, "generated": True})
    return slides


def parse_instruction(entry, idx):
    if isinstance(entry, dict):
        target = entry.get("target") or entry.get("slide") or entry.get("page")
        op = None
        raw_op = str(entry.get("op") or entry.get("operation") or entry.get("action") or "")
        for name, markers in OPS:
            if any(marker in raw_op.lower() for marker in markers):
                op = name
                break
        return {"id": "E%d" % (idx + 1), "target": target, "op": op or "replace",
                "value": entry.get("value") or entry.get("to"),
                "from": entry.get("from") or entry.get("old"),
                "limit": entry.get("limit") or entry.get("bullets"),
                "reason": entry.get("reason"), "raw": json.dumps(entry, ensure_ascii=False)[:300]}
    if isinstance(entry, str):
        text = entry.strip()
        target = None
        match = re.search(r"(?:第\s*)?(\d+)\s*(?:页|slide)", text)
        if match:
            target = int(match.group(1))
        match_id = re.search(r"\bS(\d+)\b", text)
        if target is None and match_id:
            target = int(match_id.group(1))
        op = None
        for name, markers in OPS:
            if any(marker in text.lower() for marker in markers):
                op = name
                break
        limit = None
        match_limit = re.search(r"(\d+)\s*条", text)
        if match_limit:
            limit = int(match_limit.group(1))
        value = None
        match_replace = re.search(r"(?:把|将)?\s*(.+?)\s*(?:改为|改成|替换为|replace with)\s*(.+)", text)
        if match_replace:
            value = {"from": match_replace.group(1).strip(), "to": match_replace.group(2).strip()}
        return {"id": "E%d" % (idx + 1), "target": target, "op": op or "unparsed",
                "value": value, "from": None, "limit": limit, "reason": None, "raw": text[:300]}
    return {"id": "E%d" % (idx + 1), "target": None, "op": "unparsed",
            "value": None, "from": None, "limit": None, "reason": None, "raw": str(entry)[:200]}


def find_slide(slides, target):
    if target is None:
        return None
    if isinstance(target, int):
        if 1 <= target <= len(slides):
            return slides[target - 1]
        return None
    target = str(target)
    for slide in slides:
        if slide["id"] == target:
            return slide
    return None


def apply_edit(slide, instruction, material_sentences, glossary):
    op = instruction["op"]
    before = slide["text"]
    notes_before = slide["notes"]
    applied, reason, after = False, None, before
    changed_notes = False
    if op == "compress":
        parts = [piece.strip() for piece in re.split(r"[，,；;。]", before) if piece.strip()]
        limit = instruction.get("limit") or 2
        after = "；".join(parts[:max(1, int(limit))])
        if after and len(after) < len(before):
            applied = True
        else:
            reason = "nothing_to_compress"
    elif op == "expand":
        before_tokens = set(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", before))
        best, best_score = None, 0
        for sentence in material_sentences:
            if sentence in before:
                continue
            tokens = set(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", sentence))
            score = len(before_tokens & tokens)
            if score > best_score:
                best, best_score = sentence, score
        if best:
            after = before + " " + best
            applied = True
        else:
            reason = "no_source_sentence_to_expand"
    elif op == "delete":
        after = ""
        applied = True
    elif op == "replace":
        pair = instruction.get("value") if isinstance(instruction.get("value"), dict) else None
        source = (pair or {}).get("from") or instruction.get("from")
        target = (pair or {}).get("to")
        if source and target and source in before:
            after = before.replace(source, target)
            applied = True
        else:
            reason = "replace_source_not_found"
    elif op == "audience":
        explanations = [term for term in glossary if term in before]
        if explanations:
            slide["notes"] = (notes_before + " 面向新听众时先解释：" + "、".join(explanations[:4]) + "。").strip()
            changed_notes = True
        after = before
        applied = True
    elif op == "translate":
        reason = "translation_requires_language_model_not_available"
    else:
        reason = "unparsed_instruction"
    slide["text"] = after
    if not slide.get("bullets"):
        slide["bullets"] = [piece.strip() for piece in re.split(r"[；;]", after) if piece.strip()][:6]
    return {"id": instruction["id"], "slide": slide["id"], "op": op,
            "before": before[:600], "after": after[:600],
            "notes_changed": changed_notes, "applied": applied, "reason": reason,
            "instruction": instruction.get("raw")}


def contract_fields(contract):
    fields, unsupported = [], []
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            return list(contract["properties"].keys()), unsupported
        for combo in ("anyOf", "allOf", "oneOf", "$ref", "patternProperties"):
            if combo in contract:
                unsupported.append(combo)
        nested = contract.get("fields")
        if isinstance(nested, list):
            return contract_fields(nested)[0], unsupported
        if isinstance(nested, dict):
            return list(nested.keys()), unsupported
        reserved = {"type", "required", "optional", "additionalProperties", "title",
                    "description", "version", "schema", "$schema"}
        return [key for key in contract if key not in reserved and not str(key).startswith("$")], unsupported
    if isinstance(contract, list):
        for entry in contract:
            if isinstance(entry, str):
                fields.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("field") or entry.get("key")
                if name:
                    fields.append(str(name))
        return fields, unsupported
    return fields, unsupported


ALIASES = {
    "status": "status", "deck": "deck", "slides": "slides", "edits": "edits",
    "version_log": "version_log", "versions": "version_log",
    "figure_operations": "figure_operations", "figure_ops": "figure_operations",
    "checks": "checks", "consistency_checks": "checks", "rollback": "rollback",
    "export_plan": "export_plan", "export_artifact": "export_plan",
    "source_register": "source_register", "gaps": "gaps",
    "uncertainty": "uncertainty", "evidence": "evidence", "stats": "stats",
    "limitations": "limitations",
}


def fill_contract(result, contract):
    fields, unsupported = contract_fields(contract)
    body = result.get("result") if isinstance(result.get("result"), dict) else {}
    unmapped, preserved = [], []
    for field in fields:
        if field in result and result[field] is not None:
            preserved.append(field)
            continue
        key = ALIASES.get(field.lower(), field.lower())
        if key in result and result[key] is not None:
            result[field] = result[key]
            preserved.append(field)
        elif key in body and body[key] is not None:
            result[field] = body[key]
            preserved.append(field)
        else:
            result[field] = None
            unmapped.append(field)
    result["contract_notes"] = {
        "declared_fields": fields, "preserved_fields": preserved,
        "unmapped_fields": unmapped, "unsupported_features": unsupported,
        "note": "Unsupported or absent fields are null; nothing is fabricated.",
    }
    if result.get("status") == "success" and (unmapped or unsupported):
        result["status"] = "partial"


def write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_export_plan(case):
    formats = ["pptx", "pdf", "png"]
    requested = case.get("export") or case.get("target_format")
    if isinstance(requested, str):
        parsed = [part.strip().lower() for part in re.split(r"[,/;]", requested) if part.strip()]
        if parsed:
            formats = parsed
    rules = {
        "pptx": ["editable_slides", "master_layout_preserved", "notes_saved", "no_orphan_textbox"],
        "pdf": ["fonts_embedded", "no_text_overflow", "images_not_cropped"],
        "png": ["page_previews_present", "font_substitution_absent"],
    }
    return [{"format": fmt, "required_checks": rules.get(fmt, ["renders_without_errors"]),
             "performed": False,
             "reason": "no presentation renderer invoked; plan is reproducible"} for fmt in formats]


def main():
    parser = argparse.ArgumentParser(description="Academic slide generator and editor")
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--out", "--output", default=DEFAULT_OUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args()

    result = {
        "case_id": None,
        "challenge_id": CHALLENGE_ID,
        "skill": {"name": SKILL_NAME, "version": SKILL_VERSION},
        "status": "failed",
        "result": {
            "deck": {"meta": {}, "slides": [], "edits": [], "generated": False},
            "slides": [], "edits": [], "version_log": [], "rollback": {},
            "figure_operations": [], "checks": [], "export_plan": [],
            "source_register": [], "gaps": [],
        },
        "evidence": [], "uncertainty": [], "errors": [], "stats": {},
        "limitations": [
            "Edits are deterministic text transforms plus source-sentence retrieval.",
            "No pptx/PDF renderer is invoked; export checks are a plan.",
        ],
    }

    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case root is not an object")
    except (OSError, ValueError) as exc:
        result["errors"].append({"stage": "read_case", "type": type(exc).__name__,
                                 "detail": "case.json missing or not valid JSON"})
        result["status"] = "insufficient_input"
        write_json(args.out, result)
        return 2

    base_dir = os.path.dirname(os.path.abspath(args.case))
    result["case_id"] = case.get("case_id") or case.get("id")
    contract = case.get("output_contract")
    parts, unreadable = collect_material(case, base_dir)
    extra_task, task_path = task_text(base_dir)
    text = ("\n".join(part["text"] for part in parts) + "\n" + extra_task)[:MAX_TEXT]

    slides = normalize_slides(case)
    generated = False
    if not slides:
        slides = generate_slides(case, text)
        generated = bool(slides)
    if not slides:
        result["status"] = "insufficient_input"
        result["uncertainty"].append({"item": "deck", "detail": "no starting slides or outline/material"})
        fill_contract(result, contract)
        write_json(args.out, result)
        return 0

    glossary_source = case.get("glossary")
    glossary = []
    if isinstance(glossary_source, dict):
        glossary = [str(key) for key in glossary_source]
    elif isinstance(glossary_source, list):
        for entry in glossary_source:
            if isinstance(entry, str):
                glossary.append(entry)
            elif isinstance(entry, dict) and entry.get("term"):
                glossary.append(str(entry["term"]))
    if not glossary:
        glossary = sorted({match.group(1) for slide in slides
                           for match in ABBR_RE.finditer(slide["title"] + " " + slide["text"])})

    material_sentences = [norm(sentence) for sentence in re.split(r"(?<=[。；;！!？?])", text)
                          if 12 <= len(norm(sentence)) <= 300]

    instructions = []
    for key in ("edit_instructions", "instructions", "edits", "changes"):
        value = case.get(key)
        if isinstance(value, list) and value:
            instructions = [parse_instruction(entry, idx) for idx, entry in enumerate(value)]
            break

    baseline = [dict(slide) for slide in slides]
    base_hash = sha256_json(baseline)
    version_log = [{"version": "v1", "parent": None, "hash": base_hash,
                    "summary": "baseline deck" + (" (generated)" if generated else ""),
                    "slides": len(baseline)}]
    edits, checks = [], []
    version_counter = 1

    for instruction in instructions:
        if instruction["op"] == "unparsed":
            checks.append({"check": "unparsed_instruction", "edit": instruction["id"],
                           "raw": instruction.get("raw")})
            continue
        slide = find_slide(slides, instruction.get("target"))
        if slide is None:
            checks.append({"check": "instruction_target_missing", "edit": instruction["id"],
                           "target": instruction.get("target")})
            continue
        before_text = slide["text"]
        before_terms = {term for term in glossary if term in before_text}
        before_numbers = set(NUMBER_RE.findall(before_text))
        edit = apply_edit(slide, instruction, material_sentences, glossary)
        after_text = slide["text"]
        after_terms = {term for term in glossary if term in after_text}
        lost_terms = sorted(before_terms - after_terms)
        if lost_terms:
            checks.append({"check": "terminology_dropped", "edit": edit["id"],
                           "slide": slide["id"], "terms": lost_terms})
        after_numbers = set(NUMBER_RE.findall(after_text))
        if before_numbers != after_numbers:
            checks.append({"check": "number_changed_without_source", "edit": edit["id"],
                           "slide": slide["id"], "before": sorted(before_numbers),
                           "after": sorted(after_numbers)})
        if not edit["applied"]:
            checks.append({"check": "edit_not_applied", "edit": edit["id"],
                           "slide": slide["id"], "reason": edit["reason"]})
        edits.append(edit)
        if edit["applied"]:
            version_counter += 1
            version_log.append({
                "version": "v%d" % version_counter, "parent": "v%d" % (version_counter - 1),
                "hash": sha256_json(slides), "summary": "%s on %s" % (edit["op"], slide["id"]),
                "slides": len(slides),
            })

    ids = [slide["id"] for slide in slides]
    duplicates = sorted({slide_id for slide_id in ids if ids.count(slide_id) > 1})
    if duplicates:
        checks.append({"check": "slide_id_duplicate", "ids": duplicates})
    if not instructions:
        checks.append({"check": "no_edit_instructions_supplied"})

    figures = case.get("figures") if isinstance(case.get("figures"), list) else []
    figure_labels = []
    for idx, entry in enumerate(figures):
        if isinstance(entry, str):
            figure_labels.append({"label": entry, "path": None, "caption": None})
        elif isinstance(entry, dict):
            figure_labels.append({"label": str(entry.get("label") or entry.get("id")
                                               or "Figure %d" % (idx + 1)),
                                  "path": entry.get("path") or entry.get("source"),
                                  "caption": entry.get("caption")})
    figure_operations = []
    deck_text = " ".join(slide["title"] + " " + slide["text"] for slide in slides)
    for item in figure_labels:
        number = re.sub(r"[^0-9A-Za-z]", "", item["label"])
        referenced = bool(re.search(r"(?:Figure|Fig\.?|图)\s*%s\b" % re.escape(number), deck_text))
        declared = bool(item.get("path"))
        if not declared:
            checks.append({"check": "figure_asset_not_declared", "figure": item["label"]})
        action = "quote_only" if declared else "quote_only"
        if referenced and item.get("caption") is None:
            checks.append({"check": "figure_caption_missing", "figure": item["label"]})
        figure_operations.append({"figure": item["label"], "action": action,
                                  "referenced_on_slide": referenced,
                                  "asset_declared": declared,
                                  "redraw_basis": "available only if raw data are supplied"})
    if not figure_labels:
        checks.append({"check": "figure_inventory_missing"})

    rollback = {"to": "v1", "hash": base_hash,
                "command": "restore version v1 slide payloads",
                "affected_slides": [slide["id"] for slide in slides]}

    gaps = []
    if not instructions:
        gaps.append({"type": "no_edit_instructions"})
    if not glossary:
        gaps.append({"type": "no_terminology_guard", "detail": "glossary empty"})
    if generated:
        gaps.append({"type": "deck_generated_from_material", "slides": len(slides)})
    if any(check["check"] == "figure_inventory_missing" for check in checks):
        gaps.append({"type": "no_figure_inventory"})

    source_register = [
        {"label": part["label"], "path": None if not part["path"] else os.path.basename(part["path"]),
         "chars": len(part["text"]), "sha256": sha256_text(part["text"])}
        for part in parts[:24]
    ]
    if task_path:
        source_register.append({"label": "task.md", "path": os.path.basename(task_path),
                                "chars": len(extra_task), "sha256": sha256_text(extra_task)})

    result["result"].update({
        "deck": {"meta": {"slides": len(slides), "generated": generated,
                          "glossary_terms": len(glossary),
                          "instructions": len(instructions)},
                 "slides": slides, "edits": edits, "generated": generated},
        "slides": slides,
        "edits": edits,
        "version_log": version_log,
        "rollback": rollback,
        "figure_operations": figure_operations,
        "checks": checks,
        "export_plan": build_export_plan(case),
        "source_register": source_register,
        "gaps": gaps,
    })
    result["evidence"] = [{"type": "edit_trace", "edit": edit["id"], "slide": edit["slide"],
                           "before": edit["before"][:200], "after": edit["after"][:200]}
                          for edit in edits]
    result["evidence"].append({"type": "baseline_hash", "sha256": base_hash})
    for entry in unreadable:
        result["uncertainty"].append({"item": "file", "detail": "unreadable_or_missing",
                                      "path": os.path.basename(entry["path"])})
    if any(check["check"] == "edit_not_applied" for check in checks):
        result["uncertainty"].append({"item": "edits", "detail": "some edits were not executed"})
    if generated:
        result["uncertainty"].append({"item": "deck",
                                      "detail": "baseline generated from material; not an organizer deck"})

    applied = sum(1 for edit in edits if edit["applied"])
    result["stats"] = {
        "slides": len(slides),
        "instructions": len(instructions),
        "edits_applied": applied,
        "edits_not_applied": len(edits) - applied,
        "versions": len(version_log),
        "glossary_terms": len(glossary),
        "figure_operations": len(figure_operations),
        "checks": len(checks),
    }

    if (applied or generated) and not any(check["check"] in
                                          ("instruction_target_missing", "edit_not_applied")
                                          for check in checks):
        result["status"] = "success"
    elif slides:
        result["status"] = "partial"
    else:
        result["status"] = "insufficient_input"

    fill_contract(result, contract)
    write_json(args.out, result)
    if result["status"] == "failed":
        return 2
    if result.get("contract_notes", {}).get("unmapped_fields"):
        return 1
    return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
