import re


_NUMBER_WORDS = {
    "ZERO": "0",
    "ONE": "1",
    "TWO": "2",
    "THREE": "3",
    "FOUR": "4",
    "FIVE": "5",
    "SIX": "6",
    "SEVEN": "7",
    "EIGHT": "8",
    "NINE": "9",
}


def normalize_job_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Job ID must be text")
    tokens = re.findall(r"[A-Za-z]+|\d+", value.upper())
    normalized = "".join(_NUMBER_WORDS.get(token, token) for token in tokens)
    normalized = normalized.replace("HYPHEN", "").replace("DASH", "")
    match = re.fullmatch(r"(?:JOB)?(\d{4})", re.sub(r"[^A-Z0-9]", "", normalized))
    if not match:
        raise ValueError("Job ID must contain JOB and four digits, such as JOB-1002")
    return f"JOB-{match.group(1)}"
