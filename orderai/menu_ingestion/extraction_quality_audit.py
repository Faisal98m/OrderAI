"""Offline PDF/draft quality audit and separate, publication-blocked human reviews."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from html import escape
import io
import json
from pathlib import Path
import re
from uuid import uuid4

import pymupdf
from orderai.paths import REPOSITORY_ROOT


ROOT = REPOSITORY_ROOT
OUTPUT = ROOT / "tests/output/quality_audit"
REVIEW_FIELDS = ["record_type", "item_id", "source_page", "extracted_name", "extracted_category",
    "extracted_price", "extracted_currency", "decision", "verified_name", "verified_category",
    "verified_price", "verified_currency", "evidence", "reviewer", "notes"]
OWNER_TYPES = {
    "Pricing and currency": {"missing_price", "missing_currency", "missing_menu_currency", "unknown_choice_price_or_currency"},
    "Names, categories and descriptions": {"missing_name", "missing_description", "category_description_disagreement", "possible_duplicate_products"},
    "Customer choices and references": {"potential_missing_option_groups", "relationship_ambiguity", "selection_limit_review",
        "unknown_choices", "unresolved_reference", "cross_page_reference_review", "stage2_product_failure"},
    "Pipeline and publication": {"multipage_experimental_draft", "publication_review_required", "schema_incompatibility", "incomplete_ingestion"},
}


class AuditError(ValueError):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def norm(text):
    return " ".join((text or "").casefold().split())


def load_complete(pdf, draft_path=None, draft_dir=None):
    if draft_path is None:
        candidates = sorted((draft_dir or ROOT / "tests/output/multipage_pdf").glob("complete_draft_*.json"),
                            key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    else:
        candidates = [Path(draft_path)]
    for path in candidates:
        draft = json.loads(path.read_text(encoding="utf-8-sig"))
        if draft.get("completion_status") != "complete":
            continue
        if (draft.get("draft_version") != "visual_pdf_multipage_v2"
                or draft.get("menu", {}).get("import_status") != "needs_review"
                or draft.get("validation", {}).get("ready_to_publish") is not False):
            raise AuditError("Only complete, unpublished v2 experimental drafts are supported.")
        if sha(pdf) != draft["menu"]["source"].get("pdf_sha256"):
            raise AuditError("PDF SHA-256 does not match the most recent complete draft; no fallback to older drafts.")
        return path.resolve(), draft
    raise AuditError("No complete multipage draft found.")


def inventory(draft):
    rows, seen = [], set()
    for category in draft["menu"]["categories"]:
        for item in category["items"]:
            if item["id"] in seen:
                raise AuditError("Duplicate product ID in consolidated draft.")
            seen.add(item["id"])
            sources = [s for s in category.get("category_sources", []) if s["source_page"] == item["source_page"]]
            rows.append({"item_id": item["id"], "source_page": item["source_page"],
                "category": category["name"], "original_page_categories": [s["name"] for s in sources],
                "name": item.get("name"), "description": item.get("description"),
                "price": item.get("price"), "currency": item.get("currency"),
                "source_text": item.get("source_text"), "stage2_status": item.get("relationship_stage_status", "not_completed"),
                "stage2_outcome": item.get("relationship_audit", {}).get("outcome"),
                "stage2_classification": item.get("relationship_audit", {}).get("classification"),
                "stage1_option_groups": item.get("option_groups", []),
                "stage2_option_groups": item.get("relationship_interpretation", {}).get("option_groups", []),
                "stage2_fixed_components": item.get("relationship_interpretation", {}).get("fixed_components", []),
                "stage2_ambiguities": item.get("relationship_interpretation", {}).get("ambiguities", []),
                "stage2_unresolved_relationships": item.get("relationship_interpretation", {}).get("unresolved_relationships", [])})
    # Verify that per-page originals refer to the same immutable consolidated products.
    originals = {i["id"]: i for p in draft["pages"] for c in p["stage1_draft"]["menu"]["categories"] for i in c["items"]}
    original_count = sum(len(c["items"]) for p in draft["pages"] for c in p["stage1_draft"]["menu"]["categories"])
    if set(originals) != seen or original_count != len(originals):
        raise AuditError("Consolidated IDs differ from page-level Stage 1 products.")
    for row in rows:
        original = originals[row["item_id"]]
        for field in ("name", "description", "price", "currency", "source_text", "source_page"):
            if row[field] != original.get(field):
                raise AuditError("Consolidated product changed an immutable Stage 1 source field.")
        if row["stage1_option_groups"] != original.get("option_groups", []):
            raise AuditError("Consolidated Stage 1 option groups differ from original groups.")
    return sorted(rows, key=lambda r: (r["source_page"], norm(r["category"]), r["item_id"]))


def quality_findings(rows):
    findings = []
    def add(row, kind, owner, **details):
        record = {"type": kind, "owner_category": owner, "item_id": row["item_id"],
                  "product": row["name"], "source_page": row["source_page"],
                  "requires_human_review": True, **details}
        record["finding_id"] = "audit_" + hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:20]
        findings.append(record)
    for row in rows:
        s1, s2 = row["stage1_option_groups"], row["stage2_option_groups"]
        used = set()
        for index, group in enumerate(s1):
            names = {norm(c.get("name")) for c in group.get("choices", []) if c.get("name")}
            tokens = set(re.findall(r"\w+", norm(group.get("name")))) - {"choice", "choose", "of", "from", "the", "your", "or"}
            matches = []
            for j, other in enumerate(s2):
                other_names = {norm(c.get("name")) for c in other.get("choices", []) if c.get("name")}
                other_tokens = set(re.findall(r"\w+", norm(other.get("name"))))
                if group.get("kind") == other.get("kind") and (names & other_names or tokens & other_tokens):
                    matches.append(j)
            used.update(matches)
            if not matches:
                add(row, "stage1_group_not_matched_in_stage2", "Customer choices and references",
                    stage1_group_index=index, stage1_group=group,
                    note="No lexical candidate in Stage 2; Stage 1 may itself be incorrect. Compare PDF.")
            else:
                target_names = {norm(c.get("name")) for j in matches for c in s2[j].get("choices", []) if c.get("name")}
                if names - target_names:
                    add(row, "stage1_choices_not_preserved", "Customer choices and references",
                        stage1_group_index=index, candidate_stage2_indexes=matches, missing_choice_labels=sorted(names - target_names))
                if not any(group == s2[j] for j in matches):
                    add(row, "group_structure_changed", "Customer choices and references",
                        stage1_group_index=index, candidate_stage2_indexes=matches,
                        note="Candidate correspondence only; neither stage is automatically accepted.")
        for index in set(range(len(s2))) - used:
            add(row, "stage2_group_added", "Customer choices and references", stage2_group_index=index,
                note="New proposal; verify against original PDF, not just model evidence.")
        text = "\n".join(row.get(k) or "" for k in ("description", "source_text"))
        if not s2 and re.search(r"\b(?:choose|choice|select|either|or|flavour|flavor)\b", text, re.I):
            add(row, "selection_wording_without_stage2_groups", "Customer choices and references", source_evidence=text)
        if not row["stage2_fixed_components"] and re.search(r"\b(?:topped with|includes|served with)\b", text, re.I):
            add(row, "inclusion_wording_without_fixed_components", "Portions and included components", source_evidence=text,
                note="May already be explained as uncertain; this is a comparison flag, not a proven omission.")
        last_word = re.findall(r"\w+", norm(row["name"]))
        portion = None
        if last_word:
            portion = re.search(r"\b(\d+)\s+" + re.escape(last_word[-1]) + r"\b", text, re.I)
        if portion:
            quantity = int(portion.group(1))
            components = [c for c in row["stage2_fixed_components"] if c.get("quantity") == quantity
                          and norm(c.get("name")) in norm(row["name"])]
            add(row, "possible_portion_as_component" if components else "portion_retained_in_source",
                "Portions and included components", source_evidence=portion.group(0),
                representation="fixed_component" if components else "source_only", components=components,
                note="Numeric wording matching the product's final word suggests a portion. Human confirmation required.")
        for index, component in enumerate(row["stage2_fixed_components"]):
            if norm(component.get("name")) == norm(row["name"]):
                add(row, "self_referential_component", "Portions and included components", component_index=index)
    return findings


def write_json(path, data):
    with path.open("x", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False, allow_nan=False)


def write_csv(path, rows, fields):
    with path.open("x", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            values = {k: json.dumps(row[k], ensure_ascii=False) if isinstance(row.get(k), (list, dict))
                      else row.get(k) for k in fields}
            # Prevent source text being executed as spreadsheet formulas.
            values = {k: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                      else value for k, value in values.items()}
            writer.writerow(values)


def generate(pdf, draft_path=None, draft_dir=None, output_root=None):
    pdf = Path(pdf).resolve()
    draft_path, draft = load_complete(pdf, draft_path, draft_dir)
    original_raw = draft_path.read_bytes()
    original_sha = hashlib.sha256(original_raw).hexdigest()
    if json.loads(original_raw.decode("utf-8-sig")) != draft:
        raise AuditError("Draft changed while loading.")
    rows = inventory(draft)
    findings = quality_findings(rows)
    with pymupdf.open(pdf) as document:
        if document.needs_pass or document.page_count != draft["menu"]["source"]["page_count"]:
            raise AuditError("PDF page count/encryption does not match the draft.")
        if {p["source_page"] for p in draft["pages"]} != set(range(1, document.page_count + 1)):
            raise AuditError("Page provenance is incomplete.")
        folder = (output_root or OUTPUT) / ("audit_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8])
        folder.mkdir(parents=True, exist_ok=False)
        images = []
        for number, page in enumerate(document, 1):
            name = f"page_{number:02}.png"
            page.get_pixmap(dpi=160).save(folder / name)
            images.append(name)
    if sha(pdf) != draft["menu"]["source"]["pdf_sha256"] or sha(draft_path) != original_sha:
        raise AuditError("PDF/draft changed during rendering; audit incomplete.")
    # Complete original audit is preserved verbatim, not reduced to the active queue.
    (folder / "original_audit.json").write_bytes(original_raw)
    write_json(folder / "inventory.json", rows)
    write_csv(folder / "inventory.csv", rows, ["source_page", "category", "item_id", "name", "description", "price", "currency",
        "source_text", "stage2_status", "stage2_outcome", "stage2_classification"])
    owners = defaultdict(list)
    for index, issue in enumerate(draft["validation"].get("active_review_queue", [])):
        owner = next((name for name, types in OWNER_TYPES.items() if issue.get("type") in types), "Other review")
        owners[owner].append({"origin": "original_review_queue", "original_index": index, "finding": issue})
    for issue in findings:
        owners[issue["owner_category"]].append({"origin": "offline_comparison", "finding": issue})
    summary = {"extracted_products": len(rows), "page_count": len(images),
        "products_with_unknown_price": sum(r["price"] is None for r in rows),
        "original_review_issues": len(draft["validation"].get("active_review_queue", [])),
        "offline_comparison_flags": len(findings), "comparison_types": dict(Counter(f["type"] for f in findings)),
        "owner_category_counts": {k: len(v) for k, v in owners.items()},
        "accuracy_metrics": None, "metrics_reason": "No manually verified ground truth supplied.",
        "import_status": "needs_review", "ready_to_publish": False}
    write_json(folder / "findings.json", {"summary": summary, "grouped_findings": owners})
    write_csv(folder / "human_review.csv", [{"record_type": "product_confirmation", "item_id": r["item_id"],
        "source_page": r["source_page"], "extracted_name": r["name"], "extracted_category": r["category"],
        "extracted_price": r["price"], "extracted_currency": r["currency"], "decision": "pending"} for r in rows], REVIEW_FIELDS)
    manifest = {"audit_version": "offline_quality_v1", "draft_path": str(draft_path), "draft_sha256": original_sha,
        "pdf_path": str(pdf), "pdf_sha256": sha(pdf), "page_images": images,
        "ready_to_publish": False, "import_status": "needs_review", "summary": summary}
    write_json(folder / "manifest.json", manifest)
    blocks = []
    for number, image in enumerate(images, 1):
        products = []
        for row in rows:
            if row["source_page"] != number:
                continue
            def e(value):
                return escape("Unknown" if value is None else str(value))
            products.append(f"<article><h3>{e(row['name'])}</h3><p>{e(row['category'])} | {e(row['item_id'])}</p>"
                f"<p>{e(row['description'])}</p><p>Price: {e(row['price'])} {e(row['currency'])}</p>"
                f"<p>Source: {e(row['source_text'])}</p><p>Stage 2: {e(row['stage2_status'])} / {e(row['stage2_classification'])}</p>"
                f"<details><summary>Relationships and evidence</summary><pre>{e(json.dumps({k:row[k] for k in ('stage1_option_groups','stage2_option_groups','stage2_fixed_components','stage2_ambiguities','stage2_unresolved_relationships')},indent=2,ensure_ascii=False))}</pre></details></article>")
        blocks.append(f'<h2 id="page{number}">Page {number}</h2><section><img src="{image}" alt="Original PDF page {number}"><div>{"".join(products)}</div></section>')
    html = ('<!doctype html><html><head><meta charset="utf-8"><title>Offline extraction audit</title><style>'
        'body{font:16px system-ui;margin:24px;color:#202020}section{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}'
        'img{width:100%;position:sticky;top:12px}article{padding:12px;border-bottom:1px solid #bbb}pre{white-space:pre-wrap;overflow-wrap:anywhere}'
        '@media(max-width:800px){section{grid-template-columns:1fr}img{position:static}}</style></head><body>'
        '<h1>Offline extraction comparison</h1><p>Experimental. Publication blocked. Comparison flags are not verified errors.</p>'
        '<p>Edit human_review.csv to record confirmations, omissions, false positives, category corrections and verified prices. '
        'See PRODUCT_RECOVERY documentation separately for pipeline behaviour.</p>' + ''.join(blocks) + '</body></html>')
    (folder / "comparison.html").write_text(html, encoding="utf-8")
    return folder, summary


def review(audit_dir, csv_path=None):
    folder = Path(audit_dir)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("ready_to_publish") is not False or manifest.get("audit_version") != "offline_quality_v1":
        raise AuditError("Unsupported or publishable audit manifest.")
    if sha(manifest["draft_path"]) != manifest["draft_sha256"] or sha(manifest["pdf_path"]) != manifest["pdf_sha256"]:
        raise AuditError("Original PDF/draft changed since audit generation.")
    _, draft = load_complete(Path(manifest["pdf_path"]), Path(manifest["draft_path"]))
    items = {r["item_id"]: r for r in inventory(draft)}
    confirmations, omissions, completed_pages, corrections = {}, [], set(), []
    review_csv = Path(csv_path) if csv_path else folder / "human_review.csv"
    review_raw = review_csv.read_bytes()
    review_sha = hashlib.sha256(review_raw).hexdigest()
    with io.StringIO(review_raw.decode("utf-8-sig"), newline="") as file:
        reader = csv.DictReader(file)
        if not set(REVIEW_FIELDS).issubset(reader.fieldnames or []):
            raise AuditError("Review CSV is missing required columns.")
        for number, row in enumerate(reader, 2):
            if row["decision"] in ("", "pending", "uncertain"):
                continue
            if row["decision"] not in ("confirmed", "false_positive"):
                raise AuditError(f"Row {number}: unknown decision.")
            try:
                page = int(row["source_page"])
            except ValueError:
                raise AuditError(f"Row {number}: a valid source page is required.") from None
            if not 1 <= page <= manifest["summary"]["page_count"] or not row["evidence"].strip() or not row["reviewer"].strip():
                raise AuditError(f"Row {number}: manual evidence, reviewer and valid page are required.")
            kind, identity = row["record_type"], row["item_id"]
            if kind in ("product_confirmation", "category_correction", "verified_price"):
                if identity not in items or items[identity]["source_page"] != page:
                    raise AuditError(f"Row {number}: unknown item ID or incorrect page.")
            elif kind not in ("omission", "page_complete"):
                raise AuditError(f"Row {number}: unknown record type.")
            if kind != "product_confirmation" and row["decision"] != "confirmed":
                raise AuditError(f"Row {number}: this record type requires confirmed decision.")
            if kind == "product_confirmation":
                if identity in confirmations:
                    raise AuditError(f"Row {number}: duplicate product confirmation.")
                confirmations[identity] = row["decision"]
            if kind == "omission":
                if identity or not row["verified_name"].strip():
                    raise AuditError(f"Row {number}: omissions need a verified name and empty item ID.")
                if row in omissions:
                    raise AuditError(f"Row {number}: duplicate omission record.")
                omissions.append(row)
            if kind == "page_complete":
                if identity:
                    raise AuditError(f"Row {number}: page census must not identify an item.")
                completed_pages.add(page)
            if kind == "category_correction" and not row["verified_category"].strip():
                raise AuditError(f"Row {number}: category correction needs a verified category.")
            if kind == "verified_price" and not row["verified_price"].strip():
                raise AuditError(f"Row {number}: verified price is required.")
            if row["verified_price"].strip():
                try:
                    value = Decimal(row["verified_price"])
                    if not value.is_finite() or value < 0 or not row["verified_currency"].strip():
                        raise InvalidOperation()
                except InvalidOperation:
                    raise AuditError(f"Row {number}: price must be a nonnegative number with explicit currency.") from None
            corrections.append({"csv_row": number, **row})
    tp = sum(v == "confirmed" for v in confirmations.values())
    fp = sum(v == "false_positive" for v in confirmations.values())
    metrics = {}
    if confirmations:
        metrics["precision_on_manually_reviewed_products"] = tp / (tp + fp)
        metrics["reviewed_prediction_count"] = tp + fp
    census = completed_pages == set(range(1, manifest["summary"]["page_count"] + 1)) and set(confirmations) == set(items)
    if census:
        metrics["recall_on_reviewer_asserted_complete_census"] = tp / (tp + len(omissions)) if tp + len(omissions) else None
        metrics["confirmed_omission_count"] = len(omissions)
    result = {"review_version": "offline_human_review_v1", "original_draft_sha256": manifest["draft_sha256"],
        "pdf_sha256": manifest["pdf_sha256"], "review_csv_sha256": review_sha,
        "manual_records": corrections, "accuracy_metrics": metrics or None,
        "complete_census_asserted": census, "metrics_scope": "Manual product presence only; no relationship or global semantic accuracy inferred.",
        "import_status": "needs_review", "ready_to_publish": False}
    path = folder / ("review_results_" + uuid4().hex[:12] + ".json")
    write_json(path, result)
    return path, result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate")
    create.add_argument("pdf", type=Path)
    create.add_argument("--draft", type=Path)
    create.add_argument("--draft-dir", type=Path)
    check = commands.add_parser("review")
    check.add_argument("audit_directory", type=Path)
    check.add_argument("--csv", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            path, summary = generate(args.pdf, args.draft, args.draft_dir)
            print(json.dumps(summary, indent=2))
        else:
            path, result = review(args.audit_directory, args.csv)
            print(json.dumps({"manual_records": len(result["manual_records"]), "accuracy_metrics": result["accuracy_metrics"]}, indent=2))
        print(f"Saved: {path}\nPublication blocked. Offline only; no API calls.")
        return 0
    except AuditError as exc:
        print(f"Audit failed: {exc} No original data changed.")
        return 1
    except (OSError, ValueError, KeyError, RuntimeError):
        print("Audit failed: invalid input, provenance, PDF or review CSV. Check input files; no original data changed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
