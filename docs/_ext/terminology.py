"""Give explicit glossary links a short definition from the canonical glossary."""

from __future__ import annotations

from pathlib import Path
import re


def definitions(path: Path) -> dict[str, str]:
    """Extract the first plain-language sentence; do not maintain a second glossary."""
    lines = path.read_text(encoding="utf-8").splitlines()
    result = {}
    for index, line in enumerate(lines[:-1]):
        if not re.fullmatch(r"   [^ :].*", line) or not lines[index + 1].startswith("      "):
            continue
        term = line.strip()
        text = []
        for continuation in lines[index + 1:]:
            if not continuation.startswith("      "):
                break
            text.append(continuation.strip())
        description = " ".join(text)
        description = re.sub(r":[\w-]+:`([^`]+)`", lambda match: match[1].split(" <", 1)[0], description)
        description = re.sub(r"`([^`<]+) <[^>]+>`_", r"\1", description)
        description = description.replace("`", "").replace("**", "")
        sentence = re.split(r"(?<=[.!?])\s+", description, maxsplit=1)[0]
        key = "term-" + term.lower().replace(" ", "-")
        if key in result:
            raise ValueError(f"Duplicate glossary term: {term}")
        result[key] = sentence
    return result


def setup(app):
    from docutils import nodes

    def annotate_links(app, doctree, docname):
        glossary = Path(app.srcdir) / "glossary.rst"
        terms = definitions(glossary)
        for link in doctree.findall(nodes.reference):
            uri = link.get("refuri", "")
            key = uri.split("#")[-1] if "glossary" in uri else link.get("refid") if docname == "glossary" else None
            key = key.lower() if key else None
            if key in terms:
                link["reftitle"] = terms[key]
                link["classes"].append("term-definition-link")

    def note_glossary_dependency(app, docname, source):
        # The current document context exists during reading, not necessarily
        # during output resolution (in particular on Sphinx 8 / Python 3.10).
        app.env.note_dependency(str(Path(app.srcdir) / "glossary.rst"))

    app.connect("source-read", note_glossary_dependency)
    app.connect("doctree-resolved", annotate_links, priority=700)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
