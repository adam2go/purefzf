"""Fuzzy matching algorithms ported from fzf (src/algo/algo.go).

This is a faithful port of fzf's matching algorithms at v0.73.1
(commit ce4bef75). The scoring model, bonus matrix, and all match
functions (FuzzyMatchV1/V2, exact, boundary, prefix, suffix, equal)
produce the same scores and offsets as the Go implementation.

Algo functions make two assumptions, same as upstream:
1. "pattern" is given in lowercase if "case_sensitive" is False
2. "pattern" is already normalized if "normalize" is True

Text positions are rune (code point) indices, as in fzf.
"""

import unicodedata

from .normalize import NORMALIZED

SCORE_MATCH = 16
SCORE_GAP_START = -3
SCORE_GAP_EXTENSION = -1

# We prefer matches at the beginning of a word, but the bonus should not be
# too great to prevent the longer acronym matches from always winning over
# shorter fuzzy matches. The bonus point here was specifically chosen that
# the bonus is cancelled when the gap between the acronyms grows over
# 8 characters, which is approximately the average length of the words found
# in web2 dictionary and my file system.
BONUS_BOUNDARY = SCORE_MATCH // 2

# Although bonus point for non-word characters is non-contextual, we need it
# for computing bonus points for consecutive chunks starting with a non-word
# character.
BONUS_NON_WORD = SCORE_MATCH // 2

# Edge-triggered bonus for matches in camelCase words.
BONUS_CAMEL123 = BONUS_BOUNDARY + SCORE_GAP_EXTENSION

# Minimum bonus point given to characters in consecutive chunks.
BONUS_CONSECUTIVE = -(SCORE_GAP_START + SCORE_GAP_EXTENSION)

# The first character in the typed pattern usually has more significance
# than the rest so it's important that it appears at special positions where
# bonus points are given.
BONUS_FIRST_CHAR_MULTIPLIER = 2

# Character classes (order matters: comparisons such as `cls > NON_WORD`
# mirror the Go code)
CHAR_WHITE = 0
CHAR_NON_WORD = 1
CHAR_DELIMITER = 2
CHAR_LOWER = 3
CHAR_UPPER = 4
CHAR_LETTER = 5
CHAR_NUMBER = 6

WHITE_CHARS = " \t\n\v\f\r\x85\xa0"

# Matches Go's unicode.IsSpace (the White_Space property)
_ASCII_SPACE = frozenset(" \t\n\v\f\r")


def is_space(ch):
    if ch in _ASCII_SPACE:
        return True
    cp = ord(ch)
    if cp < 128:
        return False
    return cp in (0x85, 0xA0) or unicodedata.category(ch) in ("Zs", "Zl", "Zp")


_ASCII_SPACE_STR = " \t\n\v\f\r"


def leading_whitespaces(text):
    if text.isascii():
        return len(text) - len(text.lstrip(_ASCII_SPACE_STR))
    n = 0
    for ch in text:
        if not is_space(ch):
            break
        n += 1
    return n


def trailing_whitespaces(text):
    if text.isascii():
        return len(text) - len(text.rstrip(_ASCII_SPACE_STR))
    n = 0
    for ch in reversed(text):
        if not is_space(ch):
            break
        n += 1
    return n


def trim_length(text):
    if text.isascii():
        return len(text.strip(_ASCII_SPACE_STR))
    trailing = trailing_whitespaces(text)
    if trailing == len(text):  # completely empty
        return 0
    return len(text) - trailing - leading_whitespaces(text)


def to_lower_rune(cp):
    """Simple (1:1) lowercase mapping of a code point, like Go's unicode.To."""
    lowered = chr(cp).lower()
    if len(lowered) == 1:
        return ord(lowered)
    # Full case mapping expanded to multiple chars (e.g. U+0130); Go's simple
    # mapping keeps a single rune, which is the first char of the expansion.
    return ord(lowered[0])


class Scheme:
    """Precomputed tables for a scoring scheme (fzf's algo.Init)."""

    def __init__(self, name="default"):
        if name == "default":
            self.bonus_boundary_white = BONUS_BOUNDARY + 2
            self.bonus_boundary_delimiter = BONUS_BOUNDARY + 1
            self.initial_char_class = CHAR_WHITE
            self.delimiter_chars = "/,:;|"
        elif name == "path":
            self.bonus_boundary_white = BONUS_BOUNDARY
            self.bonus_boundary_delimiter = BONUS_BOUNDARY + 1
            self.initial_char_class = CHAR_DELIMITER
            self.delimiter_chars = "/"
        elif name == "history":
            self.bonus_boundary_white = BONUS_BOUNDARY
            self.bonus_boundary_delimiter = BONUS_BOUNDARY
            self.initial_char_class = CHAR_WHITE
            self.delimiter_chars = "/,:;|"
        else:
            raise ValueError("invalid scheme: " + name)
        self.name = name

        classes = []
        for cp in range(128):
            ch = chr(cp)
            if "a" <= ch <= "z":
                c = CHAR_LOWER
            elif "A" <= ch <= "Z":
                c = CHAR_UPPER
            elif "0" <= ch <= "9":
                c = CHAR_NUMBER
            elif ch in WHITE_CHARS:
                c = CHAR_WHITE
            elif ch in self.delimiter_chars:
                c = CHAR_DELIMITER
            else:
                c = CHAR_NON_WORD
            classes.append(c)
        self.ascii_char_classes = classes

        self.bonus_matrix = [
            [self._bonus_for(p, c) for c in range(CHAR_NUMBER + 1)]
            for p in range(CHAR_NUMBER + 1)
        ]

        # C-speed helpers for the ASCII fast path: a 256-entry translate
        # table mapping an ASCII byte to its char class, and the bonus
        # matrix flattened to be indexed with (prev_class << 3) | class.
        self.ascii_class_table = bytes(classes) + bytes(128)
        flat = [0] * 64
        for p in range(CHAR_NUMBER + 1):
            for c in range(CHAR_NUMBER + 1):
                flat[(p << 3) | c] = self.bonus_matrix[p][c]
        self.bonus_flat = flat

    def _bonus_for(self, prev_class, cls):
        if cls >= CHAR_NON_WORD:
            if prev_class == CHAR_WHITE:
                # Word boundary after whitespace
                return self.bonus_boundary_white
            if prev_class == CHAR_DELIMITER:
                # Word boundary after a delimiter character
                return self.bonus_boundary_delimiter
            if prev_class == CHAR_NON_WORD:
                # Word boundary
                return BONUS_BOUNDARY
        if (prev_class == CHAR_LOWER and cls == CHAR_UPPER) or \
                (prev_class != CHAR_NUMBER and cls == CHAR_NUMBER):
            # camelCase letter123
            return BONUS_CAMEL123
        if cls in (CHAR_NON_WORD, CHAR_DELIMITER):
            return BONUS_NON_WORD
        if cls == CHAR_WHITE:
            return self.bonus_boundary_white
        return 0

    def char_class_of_non_ascii(self, ch):
        cat = unicodedata.category(ch)
        if cat == "Ll":
            return CHAR_LOWER
        if cat == "Lu":
            return CHAR_UPPER
        if cat[0] == "N":
            return CHAR_NUMBER
        if cat[0] == "L":
            return CHAR_LETTER
        if is_space(ch):
            return CHAR_WHITE
        if ch in self.delimiter_chars:
            return CHAR_DELIMITER
        return CHAR_NON_WORD

    def char_class_of(self, ch):
        cp = ord(ch)
        if cp < 128:
            return self.ascii_char_classes[cp]
        return self.char_class_of_non_ascii(ch)

    def bonus_at(self, text, idx):
        if idx == 0:
            return self.bonus_boundary_white
        return self.bonus_matrix[self.char_class_of(text[idx - 1])][self.char_class_of(text[idx])]


DEFAULT_SCHEME = Scheme("default")
PATH_SCHEME = Scheme("path")
HISTORY_SCHEME = Scheme("history")

_SCHEMES = {"default": DEFAULT_SCHEME, "path": PATH_SCHEME, "history": HISTORY_SCHEME}


def get_scheme(name):
    try:
        return _SCHEMES[name]
    except KeyError:
        raise ValueError("invalid scheme: " + name)


class Slab:
    """Stand-in for fzf's util.Slab. Only the 16-bit capacity matters here:
    FuzzyMatchV2 falls back to V1 when the score matrix would not fit, and
    replicating that threshold is required for output parity with fzf."""

    __slots__ = ("cap16",)

    def __init__(self, cap16=100 * 1024):
        self.cap16 = cap16


def _index_at(index, size, forward):
    if forward:
        return index
    return size - index - 1


NO_MATCH = (-1, -1, 0)


def _ascii_fuzzy_index(text, lower_text, pattern, case_sensitive, text_is_ascii):
    """Returns (min_idx, max_idx) window of the text where a fuzzy match could
    exist, or (-1, -1) if there is none. Port of asciiFuzzyIndex."""
    # Can't determine
    if not text_is_ascii:
        return 0, len(text)

    hay = text if case_sensitive else lower_text
    first_idx, idx, last_idx = 0, 0, 0
    ch = ""
    for pidx, ch in enumerate(pattern):
        if ord(ch) >= 128:
            # Not possible: ASCII-only text cannot contain this pattern char
            return -1, -1
        idx = hay.find(ch, idx)
        if idx < 0:
            return -1, -1
        if pidx == 0 and idx > 0:
            # Step back to find the right bonus point
            first_idx = idx - 1
        last_idx = idx
        idx += 1

    # Find the last appearance of the last char of the pattern to limit the
    # search scope
    end = hay.rfind(ch, last_idx + 1)
    if end >= 0:
        return first_idx, end + 1
    return first_idx, last_idx + 1


def fuzzy_match_v2(case_sensitive, normalize, forward, text, pattern,
                   with_pos, slab=None, scheme=DEFAULT_SCHEME,
                   _force_generic=False):
    """Optimal fuzzy match (a modified Smith-Waterman). Port of FuzzyMatchV2."""
    M = len(pattern)
    if M == 0:
        return (0, 0, 0), ([] if with_pos else None)
    N = len(text)
    if M > N:
        return NO_MATCH, None

    # Since the O(nm) algorithm can be prohibitively expensive for large
    # input, fzf falls back to the greedy algorithm based on its slab
    # capacity; replicate the exact threshold for parity.
    if (slab is not None and N * M > slab.cap16) or M > 1000:
        return fuzzy_match_v1(case_sensitive, normalize, forward, text,
                              pattern, with_pos, slab, scheme)

    # Phase 1. Optimized search for ASCII string
    text_is_ascii = text.isascii()
    lower_text = text.lower() if (text_is_ascii and not case_sensitive) else text
    min_idx, max_idx = _ascii_fuzzy_index(text, lower_text, pattern,
                                          case_sensitive, text_is_ascii)
    if min_idx < 0:
        return NO_MATCH, None

    if text_is_ascii and not _force_generic:
        # ASCII fast path: identical scores and positions, computed with
        # C-speed scans (str.find / bytes.translate) instead of per-cell
        # Python loops.
        return _fuzzy_match_v2_ascii(case_sensitive, forward, text,
                                     lower_text, pattern, with_pos, min_idx,
                                     max_idx, scheme)

    N = max_idx - min_idx

    T = [ord(c) for c in text[min_idx:max_idx]]
    B = [0] * N
    H0 = [0] * N
    C0 = [0] * N
    F = [0] * M
    pattern = [ord(c) for c in pattern]

    # Phase 2. Calculate bonus for each point
    max_score, max_score_pos = 0, 0
    pidx, last_idx = 0, 0
    pchar0 = pchar = pattern[0]
    prev_h0 = 0
    prev_class = scheme.initial_char_class
    in_gap = False

    ascii_classes = scheme.ascii_char_classes
    bonus_matrix = scheme.bonus_matrix
    char_class_of_non_ascii = scheme.char_class_of_non_ascii

    for off in range(N):
        char = T[off]
        if char < 128:
            cls = ascii_classes[char]
            if not case_sensitive and cls == CHAR_UPPER:
                char += 32
                T[off] = char
        else:
            cls = char_class_of_non_ascii(chr(char))
            if not case_sensitive and cls == CHAR_UPPER:
                char = to_lower_rune(char)
            if normalize and 0x00C0 <= char <= 0xFF61:
                char = ord(NORMALIZED.get(chr(char), chr(char)))
            T[off] = char

        bonus = bonus_matrix[prev_class][cls]
        B[off] = bonus
        prev_class = cls

        if char == pchar:
            if pidx < M:
                F[pidx] = off
                pidx += 1
                pchar = pattern[min(pidx, M - 1)]
            last_idx = off

        if char == pchar0:
            score = SCORE_MATCH + bonus * BONUS_FIRST_CHAR_MULTIPLIER
            H0[off] = score
            C0[off] = 1
            if M == 1 and (score > max_score if forward else score >= max_score):
                max_score, max_score_pos = score, off
                if forward and bonus >= BONUS_BOUNDARY:
                    break
            in_gap = False
        else:
            if in_gap:
                h = prev_h0 + SCORE_GAP_EXTENSION
            else:
                h = prev_h0 + SCORE_GAP_START
            H0[off] = h if h > 0 else 0
            C0[off] = 0
            in_gap = True
        prev_h0 = H0[off]

    if pidx != M:
        return NO_MATCH, None
    if M == 1:
        result = (min_idx + max_score_pos, min_idx + max_score_pos + 1, max_score)
        if not with_pos:
            return result, None
        return result, [min_idx + max_score_pos]

    # Phase 3. Fill in score matrix (H). Unlike the original algorithm,
    # omission of a pattern character is not allowed.
    f0 = F[0]
    width = last_idx - f0 + 1
    size = width * M
    H = [0] * size
    H[0:last_idx + 1 - f0] = H0[f0:last_idx + 1]
    C = [0] * size
    C[0:last_idx + 1 - f0] = C0[f0:last_idx + 1]

    for i in range(1, M):
        f = F[i]
        pchar = pattern[i]
        row = i * width
        in_gap = False
        H[row + f - f0 - 1] = 0  # Hleft[0] = 0
        base = row - f0
        dbase = base - width - 1
        for j in range(f, last_idx + 1):
            col_idx = base + j
            if in_gap:
                s2 = H[col_idx - 1] + SCORE_GAP_EXTENSION
            else:
                s2 = H[col_idx - 1] + SCORE_GAP_START

            consecutive = 0
            if pchar == T[j]:
                s1 = H[dbase + j] + SCORE_MATCH
                b = B[j]
                consecutive = C[dbase + j] + 1
                if consecutive > 1:
                    fb = B[j - consecutive + 1]
                    # Break consecutive chunk
                    if b >= BONUS_BOUNDARY and b > fb:
                        consecutive = 1
                    else:
                        b = max(b, BONUS_CONSECUTIVE, fb)
                if s1 + b < s2:
                    s1 += B[j]
                    consecutive = 0
                else:
                    s1 += b
            else:
                s1 = 0
            C[col_idx] = consecutive

            in_gap = s1 < s2
            score = s1 if s1 > s2 else s2
            if score < 0:
                score = 0
            if i == M - 1 and (score > max_score if forward else score >= max_score):
                max_score, max_score_pos = score, j
            H[col_idx] = score

    # Phase 4. (Optional) Backtrace to find character positions
    pos = [] if with_pos else None
    j = f0
    if with_pos:
        i = M - 1
        j = max_score_pos
        prefer_match = True
        while True:
            I = i * width
            j0 = j - f0
            s = H[I + j0]

            s1 = 0
            s2 = 0
            if i > 0 and j >= F[i]:
                s1 = H[I - width + j0 - 1]
            if j > F[i]:
                s2 = H[I + j0 - 1]

            if s > s1 and (s > s2 or (s == s2 and prefer_match)):
                pos.append(j + min_idx)
                if i == 0:
                    break
                i -= 1
            prefer_match = C[I + j0] > 1 or \
                (I + width + j0 + 1 < size and C[I + width + j0 + 1] > 0)
            j -= 1

    # Start offset we return here is only relevant when begin tiebreak is
    # used, same caveat as upstream.
    return (min_idx + j, min_idx + max_score_pos + 1, max_score), pos


def _fuzzy_match_v2_ascii(case_sensitive, forward, text, lower_text, pattern,
                          with_pos, min_idx, max_idx, scheme):
    """ASCII fast path of FuzzyMatchV2. Match cells are located with
    str.find and the gap cells between them are filled analytically (the
    score of a gap run decays by the gap penalties and clamps at zero), so
    the heavy per-cell work only happens at candidate positions."""
    M = len(pattern)
    hay = text if case_sensitive else lower_text
    twin = hay[min_idx:max_idx]
    last = max_idx - min_idx - 1
    tfind = twin.find
    GS, GE, SM = SCORE_GAP_START, SCORE_GAP_EXTENSION, SCORE_MATCH
    BB, BC, FCM = BONUS_BOUNDARY, BONUS_CONSECUTIVE, \
        BONUS_FIRST_CHAR_MULTIPLIER

    # Bonus for each position, derived from the original (un-folded) chars
    cls = text[min_idx:max_idx].encode("ascii").translate(
        scheme.ascii_class_table)
    if min_idx > 0:
        prev0 = scheme.ascii_char_classes[ord(text[min_idx - 1])]
    else:
        prev0 = scheme.initial_char_class
    bm = scheme.bonus_flat

    # Bonus values are only needed at candidate match positions, so they
    # are computed on demand from the class bytes instead of materializing
    # a bonus array for the whole window.
    if M == 1:
        ch = pattern
        max_score, max_pos = 0, 0
        j = tfind(ch)
        while j >= 0:
            bonus = bm[((cls[j - 1] if j else prev0) << 3) | cls[j]]
            score = SM + bonus * FCM
            if score > max_score if forward else score >= max_score:
                max_score, max_pos = score, j
                if forward and bonus >= BB:
                    break
            j = tfind(ch, j + 1)
        result = (min_idx + max_pos, min_idx + max_pos + 1, max_score)
        return result, ([min_idx + max_pos] if with_pos else None)

    # The first occurrence of each pattern character (Go's F); the chain is
    # guaranteed to exist because the prefilter succeeded
    F = []
    idx = 0
    for ch in pattern:
        idx = tfind(ch, idx)
        F.append(idx)
        idx += 1

    f0 = F[0]
    width = last - f0 + 1

    h_rows = [None] * M if with_pos else None
    c_rows = [None] * M if with_pos else None

    # Row 0 (Go's H0/C0 restricted to the columns that matter)
    h_prev = [0] * width
    c_prev = [0] * width
    pchar = pattern[0]
    h = 0
    in_gap = False
    j = f0
    while j <= last:
        nxt = tfind(pchar, j)
        if nxt < 0 or nxt > last:
            nxt = last + 1
        run = nxt - j
        if run:
            first = h + (GE if in_gap else GS)
            base = j - f0
            if first <= 0:
                h = 0
            elif first - run + 1 > 0:
                h_prev[base:base + run] = range(first, first - run, -1)
                h = first - run + 1
            else:
                h_prev[base:base + first] = range(first, 0, -1)
                h = 0
            in_gap = True
        if nxt > last:
            break
        h = SM + bm[((cls[nxt - 1] if nxt else prev0) << 3) | cls[nxt]] * FCM
        h_prev[nxt - f0] = h
        c_prev[nxt - f0] = 1
        in_gap = False
        j = nxt + 1

    # Rows 1..M-1
    max_score, max_pos = 0, 0
    if with_pos:
        h_rows[0] = h_prev
        c_rows[0] = c_prev
    for i in range(1, M):
        pchar = pattern[i]
        f = F[i]
        h_cur = [0] * width
        c_cur = [0] * width
        in_gap = False
        h_left = 0
        is_last = i == M - 1
        j = f
        while j <= last:
            nxt = tfind(pchar, j)
            if nxt < 0 or nxt > last:
                nxt = last + 1
            run = nxt - j
            if run:
                # Non-match cells: s1 = 0, so the score decays from the left
                # neighbor and clamps at zero
                first = h_left + (GE if in_gap else GS)
                base = j - f0
                if first <= 0:
                    h_left = 0
                elif first - run + 1 > 0:
                    h_cur[base:base + run] = range(first, first - run, -1)
                    h_left = first - run + 1
                else:
                    h_cur[base:base + first] = range(first, 0, -1)
                    h_left = 0
                in_gap = h_left > 0
            if nxt > last:
                break
            s2 = h_left + (GE if in_gap else GS)
            diag = nxt - 1 - f0
            s1 = h_prev[diag] + SM
            consecutive = c_prev[diag] + 1
            b0 = b = bm[((cls[nxt - 1] if nxt else prev0) << 3) | cls[nxt]]
            if consecutive > 1:
                start = nxt - consecutive + 1
                fb = bm[((cls[start - 1] if start else prev0) << 3) |
                        cls[start]]
                # Break consecutive chunk
                if b >= BB and b > fb:
                    consecutive = 1
                else:
                    if BC > b:
                        b = BC
                    if fb > b:
                        b = fb
            if s1 + b < s2:
                s1 += b0
                consecutive = 0
            else:
                s1 += b
            c_cur[nxt - f0] = consecutive
            in_gap = s1 < s2
            score = s1 if s1 > s2 else s2
            if score < 0:
                score = 0
            if is_last and (score > max_score if forward
                            else score >= max_score):
                max_score, max_pos = score, nxt
            h_cur[nxt - f0] = score
            h_left = score
            j = nxt + 1
        if with_pos:
            h_rows[i] = h_cur
            c_rows[i] = c_cur
        h_prev, c_prev = h_cur, c_cur

    # Backtrace (same traversal as the generic path, over row lists)
    pos = [] if with_pos else None
    j = f0
    if with_pos:
        i = M - 1
        j = max_pos
        prefer_match = True
        while True:
            row = i
            j0 = j - f0
            s = h_rows[row][j0]
            s1 = 0
            s2 = 0
            if i > 0 and j >= F[i]:
                s1 = h_rows[row - 1][j0 - 1]
            if j > F[i]:
                s2 = h_rows[row][j0 - 1]
            if s > s1 and (s > s2 or (s == s2 and prefer_match)):
                pos.append(j + min_idx)
                if i == 0:
                    break
                i -= 1
            prefer_match = c_rows[row][j0] > 1 or \
                (row + 1 < M and j0 + 1 < width and
                 c_rows[row + 1][j0 + 1] > 0)
            j -= 1

    return (min_idx + j, min_idx + max_pos + 1, max_score), pos


def _calculate_score(case_sensitive, normalize, text, pattern, sidx, eidx,
                     with_pos, scheme):
    """Implements the same scoring criteria as V2. Port of calculateScore."""
    pidx, score, in_gap, consecutive, first_bonus = 0, 0, False, 0, 0
    pos = [] if with_pos else None
    prev_class = scheme.initial_char_class
    if sidx > 0:
        prev_class = scheme.char_class_of(text[sidx - 1])
    pattern_cps = [ord(c) for c in pattern]
    bonus_matrix = scheme.bonus_matrix
    for idx in range(sidx, eidx):
        ch = text[idx]
        char = ord(ch)
        cls = scheme.char_class_of(ch)
        if not case_sensitive:
            if 65 <= char <= 90:
                char += 32
            elif char > 127:
                char = to_lower_rune(char)
        # pattern is already normalized
        if normalize and 0x00C0 <= char <= 0xFF61:
            char = ord(NORMALIZED.get(chr(char), chr(char)))
        if char == pattern_cps[pidx]:
            if with_pos:
                pos.append(idx)
            score += SCORE_MATCH
            bonus = bonus_matrix[prev_class][cls]
            if consecutive == 0:
                first_bonus = bonus
            else:
                # Break consecutive chunk
                if bonus >= BONUS_BOUNDARY and bonus > first_bonus:
                    first_bonus = bonus
                bonus = max(bonus, first_bonus, BONUS_CONSECUTIVE)
            if pidx == 0:
                score += bonus * BONUS_FIRST_CHAR_MULTIPLIER
            else:
                score += bonus
            in_gap = False
            consecutive += 1
            pidx += 1
        else:
            if in_gap:
                score += SCORE_GAP_EXTENSION
            else:
                score += SCORE_GAP_START
            in_gap = True
            consecutive = 0
            first_bonus = 0
        prev_class = cls
    return score, pos


def fuzzy_match_v1(case_sensitive, normalize, forward, text, pattern,
                   with_pos, slab=None, scheme=DEFAULT_SCHEME):
    """Greedy fuzzy match. Port of FuzzyMatchV1."""
    if len(pattern) == 0:
        return (0, 0, 0), None
    text_is_ascii = text.isascii()
    lower_text = text.lower() if (text_is_ascii and not case_sensitive) else text
    idx, _ = _ascii_fuzzy_index(text, lower_text, pattern, case_sensitive,
                                text_is_ascii)
    if idx < 0:
        return NO_MATCH, None

    pidx = 0
    sidx = -1
    eidx = -1

    len_runes = len(text)
    len_pattern = len(pattern)
    pattern_cps = [ord(c) for c in pattern]

    for index in range(len_runes):
        char = ord(text[_index_at(index, len_runes, forward)])
        if not case_sensitive:
            if 65 <= char <= 90:
                char += 32
            elif char > 127:
                char = to_lower_rune(char)
        if normalize and 0x00C0 <= char <= 0xFF61:
            char = ord(NORMALIZED.get(chr(char), chr(char)))
        pchar = pattern_cps[_index_at(pidx, len_pattern, forward)]
        if char == pchar:
            if sidx < 0:
                sidx = index
            pidx += 1
            if pidx == len_pattern:
                eidx = index + 1
                break

    if sidx >= 0 and eidx >= 0:
        pidx -= 1
        for index in range(eidx - 1, sidx - 1, -1):
            tidx = _index_at(index, len_runes, forward)
            char = ord(text[tidx])
            if not case_sensitive:
                if 65 <= char <= 90:
                    char += 32
                elif char > 127:
                    char = to_lower_rune(char)
            if normalize and 0x00C0 <= char <= 0xFF61:
                char = ord(NORMALIZED.get(chr(char), chr(char)))
            pidx_ = _index_at(pidx, len_pattern, forward)
            pchar = pattern_cps[pidx_]
            if char == pchar:
                pidx -= 1
                if pidx < 0:
                    sidx = index
                    break

        if not forward:
            sidx, eidx = len_runes - eidx, len_runes - sidx

        score, pos = _calculate_score(case_sensitive, normalize, text,
                                      pattern, sidx, eidx, with_pos, scheme)
        return (sidx, eidx, score), pos
    return NO_MATCH, None


def exact_match_naive(case_sensitive, normalize, forward, text, pattern,
                      with_pos, slab=None, scheme=DEFAULT_SCHEME):
    """Exact match searching for the occurrence with the highest bonus point.
    Port of ExactMatchNaive."""
    return _exact_match_naive(case_sensitive, normalize, forward, False, text,
                              pattern, with_pos, slab, scheme)


def exact_match_boundary(case_sensitive, normalize, forward, text, pattern,
                         with_pos, slab=None, scheme=DEFAULT_SCHEME):
    """Exact match at word boundaries ('-quoted term). Port of
    ExactMatchBoundary."""
    return _exact_match_naive(case_sensitive, normalize, forward, True, text,
                              pattern, with_pos, slab, scheme)


def _exact_match_naive(case_sensitive, normalize, forward, boundary_check,
                       text, pattern, with_pos, slab, scheme):
    if len(pattern) == 0:
        return (0, 0, 0), None

    len_runes = len(text)
    len_pattern = len(pattern)

    if len_runes < len_pattern:
        return NO_MATCH, None

    text_is_ascii = text.isascii()
    lower_text = text.lower() if (text_is_ascii and not case_sensitive) else text
    idx, _ = _ascii_fuzzy_index(text, lower_text, pattern, case_sensitive,
                                text_is_ascii)
    if idx < 0:
        return NO_MATCH, None

    if text_is_ascii and pattern.isascii() and not boundary_check:
        return _exact_match_ascii(case_sensitive, normalize, forward, text,
                                  lower_text, pattern, scheme)

    # For simplicity, only look at the bonus at the first character position
    pattern_cps = [ord(c) for c in pattern]
    pidx = 0
    best_pos, bonus, bbonus, best_bonus = -1, 0, 0, -1
    index = 0
    while index < len_runes:
        index_ = _index_at(index, len_runes, forward)
        char = ord(text[index_])
        if not case_sensitive:
            if 65 <= char <= 90:
                char += 32
            elif char > 127:
                char = to_lower_rune(char)
        if normalize and 0x00C0 <= char <= 0xFF61:
            char = ord(NORMALIZED.get(chr(char), chr(char)))
        pidx_ = _index_at(pidx, len_pattern, forward)
        pchar = pattern_cps[pidx_]
        ok = pchar == char
        if ok:
            if pidx_ == 0:
                bonus = scheme.bonus_at(text, index_)
            if boundary_check:
                if forward and pidx_ == 0:
                    bbonus = bonus
                elif not forward and pidx_ == len_pattern - 1:
                    if index_ < len_runes - 1:
                        bbonus = scheme.bonus_at(text, index_ + 1)
                    else:
                        bbonus = scheme.bonus_boundary_white
                ok = bbonus >= BONUS_BOUNDARY
                if ok and pidx_ == 0:
                    ok = index_ == 0 or \
                        scheme.char_class_of(text[index_ - 1]) <= CHAR_DELIMITER
                if ok and pidx_ == len_pattern - 1:
                    ok = index_ == len_runes - 1 or \
                        scheme.char_class_of(text[index_ + 1]) <= CHAR_DELIMITER
        if ok:
            pidx += 1
            if pidx == len_pattern:
                if bonus > best_bonus:
                    best_pos, best_bonus = index, bonus
                if bonus >= BONUS_BOUNDARY:
                    break
                index -= pidx - 1
                pidx, bonus = 0, 0
        else:
            index -= pidx
            pidx, bonus = 0, 0
        index += 1

    if best_pos >= 0:
        if forward:
            sidx = best_pos - len_pattern + 1
            eidx = best_pos + 1
        else:
            sidx = len_runes - (best_pos + 1)
            eidx = len_runes - (best_pos - len_pattern + 1)
        if boundary_check:
            # Underscore boundaries should be ranked lower than the other
            # types of boundaries
            score = bonus
            deduct = bonus - BONUS_BOUNDARY + 1
            if sidx > 0 and text[sidx - 1] == "_":
                score -= deduct + 1
                deduct = 1
            if eidx < len_runes and text[eidx] == "_":
                score -= deduct
            # Add base score so that this can compete with other match types
            score += SCORE_MATCH * len_pattern + \
                scheme.bonus_boundary_white * (len_pattern + 1)
        else:
            score, _ = _calculate_score(case_sensitive, normalize, text,
                                        pattern, sidx, eidx, False, scheme)
        return (sidx, eidx, score), None
    return NO_MATCH, None


def _exact_match_ascii(case_sensitive, normalize, forward, text, lower_text,
                       pattern, scheme):
    """Fast path of _exact_match_naive for ASCII text and pattern, using
    C-speed substring search to enumerate candidate positions. Semantics are
    identical: scan occurrences in the search direction, keep the one with
    the highest bonus at the pattern start, stop early at a word boundary."""
    hay = text if case_sensitive else lower_text
    needle = pattern
    len_runes = len(text)
    len_pattern = len(pattern)
    best_start, best_bonus = -1, -1
    if forward:
        start = hay.find(needle)
        while start >= 0:
            bonus = scheme.bonus_at(text, start)
            if bonus > best_bonus:
                best_start, best_bonus = start, bonus
            if bonus >= BONUS_BOUNDARY:
                break
            start = hay.find(needle, start + 1)
    else:
        start = hay.rfind(needle)
        while start >= 0:
            bonus = scheme.bonus_at(text, start)
            if bonus > best_bonus:
                best_start, best_bonus = start, bonus
            if bonus >= BONUS_BOUNDARY:
                break
            start = hay.rfind(needle, 0, start + len_pattern - 1)
    if best_start < 0:
        return NO_MATCH, None
    sidx = best_start
    eidx = best_start + len_pattern
    score, _ = _calculate_score(case_sensitive, normalize, text, pattern,
                                sidx, eidx, False, scheme)
    return (sidx, eidx, score), None


def prefix_match(case_sensitive, normalize, forward, text, pattern,
                 with_pos, slab=None, scheme=DEFAULT_SCHEME):
    """Port of PrefixMatch."""
    if len(pattern) == 0:
        return (0, 0, 0), None

    trimmed_len = 0
    if not is_space(pattern[0]):
        trimmed_len = leading_whitespaces(text)

    if len(text) - trimmed_len < len(pattern):
        return NO_MATCH, None

    len_pattern = len(pattern)
    if text.isascii():
        sub = text[trimmed_len:trimmed_len + len_pattern]
        if not case_sensitive:
            sub = sub.lower()
        if sub != pattern:
            return NO_MATCH, None
    else:
        for index, r in enumerate(pattern):
            char = ord(text[trimmed_len + index])
            if not case_sensitive:
                char = to_lower_rune(char)
            if normalize and 0x00C0 <= char <= 0xFF61:
                char = ord(NORMALIZED.get(chr(char), chr(char)))
            if char != ord(r):
                return NO_MATCH, None
    score, _ = _calculate_score(case_sensitive, normalize, text, pattern,
                                trimmed_len, trimmed_len + len_pattern, False,
                                scheme)
    return (trimmed_len, trimmed_len + len_pattern, score), None


def suffix_match(case_sensitive, normalize, forward, text, pattern,
                 with_pos, slab=None, scheme=DEFAULT_SCHEME):
    """Port of SuffixMatch."""
    len_runes = len(text)
    trimmed_len = len_runes
    if len(pattern) == 0 or not is_space(pattern[-1]):
        trimmed_len -= trailing_whitespaces(text)
    if len(pattern) == 0:
        return (trimmed_len, trimmed_len, 0), None
    diff = trimmed_len - len(pattern)
    if diff < 0:
        return NO_MATCH, None

    if text.isascii():
        sub = text[diff:trimmed_len]
        if not case_sensitive:
            sub = sub.lower()
        if sub != pattern:
            return NO_MATCH, None
    else:
        for index, r in enumerate(pattern):
            char = ord(text[index + diff])
            if not case_sensitive:
                char = to_lower_rune(char)
            if normalize and 0x00C0 <= char <= 0xFF61:
                char = ord(NORMALIZED.get(chr(char), chr(char)))
            if char != ord(r):
                return NO_MATCH, None
    len_pattern = len(pattern)
    sidx = trimmed_len - len_pattern
    eidx = trimmed_len
    score, _ = _calculate_score(case_sensitive, normalize, text, pattern,
                                sidx, eidx, False, scheme)
    return (sidx, eidx, score), None


def equal_match(case_sensitive, normalize, forward, text, pattern,
                with_pos, slab=None, scheme=DEFAULT_SCHEME):
    """Port of EqualMatch."""
    len_pattern = len(pattern)
    if len_pattern == 0:
        return NO_MATCH, None

    # Strip leading whitespaces
    trimmed_len = 0
    if not is_space(pattern[0]):
        trimmed_len = leading_whitespaces(text)

    # Strip trailing whitespaces
    trimmed_end_len = 0
    if not is_space(pattern[-1]):
        trimmed_end_len = trailing_whitespaces(text)

    if len(text) - trimmed_len - trimmed_end_len != len_pattern:
        return NO_MATCH, None
    match = True
    if normalize:
        for idx, pchar in enumerate(pattern):
            char = ord(text[trimmed_len + idx])
            if not case_sensitive:
                char = to_lower_rune(char)
            pcp = ord(pchar)
            if 0x00C0 <= pcp <= 0xFF61:
                pcp = ord(NORMALIZED.get(pchar, pchar))
            if 0x00C0 <= char <= 0xFF61:
                char = ord(NORMALIZED.get(chr(char), chr(char)))
            if pcp != char:
                match = False
                break
    else:
        sub = text[trimmed_len:len(text) - trimmed_end_len]
        if not case_sensitive:
            if sub.isascii():
                sub = sub.lower()
            else:
                sub = "".join(chr(to_lower_rune(ord(c))) for c in sub)
        match = sub == pattern
    if match:
        return (trimmed_len, trimmed_len + len_pattern,
                (SCORE_MATCH + scheme.bonus_boundary_white) * len_pattern +
                (BONUS_FIRST_CHAR_MULTIPLIER - 1) * scheme.bonus_boundary_white), \
            None
    return NO_MATCH, None
