// Spaghetti Architect — generated class: Demo
// Deliberately redundant, but syntactically correct and crash-free.
import java.util.HashMap;
import java.util.Map;
public class Demo {
    public static void main(String[] args) {
        // --- run fixtures (inputs) ---
        int[] data_list = {10, 20, 30, 40};
        int search_val = 30;
        Map<String, String> config_db = new HashMap<>();
        config_db.put("dev", "localhost");
        config_db.put("prod", "10.0.0.1");
        String input_key = "dev";

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

        // KEY_VALUE_LOOKUP: out_val = config_db[input_key] or '127.0.0.1'
        String out_val = "127.0.0.1";
        try {
            if (config_db != null) {
                // SPAGH_005: nested if chain enumerating every known key
                boolean _resolved = false;
                String _key = input_key;
                if (_key.equals("dev")) {
                    out_val = "localhost";
                    _resolved = true;
                }
                else if (_key.equals("prod")) {
                    out_val = "10.0.0.1";
                    _resolved = true;
                }
                else {
                    _resolved = false;
                }
                if (_resolved == false) {
                    out_val = "127.0.0.1";
                }
            }
            else {
                out_val = "127.0.0.1";
            }
        }
        catch (Exception e) {
            out_val = "127.0.0.1";
        }

        // emit result_vars as one JSON line for the validator
        System.out.println("{" + "\"is_found\": " + is_found + ", " + "\"out_val\": " + _q(out_val) + "}");
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
