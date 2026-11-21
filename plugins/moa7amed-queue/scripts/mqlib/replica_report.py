"""The reconstruction report of a replica project (Markdown). It separates what was
verified, what was built but not verified, what is a mock, and what is unknown, and
states the method behind every number."""
from __future__ import annotations

from . import replica_model as model
from . import replica_store as rstore


def _table(header, rows):
    out = ["| %s |" % " | ".join(header), "|%s" % "".join(" :-- |" for _ in header)]
    for row in rows:
        out.append("| %s |" % " | ".join(str(cell).replace("|", "/").replace("\n", " ")
                                         for cell in row))
    return out


def render(q, project_id):
    status = rstore.status(q, project_id)
    project = rstore.get(q, project_id)
    document = project["document"]
    references = rstore.refs(q, project_id)
    parity = status["parity"]
    lines = ["# Replica %s: reconstruction report" % project_id, "",
             "Request (verbatim):", "", "> %s" % project["request"].replace("\n", "\n> "), "",
             "- Mode: **%s**%s; quality target: %s; ownership of the reference: %s" % (
                 status["mode"], "" if status["mode"] == status["requested_mode"]
                 else " (requested %s)" % status["requested_mode"], status["quality"],
                 status["ownership"]),
             "- Stage: %s; plan: %s" % (status["stage"], status["plan_stage"]),
             "- Cost of its tasks, as reported by Claude Code: $%.4f" % status["cost_usd_reported"],
             "", "Quality targets are delivery goals, not guarantees. Visual similarity is not "
             "evidence of working behaviour.", ""]
    if status["ownership"] not in ("own", "licensed"):
        lines += ["Asset policy: the reference is treated as a third-party application. The "
                  "rebuild uses original code, branding, artwork and text; layout and "
                  "interaction patterns are reproduced. Run `replica sweep` before publishing.",
                  ""]
    lines += ["## References", ""]
    lines += _table(["id", "kind", "status", "source", "provenance", "limitation"], [
        (item["id"], item["kind"], item["status"], item.get("uri") or
         (item["meta"].get("original_name") or "") or "", item.get("provenance") or "",
         item.get("limitation") or "") for item in references])
    lines += ["", "## Screen inventory", ""]
    lines += _table(["id", "name", "evidence", "components", "navigates to", "unknown"], [
        (item.get("id"), item.get("name"), item.get("evidence"),
         ", ".join(item.get("components") or []), ", ".join(item.get("navigates_to") or []),
         "; ".join(item.get("unknown") or [])) for item in document.get("screens") or []])
    lines += ["", "## Features and parity", ""]
    lines += _table(["id", "feature", "evidence", "backend", "built", "tests", "verdict"], [
        (row["feature"], row["name"], row["evidence"], "yes" if row["backend"] else "",
         row["built"], row["tests"], row["verdict"]) for row in parity["features"]])
    lines += ["", "Verdicts: *verified* = a real test passed; *built, not verified* = its task "
              "finished but no test was recorded; *mock* = labelled mock behaviour; *unknown "
              "behaviour* = the evidence does not show it.", ""]
    if parity.get("flows"):
        lines += ["## User flows", ""]
        lines += _table(["id", "flow", "evidence", "verdict"], [
            (row["flow"], row["name"], row["evidence"], row["verdict"]) for row in parity["flows"]])
        lines.append("")
    tokens = (document.get("design") or {}).get("tokens") or []
    if tokens:
        lines += ["## Design tokens", ""]
        lines += _table(["group", "value", "evidence", "count", "refs"], [
            (item.get("group"), item.get("value"), item.get("evidence"), item.get("count", ""),
             ", ".join(item.get("refs") or [])) for item in tokens[:60]])
        lines.append("")
    rows = rstore.comparisons(q, project_id)
    if rows:
        lines += ["## Visual comparison", ""]
        lines += _table(["screen", "score", "reference", "difference image", "notes"], [
            (row["screen"], "%.4f" % row["score"], row["reference"], row["diff"],
             "; ".join(row["metrics"].get("notes") or [])) for row in rows])
        lines += ["", "Method: %s" % rows[0]["method"], ""]
    steps = rstore.iterations(q, project_id)
    if steps:
        lines += ["## Improvement rounds", ""]
        lines += _table(["round", "status", "tasks", "before", "after"], [
            (row["number"], row["status"], ", ".join("#%d" % i for i in row["tasks"] or []),
             ", ".join("%s %.3f" % item for item in sorted((row["scores_before"] or {}).items())),
             ", ".join("%s %.3f" % item for item in sorted((row["scores_after"] or {}).items())))
            for row in steps])
        lines.append("")
    unknowns = list(document.get("unknowns") or [])
    unknowns += ["%s: %s" % (item["id"], item.get("name")) for item in document.get("features") or []
                 if item.get("evidence") == "UNKNOWN"]
    verified = [row for row in parity["features"] if row["verdict"] == "verified"]
    partial = [row for row in parity["features"]
               if row["verdict"] in ("built, not verified", "works against a mock", "mock",
                                     "in progress", "failing")]
    lines += ["## Summary", "",
              "- Verified: %s" % (", ".join(row["feature"] for row in verified) or "none"),
              "- Partial (built without a passing real test, mocked, failing or in progress): %s"
              % (", ".join("%s (%s)" % (row["feature"], row["verdict"]) for row in partial)
                 or "none"),
              "- Unknown: %s" % ("; ".join(str(item if not isinstance(item, dict) else
                                            item.get("text") or item.get("id"))
                                        for item in unknowns) or "none")]
    for item in status["limitations"]:
        lines.append("- Limitation (%s): %s" % (item["ref"], item["limitation"]))
    findings = model.validate(document, references, status["mode"])
    if any(item["level"] == "error" for item in findings):
        lines.append("- The inventory still has %d error(s): `moa7amed-queue replica status %s`"
                     % (len([i for i in findings if i["level"] == "error"]), project_id))
    return "\n".join(lines) + "\n"
