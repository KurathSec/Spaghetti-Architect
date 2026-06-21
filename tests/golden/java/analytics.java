// Spaghetti Architect — generated class: Analytics
// Deliberately redundant, but syntactically correct and crash-free.
import java.util.HashMap;
import java.util.Map;
public class Analytics {
    public static void main(String[] args) {
        // --- run fixtures (inputs) ---
        int[] samples = {10, 20, 30, 40, 50};
        int needle = 30;
        Map<String, String> regions = new HashMap<>();
        regions.put("us", "use1");
        regions.put("eu", "euw1");
        String region_key = "eu";
        int threshold_input = 100;

        // MEMBERSHIP_CHECK: has_needle = needle in samples
        boolean has_needle = false;
        try {
            if (samples != null) {
                // SPAGH_001/006: index loop over the raw array
                int _idx = 0;
                // SPAGH_010: recompute .length every iteration (de-hoisted)
                boolean _match_flag = false;
                for (_idx = 0; _idx < samples.length; _idx++) {
                    int _current = samples[_idx];
                    // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                    if ((_idx * (_idx + 1)) % 2 == 0) {
                        if (needle == _current) {
                            _match_flag = true;
                        }
                        else {
                            _match_flag = _match_flag;
                        }
                    }
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
        catch (Exception e) {
            has_needle = false;
        }

        // AGGREGATE: sample_sum = sum(samples)
        int sample_sum = 0;
        try {
            if (samples != null) {
                // SPAGH_001/006/008: manual sum reduction over the raw array
                int _idx = 0;
                // SPAGH_010: recompute .length every iteration (de-hoisted)
                int _acc = 0;
                for (_idx = 0; _idx < samples.length; _idx++) {
                    int _current = samples[_idx];
                    // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                    if ((_idx * (_idx + 1)) % 2 == 0) {
                        _acc = _acc + _current;
                        _acc = _acc;
                    }
                }
                sample_sum = _acc;
            }
            else {
                sample_sum = 0;
            }
        }
        catch (Exception e) {
            sample_sum = 0;
        }

        // CONDITIONAL_SELECT: band = 'high' if threshold_input > 50 else 'low'
        String band = "low";
        try {
            // SPAGH_001/005: expand the ternary into an explicit if/else
            boolean _cond = 50 < threshold_input;
            if (_cond) {
                band = "high";
            }
            else {
                band = "low";
                band = band;
            }
        }
        catch (Exception e) {
            band = "low";
        }

        // KEY_VALUE_LOOKUP: zone = regions[region_key] or 'unknown'
        String zone = "unknown";
        try {
            if (regions != null) {
                // SPAGH_005: nested if chain enumerating every known key
                boolean _resolved = false;
                String _key = region_key;
                if (_key.equals("us")) {
                    zone = "use1";
                    _resolved = true;
                }
                else if (_key.equals("eu")) {
                    zone = "euw1";
                    _resolved = true;
                }
                else {
                    _resolved = false;
                }
                if (_resolved == false) {
                    zone = "unknown";
                }
            }
            else {
                zone = "unknown";
            }
        }
        catch (Exception e) {
            zone = "unknown";
        }

        // emit result_vars as one JSON line for the validator
        System.out.println("{" + "\"has_needle\": " + has_needle + ", " + "\"sample_sum\": " + sample_sum + ", " + "\"band\": " + _q(band) + ", " + "\"zone\": " + _q(zone) + "}");
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
