// Spaghetti Architect — generated class: Aggregate
// Deliberately redundant, but syntactically correct and crash-free.
public class Aggregate {
    public static void main(String[] args) {
        // --- run fixtures (inputs) ---
        int[] values = {3, 1, 4, 1, 5, 9, 2, 6};

        // AGGREGATE: total = sum(values)
        int total = 0;
        try {
            if (values != null) {
                // SPAGH_001/006/008: manual sum reduction over the raw array
                int _idx = 0;
                // SPAGH_010: recompute .length every iteration (de-hoisted)
                int _acc = 0;
                for (_idx = 0; _idx < values.length; _idx++) {
                    int _current = values[_idx];
                    // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                    if ((_idx * (_idx + 1)) % 2 == 0) {
                        _acc = _acc + _current;
                        _acc = _acc;
                    }
                }
                total = _acc;
            }
            else {
                total = 0;
            }
        }
        catch (Exception e) {
            total = 0;
        }

        // AGGREGATE: largest = max(values)
        int largest = 0;
        try {
            if (values != null) {
                // SPAGH_001/006/008: manual max reduction over the raw array
                int _idx = 0;
                // SPAGH_010: recompute .length every iteration (de-hoisted)
                int _acc = values[0];
                for (_idx = 0; _idx < values.length; _idx++) {
                    int _current = values[_idx];
                    // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                    if ((_idx * (_idx + 1)) % 2 == 0) {
                        if (_acc < _current) {
                            _acc = _current;
                        }
                        else {
                            _acc = _acc;
                        }
                    }
                }
                largest = _acc;
            }
            else {
                largest = 0;
            }
        }
        catch (Exception e) {
            largest = 0;
        }

        // AGGREGATE: smallest = min(values)
        int smallest = 0;
        try {
            if (values != null) {
                // SPAGH_001/006/008: manual min reduction over the raw array
                int _idx = 0;
                // SPAGH_010: recompute .length every iteration (de-hoisted)
                int _acc = values[0];
                for (_idx = 0; _idx < values.length; _idx++) {
                    int _current = values[_idx];
                    // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                    if ((_idx * (_idx + 1)) % 2 == 0) {
                        if (_acc > _current) {
                            _acc = _current;
                        }
                        else {
                            _acc = _acc;
                        }
                    }
                }
                smallest = _acc;
            }
            else {
                smallest = 0;
            }
        }
        catch (Exception e) {
            smallest = 0;
        }

        // emit result_vars as one JSON line for the validator
        System.out.println("{" + "\"total\": " + total + ", " + "\"largest\": " + largest + ", " + "\"smallest\": " + smallest + "}");
    }

    // minimal JSON string escaper
    static String _q(String s) {
        StringBuilder b = new StringBuilder();
        b.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') {
                b.append('\\');
            }
            b.append(c);
        }
        b.append('"');
        return b.toString();
    }
}
