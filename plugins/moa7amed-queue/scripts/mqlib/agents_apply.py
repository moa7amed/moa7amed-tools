"""Integration of agent results into shared project state: BidStudio sections and
requirements, Replica screens.

Every update runs in one short transaction that first checks that what the agent started
from is unchanged (its input fingerprint); a stale result is rejected and reported, never
written over newer work, and accepted sections of other agents are kept. Agents never
write files (stage A: only the coordinator writes).
"""
from __future__ import annotations

import hashlib
import json
import os



def section_fingerprint(content):
    return hashlib.sha256(json.dumps(content or "", sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()[:16]



# --------------------------------------------------------------------------- shared state

def shared_state(q, task, item, result):
    if item["applier"] == "bid_sections":
        return _bid_sections(q, item, result)
    if item["applier"] == "bid_requirements":
        return _bid_requirements(q, item, result)
    if item["applier"] == "replica_screens":
        return _replica_screen(q, item, result)
    return "rejected", "unknown result type %s" % item["applier"]


def _bid_sections(q, item, result):
    from . import bid_store
    inputs = item["inputs"] or {}
    bid_id = inputs.get("bid")
    assigned = {title.lower(): title for title in inputs.get("sections") or []}
    expected = inputs.get("fingerprints") or {}
    accepted, stale, ignored = [], [], []
    with q.tx():
        technical = bid_store.get(q, bid_id)["document"].get("technical") or {}
        current = {str(s.get("title") or "").lower(): s for s in technical.get("sections") or []}
        for section in result.get("sections") or []:
            title = str(section.get("title") or "")
            if title.lower() not in assigned or not section.get("content"):
                ignored.append(title or "?")
                continue
            now = section_fingerprint((current.get(title.lower()) or {}).get("content"))
            if now != expected.get(assigned[title.lower()]):
                stale.append(title)
                continue
            accepted.append({"title": assigned[title.lower()], "content": section["content"]})
        if accepted:
            bid_store.apply(q, bid_id, {"technical": {"sections": accepted}},
                            "worker %s of task #%d: %d section(s)" % (item["key"], item["task_id"],
                                                                     len(accepted)))
    detail = "%d section(s) applied" % len(accepted)
    if stale:
        detail += "; stale (changed since the worker started, not overwritten): %s" % ", ".join(stale)
    if ignored:
        detail += "; ignored (not assigned or empty): %s" % ", ".join(ignored)
    if not accepted:
        return ("stale" if stale else "rejected"), detail
    return ("partial" if stale or ignored else "accepted"), detail


def _bid_requirements(q, item, result):
    """Requirements found by a worker in one document: numbered here, checked against the
    document's text, duplicates dropped; an entry whose quote is not in the document is
    rejected, never stored."""
    from . import bid_model, bid_store
    inputs = item["inputs"] or {}
    bid_id, doc_id = inputs.get("bid"), inputs.get("doc")
    texts = bid_store.texts(q, bid_id)
    doc_text = (texts.get(doc_id) or {}).get("text") or ""
    accepted, rejected = [], []
    with q.tx():
        document = bid_store.get(q, bid_id)["document"]
        existing = {bid_model.normalise_text(req.get("text")) for req in document.get("requirements") or []}
        numbers = [int(req["id"][4:]) for req in document.get("requirements") or []
                   if bid_model.REQ_ID.match(str(req.get("id")))]
        next_number = max(numbers or [0]) + 1
        for req in result.get("requirements") or []:
            text = req.get("text") or ""
            source = dict(req.get("source") or {}, doc=doc_id)
            if bid_model.normalise_text(text) in existing:
                continue
            if req.get("class", "EXPLICIT_REQUIREMENT") == "EXPLICIT_REQUIREMENT" and \
                    not bid_model.quote_found(text, doc_text):
                rejected.append(text[:80])
                continue
            accepted.append(dict({key: value for key, value in req.items() if key != "id"},
                                 id="REQ-%03d" % next_number, source=source))
            existing.add(bid_model.normalise_text(text))
            next_number += 1
        if accepted:
            bid_store.apply(q, bid_id, {"requirements": accepted},
                            "worker %s of task #%d: %d requirement(s) from %s"
                            % (item["key"], item["task_id"], len(accepted), doc_id))
    detail = "%d requirement(s) from %s applied" % (len(accepted), doc_id)
    if rejected:
        detail += "; %d rejected because the quote is not in the document: %s" % (
            len(rejected), "; ".join(rejected[:3]))
    if not accepted:
        return "rejected", detail
    return ("partial" if rejected else "accepted"), detail


def _replica_screen(q, item, result):
    from . import replica_model, replica_store
    inputs = item["inputs"] or {}
    project_id, screen_id = inputs.get("project"), inputs.get("screen")
    screen = dict(result.get("screen") or {})
    if not screen.get("name"):
        return "rejected", "no screen in the result"
    screen.update(id=screen_id, refs=inputs.get("refs") or screen.get("refs") or [])
    screen.pop("design", None)          # the design baseline has one writer: replica tokens
    with q.tx():
        project = replica_store.get(q, project_id)
        current = {s.get("id"): s for s in project["document"].get("screens") or []}
        if section_fingerprint(current.get(screen_id)) != inputs.get("fingerprint"):
            return "stale", "%s changed since the worker started; not overwritten" % screen_id
        trial = json.loads(json.dumps(project["document"]))
        trial["screens"] = [s for s in trial.get("screens") or [] if s.get("id") != screen_id] + [screen]
        errors = [f for f in replica_model.validate(trial, replica_store.refs(q, project_id),
                                                    project["mode"])
                  if f["level"] == "error" and f["where"] == screen_id]
        if errors:
            return "rejected", "; ".join(f["message"] for f in errors[:3])
        replica_store.apply_inventory(q, project_id, {"screens": [screen]})
    return "accepted", "screen %s recorded" % screen_id


def apply_now(q, item, result):
    """For tests and the CLI: apply a result as the coordinator would."""
    return shared_state(q, None, item, result)



