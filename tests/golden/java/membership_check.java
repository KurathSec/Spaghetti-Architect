// Spaghetti Architect — generated class: Membership
// Deliberately redundant, but syntactically correct and crash-free.
public class Membership {
    public static void main(String[] args) {
        // --- run fixtures (inputs) ---
        int[] data_list = {10, 20, 30, 40};
        int search_val = 30;

        // MEMBERSHIP_CHECK: is_found = search_val in data_list
        boolean is_found = false;
        try {
            if (data_list != null) {
                // SPAGH_001/006: index loop over the raw array
                int _idx = 0;
                // SPAGH_010: recompute .length every iteration (de-hoisted)
                boolean _match_flag = false;
                for (_idx = 0; _idx < data_list.length; _idx++) {
                    int _current = data_list[_idx];
                    // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                    if ((_idx * (_idx + 1)) % 2 == 0) {
                        if (search_val == _current) {
                            _match_flag = true;
                        }
                        else {
                            _match_flag = _match_flag;
                        }
                    }
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
        catch (Exception e) {
            is_found = false;
        }

        // emit result_vars as one JSON line for the validator
        System.out.println("{" + "\"is_found\": " + is_found + "}");
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
