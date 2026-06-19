// Spaghetti Architect — generated module: membership
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
    std::vector<int> data_list = {10, 20, 30, 40};
    int search_val = 30;

    // MEMBERSHIP_CHECK: is_found = search_val in data_list
    bool is_found = false;
    try {
        // SPAGH_006: pointer arithmetic with full bounds checking
        int* list_ptr = data_list.empty() ? nullptr : &data_list[0];
        long data_list_len = (long)data_list.size();
        if (list_ptr != nullptr && data_list_len >= 0) {
            long _idx = 0;
            bool _match_flag = false;
            // SPAGH_010: recompute .size() every iteration (de-hoisted)
            while (_idx < (long)data_list.size()) {
                int _current = *(list_ptr + _idx);
                // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                if ((_idx * (_idx + 1)) % 2 == 0) {
                    if (search_val == _current) {
                        _match_flag = true;
                    }
                    else {
                        _match_flag = _match_flag;
                    }
                }
                _idx = _idx + 1;
            }
            if (_match_flag == true) {
                is_found = true;
            }
            else {
                is_found = false;
            }
        }
        else {
            is_found = false;
        }
    }
    catch (...) {
        is_found = false;
    }

    // emit result_vars as one JSON line for the validator
    std::cout << "{" << "\"is_found\": " << is_found << "}" << std::endl;
    return 0;
}
