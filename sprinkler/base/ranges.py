import re

def parse_range(spec: str):
    text = str(spec or "").replace(" ", "")
    if not text:
        return []

    return _parse_range(text, _to_int)

def parse_hex_range(spec: str):
    text = str(spec or "").replace(" ", "")
    if not text:
        return []

    return _parse_range(text, _to_hex_int)

def _parse_range(text: str, converter):
    out: list[int] = []
    for token in text.split(","):
        if not token:
            continue
        match = re.fullmatch(r"([0-9a-fA-FxX]+)-([0-9a-fA-FxX]+)", token)
        if match:
            left = converter(match.group(1))
            right = converter(match.group(2))
            step = 1 if left <= right else -1
            out.extend(range(left, right + step, step))
            continue
        if not re.fullmatch(r"[0-9a-fA-FxX]+", token):
            raise ValueError(f"bad range token: {token}")
        out.append(converter(token))

    seen = set()
    unique: list[int] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique

def _to_int(token: str):
    text = str(token or "").strip().lower()
    if not text:
        raise ValueError("empty range token")
    base = 16 if text.startswith("0x") or re.search(r"[a-f]", text) else 10
    try:
        return int(text, base)
    except ValueError as exc:
        raise ValueError(f"bad range token: {token}") from exc

def _to_hex_int(token: str):
    text = str(token or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        raise ValueError("empty range token")
    try:
        return int(text, 16)
    except ValueError as exc:
        raise ValueError(f"bad range token: {token}") from exc
