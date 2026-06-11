"""Sort criteria (tiebreaks), ported from fzf's result.go / options.go."""

from .algo import is_space, trim_length

BY_SCORE = 0
BY_CHUNK = 1
BY_LENGTH = 2
BY_BEGIN = 3
BY_END = 4
BY_PATHNAME = 5

_MAX_UINT16 = 0xFFFF

_CRITERION_NAMES = {
    "chunk": BY_CHUNK,
    "pathname": BY_PATHNAME,
    "length": BY_LENGTH,
    "begin": BY_BEGIN,
    "end": BY_END,
}


def parse_tiebreak(s):
    """Port of parseTiebreak: returns the criteria list, always starting with
    BY_SCORE. Raises ValueError on bad input."""
    criteria = [BY_SCORE]
    seen = set()
    has_index = False
    for word in s.lower().split(","):
        if word == "index":
            if "index" in seen:
                raise ValueError("duplicate sort criteria: index")
            if has_index:
                raise ValueError("index should be the last criterion")
            seen.add("index")
            has_index = True
            continue
        if word not in _CRITERION_NAMES:
            raise ValueError("invalid sort criterion: " + word)
        if word in seen:
            raise ValueError("duplicate sort criteria: " + word)
        if has_index:
            raise ValueError("index should be the last criterion")
        seen.add(word)
        criteria.append(_CRITERION_NAMES[word])
    if len(criteria) > 4:
        raise ValueError("at most 3 tiebreaks are allowed: " + s)
    return criteria


def criteria_for_scheme(scheme_name):
    """Port of parseScheme's criteria selection."""
    if scheme_name == "history":
        return [BY_SCORE]
    if scheme_name == "path":
        return [BY_SCORE, BY_PATHNAME, BY_LENGTH]
    return [BY_SCORE, BY_LENGTH]


def _as_uint16(val):
    if val > _MAX_UINT16:
        return _MAX_UINT16
    if val < 0:
        return 0
    return val


def build_rank(item_text, offsets, score, criteria):
    """Compute the sort key for a matched item: a tuple compared
    lexicographically, smaller is better. Port of buildResult."""
    if len(offsets) > 1:
        offsets = sorted(offsets)

    min_begin = _MAX_UINT16
    min_end = _MAX_UINT16
    max_end = 0
    valid_offset_found = False
    for b, e in offsets:
        if b < e:
            min_begin = min(b, min_begin)
            min_end = min(e, min_end)
            max_end = max(e, max_end)
            valid_offset_found = True

    num_chars = len(item_text)
    points = []
    for criterion in criteria:
        val = _MAX_UINT16
        if criterion == BY_SCORE:
            # Higher is better
            val = _MAX_UINT16 - _as_uint16(score)
        elif criterion == BY_CHUNK:
            if valid_offset_found:
                b = min_begin
                e = max_end
                while b >= 1 and not is_space(item_text[b - 1]):
                    b -= 1
                while e < num_chars and not is_space(item_text[e]):
                    e += 1
                val = _as_uint16(e - b)
        elif criterion == BY_LENGTH:
            val = _as_uint16(trim_length(item_text))
        elif criterion == BY_PATHNAME:
            if valid_offset_found:
                last_delim = -1
                for i in range(len(item_text) - 1, -1, -1):
                    if item_text[i] == "/" or item_text[i] == "\\":
                        last_delim = i
                        break
                if last_delim <= min_begin:
                    val = _as_uint16(min_begin - last_delim)
        elif criterion in (BY_BEGIN, BY_END):
            if valid_offset_found:
                white_prefix_len = 0
                for idx in range(num_chars):
                    white_prefix_len = idx
                    if idx == min_begin or not is_space(item_text[idx]):
                        break
                if criterion == BY_BEGIN:
                    val = _as_uint16(min_end - white_prefix_len)
                else:
                    val = _as_uint16(
                        _MAX_UINT16 - _MAX_UINT16 * (max_end - white_prefix_len)
                        // (_as_uint16(trim_length(item_text)) + 1))
        points.append(val)
    return tuple(points)
