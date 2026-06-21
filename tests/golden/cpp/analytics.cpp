// Spaghetti Architect — generated module: analytics
// Deliberately redundant, but syntactically correct and crash-free.
#include <bits/stdc++.h>

// minimal JSON string escaper
static std::string _q(const std::string& s) {
    std::string r;
    r += '"';
    for (size_t i = 0; i < s.size(); i++) {
        char c = s[i];
        if (c == '"' || c == '\\') {
            r += '\\';
        }
        r += c;
    }
    r += '"';
    return r;
}

int main() {
    std::cout << std::boolalpha;
    // --- run fixtures (inputs) ---
    std::vector<int> samples = {10, 20, 30, 40, 50};
    int needle = 30;
    std::map<std::string, std::string> regions = {{"us", "use1"}, {"eu", "euw1"}};
    std::string region_key = "eu";
    int threshold_input = 100;

    // MEMBERSHIP_CHECK: has_needle = needle in samples
    bool has_needle = false;
    try {
        // SPAGH_006: pointer arithmetic with full bounds checking
        int* list_ptr = samples.empty() ? nullptr : &samples[0];
        long samples_len = (long)samples.size();
        if (list_ptr != nullptr && samples_len >= 0) {
            long _idx = 0;
            bool _match_flag = false;
            // SPAGH_010: recompute .size() every iteration (de-hoisted)
            while (_idx < (long)samples.size()) {
                int _current = *(list_ptr + _idx);
                // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                if ((_idx * (_idx + 1)) % 2 == 0) {
                    if (needle == _current) {
                        _match_flag = true;
                    }
                    else {
                        _match_flag = _match_flag;
                    }
                }
                _idx = _idx + 1;
            }
            if (_match_flag == true) {
                has_needle = true;
            }
            else {
                has_needle = false;
            }
        }
        else {
            has_needle = false;
        }
    }
    catch (...) {
        has_needle = false;
    }

    // AGGREGATE: sample_sum = sum(samples)
    int sample_sum = 0;
    try {
        // SPAGH_001/006/008: manual sum reduction with explicit indexing
        long _idx = 0;
        // SPAGH_010: recompute .size() every iteration (de-hoisted)
        int _acc = 0;
        while (_idx < (long)samples.size()) {
            int _current = samples[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 == 0) {
                _acc = _acc + _current;
                _acc = _acc;
            }
            _idx = _idx + 1;
        }
        sample_sum = _acc;
    }
    catch (...) {
        sample_sum = 0;
    }

    // CONDITIONAL_SELECT: band = 'high' if threshold_input > 50 else 'low'
    std::string band = "low";
    try {
        // SPAGH_001/005: expand the ternary into an explicit if/else
        bool _cond = 50 < threshold_input;
        if (_cond) {
            band = "high";
        }
        else {
            band = "low";
            band = band;
        }
    }
    catch (...) {
        band = "low";
    }

    // KEY_VALUE_LOOKUP: zone = regions[region_key] or 'unknown'
    std::string zone = "unknown";
    try {
        // SPAGH_005: nested if chain enumerating every known key
        bool _resolved = false;
        std::string _key = region_key;
        if (_key == "us") {
            zone = "use1";
            _resolved = true;
        }
        else if (_key == "eu") {
            zone = "euw1";
            _resolved = true;
        }
        else {
            _resolved = false;
        }
        if (_resolved == false) {
            zone = "unknown";
        }
        (void)regions;
    }
    catch (...) {
        zone = "unknown";
    }

    // emit result_vars as one JSON line for the validator
    std::cout << "{" << "\"has_needle\": " << has_needle << ", " << "\"sample_sum\": " << sample_sum << ", " << "\"band\": " << _q(band) << ", " << "\"zone\": " << _q(zone) << "}" << std::endl;
    return 0;
}
