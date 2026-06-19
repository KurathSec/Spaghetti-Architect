# Spaghetti Architect — generated module: lookup
# Deliberately redundant, but syntactically correct and crash-free.

# --- run fixtures (inputs) ---
config_db = {'dev': 'localhost', 'prod': '10.0.0.1'}
input_key = 'dev'

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
