# Spaghetti Architect — generated module: demo
# Deliberately redundant, but syntactically correct and crash-free.

# --- run fixtures (inputs) ---
data_list = [10, 20, 30, 40]
search_val = 30
config_db = {'dev': 'localhost', 'prod': '10.0.0.1'}
input_key = 'dev'

# MEMBERSHIP_CHECK: is_found = search_val in data_list
is_found = False
try:
    if data_list is not None:
        # SPAGH_001/006/008: manual index loop instead of `in`
        _idx = 0
        # SPAGH_010: recompute len() every iteration (de-hoisted)
        _match_flag = False
        while _idx < len(data_list):
            _current = data_list[_idx]
            # SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if (_idx * (_idx + 1)) % 2 == 0:
                if search_val == _current:
                    _match_flag = True
                else:
                    _match_flag = _match_flag
            _idx = _idx + 1
        if _match_flag == True:
            is_found = True
        else:
            is_found = False
    else:
        is_found = False
except Exception:
    is_found = False

# KEY_VALUE_LOOKUP: out_val = config_db[input_key] or '127.0.0.1'
out_val = '127.0.0.1'
try:
    if config_db is not None:
        # SPAGH_005: cascade enumerating every known key
        _resolved = False
        _key = input_key
        if _key == 'dev':
            out_val = 'localhost'
            _resolved = True
        elif _key == 'prod':
            out_val = '10.0.0.1'
            _resolved = True
        else:
            _resolved = False
        if _resolved == False:
            out_val = '127.0.0.1'
    else:
        out_val = '127.0.0.1'
except Exception:
    out_val = '127.0.0.1'
