# Spaghetti Architect — generated module: analytics
# Deliberately redundant, but syntactically correct and crash-free.

# --- run fixtures (inputs) ---
samples = [10, 20, 30, 40, 50]
needle = 30
regions = {'us': 'use1', 'eu': 'euw1'}
region_key = 'eu'
threshold_input = 100

# MEMBERSHIP_CHECK: has_needle = needle in samples
has_needle = False
try:
    if samples is not None:
        # SPAGH_001/006/008: manual index loop instead of `in`
        _idx = 0
        # SPAGH_010: recompute len() every iteration (de-hoisted)
        _match_flag = False
        while _idx < len(samples):
            _current = samples[_idx]
            # SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if (_idx * (_idx + 1)) % 2 == 0:
                if needle == _current:
                    _match_flag = True
                else:
                    _match_flag = _match_flag
            _idx = _idx + 1
        if _match_flag == True:
            has_needle = True
        else:
            has_needle = False
    else:
        has_needle = False
except Exception:
    has_needle = False

# AGGREGATE: sample_sum = sum(samples)
sample_sum = 0
try:
    if samples is not None:
        # SPAGH_001/006/008: manual sum reduction instead of sum()
        _idx = 0
        # SPAGH_010: recompute len() every iteration (de-hoisted)
        _acc = 0
        while _idx < len(samples):
            _current = samples[_idx]
            # SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if (_idx * (_idx + 1)) % 2 == 0:
                _acc = _acc + _current
                _acc = _acc
            _idx = _idx + 1
        sample_sum = _acc
    else:
        sample_sum = 0
except Exception:
    sample_sum = 0

# CONDITIONAL_SELECT: band = 'high' if threshold_input > 50 else 'low'
band = 'low'
try:
    # SPAGH_001/005: expand the ternary into an explicit if/else
    _cond = 50 < threshold_input
    if _cond:
        band = 'high'
    else:
        band = 'low'
        band = band
except Exception:
    band = 'low'

# KEY_VALUE_LOOKUP: zone = regions[region_key] or 'unknown'
zone = 'unknown'
try:
    if regions is not None:
        # SPAGH_005: cascade enumerating every known key
        _resolved = False
        _key = region_key
        if _key == 'us':
            zone = 'use1'
            _resolved = True
        elif _key == 'eu':
            zone = 'euw1'
            _resolved = True
        else:
            _resolved = False
        if _resolved == False:
            zone = 'unknown'
    else:
        zone = 'unknown'
except Exception:
    zone = 'unknown'
