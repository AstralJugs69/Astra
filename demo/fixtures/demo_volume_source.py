"""A sizeable, original source pair for the live-demo Braille ripple.

The corpus is deliberately generated from closed local text, rather than copied
from a book or a web page.  It models an educational cell-systems field guide:
headings and paragraphs only, so the exact same simple structure can be pasted
into the supported native Google Doc demo source.
"""

from __future__ import annotations

from collections.abc import Iterator

_TOPICS = (
    ("boundary", "The cell membrane regulates exchange with the surrounding solution."),
    (
        "transport",
        "Transport proteins move selected materials without changing the record of the cell.",
    ),
    ("energy", "Mitochondria release usable energy from nutrients for ordinary cellular work."),
    (
        "information",
        "The nucleus protects genetic instructions and coordinates many cell activities.",
    ),
    (
        "storage",
        "The vacuole holds water and dissolved materials that help maintain internal balance.",
    ),
    ("repair", "Ribosomes assemble proteins that support maintenance, growth, and repair."),
    ("sorting", "The Golgi apparatus prepares and routes materials for their next location."),
    ("cleanup", "Lysosomes break down selected materials so reusable parts can be recovered."),
    ("support", "The cytoskeleton helps the cell keep shape and organize internal movement."),
    ("communication", "Receptors receive signals that let a cell respond to changing conditions."),
)

_FIELD_NOTES = (
    "The guide records the observation, the reason it matters, and the limit on the conclusion.",
    "The class compares the wording with its diagram and leaves uncertain terms for a qualified reviewer.",
    "The activity uses a small, repeatable observation so a later reader can trace the source of every claim.",
    "The lesson distinguishes a measured fact from an illustration and does not treat a visual aid as a final proof.",
    "The record keeps the language accessible while preserving the scientific relationship under discussion.",
)


def _ordinary_section(number: int) -> tuple[str, str]:
    topic, fact = _TOPICS[(number - 1) % len(_TOPICS)]
    note = _FIELD_NOTES[(number - 1) % len(_FIELD_NOTES)]
    heading = f"## Field lesson {number:02d}: {topic.title()} evidence"
    paragraph = (
        f"Field lesson {number:02d} checks one cell-systems claim before a tactile study "
        f"guide is prepared. {fact} Students name the observation in plain language, "
        f"connect it to a simple model, and leave uncertainty visible for a qualified "
        f"reviewer. {note} The lesson ends by asking what an editor should verify before "
        f"an accessible guide is embossed."
    )
    return heading, paragraph


def _correction_section(version: str) -> tuple[str, str]:
    if version not in {"v1", "v2"}:
        raise ValueError("demo volume version must be v1 or v2")
    correction = (
        "The nucleus stores water and dissolved minerals for the cell."
        if version == "v1"
        else "The vacuole stores water and dissolved minerals for the cell."
    )
    heading = "## Field lesson 41: Correcting the storage claim"
    paragraph = (
        "This synthetic correction exercise sits near the middle of the volume so the "
        "production team can see a local source edit and its Braille consequences. "
        + correction
        + " The team records the sentence beside a simple membrane diagram, checks the term "
        "against its labeled structure, and asks a reviewer to distinguish storage from "
        "information control before releasing a tactile guide. The surrounding field lessons "
        "are unchanged synthetic context."
    )
    return heading, paragraph


def _sections(version: str) -> Iterator[str]:
    yield "# Cellular Systems Field Guide"
    yield (
        "This entirely synthetic educational volume is an original demo source for a "
        "Braille production investigation. It uses only simple headings and paragraphs. "
        "Each field lesson connects a cell-systems observation to a responsible editorial "
        "review practice; it is not copied from a textbook or a production master."
    )
    for number in range(1, 81):
        if number == 41:
            yield from _correction_section(version)
        else:
            yield from _ordinary_section(number)


def build_demo_volume(version: str) -> bytes:
    """Return deterministic UTF-8 Markdown for one wholly synthetic source revision."""

    sections = tuple(_sections(version))
    return ("\n\n".join(sections) + "\n").encode("utf-8")
