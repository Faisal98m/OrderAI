"""Experimental lexical candidates only; never resolves or mutates menu choices."""
from copy import deepcopy
import hashlib
import json
import re


def normalize(text):
    return " ".join((text or "").casefold().split())


def reference_candidates(menu: dict) -> list[dict]:
    """Exact means an exact label match, not an approved relationship."""
    targets = []
    for index, category in enumerate(menu["categories"]):
        for source in category["category_sources"]:
            targets.append({"target_type": "category", "category_index": index,
                "category": source["name"], "item_id": None, "product": None,
                "source_page": source["source_page"], "evidence": deepcopy(source)})
        for item in category["items"]:
            targets.append({"target_type": "product", "category_index": index,
                "category": category["name"], "item_id": item["id"], "product": item["name"],
                "source_page": item["source_page"], "evidence": {
                    "name": item["name"], "description": item["description"],
                    "source_text": item["source_text"], "source_page": item["source_page"]}})
    results = []
    for category in menu["categories"]:
        for item in category["items"]:
            groups = item.get("relationship_interpretation", {}).get("option_groups", [])
            for index, group in enumerate(groups):
                if group["choices_status"] != "unresolved_reference":
                    continue
                reference = group["menu_reference"]
                ref = normalize(reference)
                tokens = set(re.findall(r"\w+", ref)) - {"our", "the", "of", "from", "choice", "choose", "range", "menu"}
                candidates = []
                for target in targets:
                    if target["item_id"] == item["id"]:
                        continue
                    label = normalize(target["product"] or target["category"])
                    target_tokens = set(re.findall(r"\w+", label))
                    # Singular/plural is approximate, never an exact match.
                    shared = {word.rstrip("s") for word in tokens} & {word.rstrip("s") for word in target_tokens}
                    if ref and ref == label:
                        match = "exact_label"
                    elif shared:
                        match = "approximate_lexical"
                    else:
                        continue
                    candidates.append({**deepcopy(target), "match_type": match,
                        "support": {"reference_text": reference,
                                    "matched_label": target["product"] or target["category"],
                                    "shared_tokens": sorted(shared)}, "approved": False})
                signature = json.dumps([item["id"], index, reference], ensure_ascii=False)
                results.append({"reference_id": "ref_" + hashlib.sha256(signature.encode()).hexdigest()[:20],
                    "item_id": item["id"], "product": item["name"], "source_page": item["source_page"],
                    "relationship_path": f"option_groups[{index}]", "menu_reference": reference,
                    "source_evidence": group["evidence"], "choices_status": "unresolved_reference",
                    "status": "unresolved", "requires_human_approval": True,
                    "candidates": sorted(candidates, key=lambda c: (c["match_type"] != "exact_label",
                                                                     c["source_page"], c["item_id"] or ""))})
    return results
