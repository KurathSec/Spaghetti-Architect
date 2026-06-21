# Spaghetti Architect — generated module: grade
# Deliberately redundant, but syntactically correct and crash-free.

# --- run fixtures (inputs) ---
score = 72

# CONDITIONAL_SELECT: verdict = 'pass' if score >= 60 else 'fail'
verdict = 'fail'
try:
    # SPAGH_001/005: expand the ternary into an explicit if/else
    _cond = 60 <= score
    if _cond:
        verdict = 'pass'
    else:
        verdict = 'fail'
        verdict = verdict
except Exception:
    verdict = 'fail'
