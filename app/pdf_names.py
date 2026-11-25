"""Read patient and study values out of a PDF's file name, for Router PDF rules.

A pattern is the file name (without ``.pdf``) with ``{Tokens}`` where the values
sit, for example ``{PatientID}_{PatientName}_{Accession}``. Nothing here keeps
or logs a file name.
"""

from __future__ import annotations

import re

# token -> the option it fills in
TOKENS = {
    "PatientID": "patient_id",
    "PatientName": "patient_name",
    "Accession": "accession_number",
    "Description": "study_description",
    "Title": "document_title",
}
SKIP = "*"
_TOKEN = re.compile(r"\{([^{}]*)\}")


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """The regular expression for a pattern, or ValueError saying what is wrong."""
    pattern = (pattern or "").strip()
    pieces = _TOKEN.split(pattern)  # literal, token, literal, token, ..., literal
    if len(pieces) == 1:
        raise ValueError("The file name pattern needs at least one {Token}, such as {PatientID}")
    seen: set[str] = set()
    out = ["^"]
    for index, piece in enumerate(pieces):
        if index % 2 == 0:
            if "{" in piece or "}" in piece:
                raise ValueError("The file name pattern has an unmatched { or }")
            if index not in (0, len(pieces) - 1) and not piece:
                raise ValueError("Put a separator such as _ or - between the {Tokens} in the file name pattern")
            out.append(re.escape(piece))
            continue
        if piece == SKIP:
            out.append(".*?")
            continue
        if piece not in TOKENS:
            names = ", ".join("{" + name + "}" for name in TOKENS) + ", {*}"
            raise ValueError(f"Unknown {{{piece}}} in the file name pattern. Use {names}")
        if piece in seen:
            raise ValueError(f"{{{piece}}} appears twice in the file name pattern")
        seen.add(piece)
        out.append(f"(?P<{TOKENS[piece]}>.+?)")
    out.append("$")
    return re.compile("".join(out))


def values_from_name(compiled: re.Pattern[str], file_name: str) -> dict[str, str] | None:
    """The values in a file name, or None when the name does not fit the pattern."""
    stem = file_name[:-4] if file_name.lower().endswith(".pdf") else file_name
    match = compiled.match(stem)
    if match is None:
        return None
    values = {key: value.strip() for key, value in match.groupdict().items()}
    return values if all(values.values()) else None
