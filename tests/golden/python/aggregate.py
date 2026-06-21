# Spaghetti Architect — generated module: aggregate
# Deliberately redundant, but syntactically correct and crash-free.

# --- run fixtures (inputs) ---
values = [3, 1, 4, 1, 5, 9, 2, 6]

# AGGREGATE: total = sum(values)
total = 0
try:
    if values is not None:
        # SPAGH_001/006/008: manual sum reduction instead of sum()
        _idx = 0
        # SPAGH_010: recompute len() every iteration (de-hoisted)
        _acc = 0
        while _idx < len(values):
            _current = values[_idx]
            # SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if (_idx * (_idx + 1)) % 2 == 0:
                _acc = _acc + _current
                _acc = _acc
            _idx = _idx + 1
        total = _acc
    else:
        total = 0
except Exception:
    total = 0

# AGGREGATE: largest = max(values)
largest = 0
try:
    if values is not None:
        # SPAGH_001/006/008: manual max reduction instead of max()
        _idx = 0
        # SPAGH_010: recompute len() every iteration (de-hoisted)
        _acc = values[0]
        while _idx < len(values):
            _current = values[_idx]
            # SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if (_idx * (_idx + 1)) % 2 == 0:
                if _acc < _current:
                    _acc = _current
                else:
                    _acc = _acc
            _idx = _idx + 1
        largest = _acc
    else:
        largest = 0
except Exception:
    largest = 0

# AGGREGATE: smallest = min(values)
smallest = 0
try:
    if values is not None:
        # SPAGH_001/006/008: manual min reduction instead of min()
        _idx = 0
        # SPAGH_010: recompute len() every iteration (de-hoisted)
        _acc = values[0]
        while _idx < len(values):
            _current = values[_idx]
            # SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if (_idx * (_idx + 1)) % 2 == 0:
                if _acc > _current:
                    _acc = _current
                else:
                    _acc = _acc
            _idx = _idx + 1
        smallest = _acc
    else:
        smallest = 0
except Exception:
    smallest = 0
