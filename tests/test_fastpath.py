"""Cross-check the ASCII fast path of FuzzyMatchV2 against the generic
(direct Go port) implementation on randomized inputs. Both must agree on
score, offsets, and positions for every case."""

import random
import string

import pytest

from purefzf.algo import (DEFAULT_SCHEME, PATH_SCHEME, fuzzy_match_v2)

ALPHABET = string.ascii_letters + string.digits + " /_-.,:'!|$^"


@pytest.mark.parametrize("seed", range(20))
def test_fast_path_equals_generic(seed):
    rng = random.Random(seed)
    schemes = [DEFAULT_SCHEME, PATH_SCHEME]
    for _ in range(300):
        text = "".join(rng.choice(ALPHABET)
                       for _ in range(rng.randint(1, 60)))
        plen = rng.randint(1, 6)
        if rng.random() < 0.5:
            # Sample pattern chars from the text to get plenty of matches
            pattern = "".join(rng.choice(text) for _ in range(plen)).lower()
        else:
            pattern = "".join(rng.choice(string.ascii_lowercase + "/. ")
                              for _ in range(plen))
        case_sensitive = rng.random() < 0.3
        if case_sensitive:
            pattern = pattern  # pattern may contain any case via text sample
        forward = rng.random() < 0.7
        with_pos = rng.random() < 0.7
        scheme = rng.choice(schemes)

        fast = fuzzy_match_v2(case_sensitive, False, forward, text, pattern,
                              with_pos, None, scheme)
        generic = fuzzy_match_v2(case_sensitive, False, forward, text,
                                 pattern, with_pos, None, scheme,
                                 _force_generic=True)
        assert fast == generic, (
            "fast=%r generic=%r text=%r pattern=%r cs=%r fwd=%r pos=%r "
            "scheme=%s" % (fast, generic, text, pattern, case_sensitive,
                           forward, with_pos, scheme.name))
