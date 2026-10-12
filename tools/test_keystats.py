"""Tests for tools/keystats.py. Uses made-up log lines and a temporary folder only.

Run: python3 tools/test_keystats.py
KEYSTATS_MODULE=path/to/copy.py runs the same tests against another copy.
"""
import gzip
import importlib.util
import os
import stat
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_path = os.environ.get("KEYSTATS_MODULE", os.path.join(HERE, "keystats.py"))
_spec = importlib.util.spec_from_file_location("keystats", _path)
ks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ks)

A, S, D, F, J, K = 10, 11, 12, 13, 16, 17
C_POS, V_POS = 22, 23
TAB_NAV, SPACE_NUMSYM = 30, 33
CODE = {"A": 0x04, "C": 0x06, "D": 0x07, "F": 0x09, "BSPC": 0x2A, "LCTRL": 0xE0, "RCTRL": 0xE4}


def ln(ms: int, text: str, level: str = "dbg") -> str:
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, msec = divmod(rest, 1000)
    return f"2026-10-02 09:00:00.000 [{h:02d}:{m:02d}:{s:02d}.{msec:03d},000] <{level}> zmk: {text}\n"


def endpoint(ms, ep):
    return [ln(ms, f"Endpoint changed: {ep}", "inf")]


def edge(ms, pos):
    """One physical key event, down or up, as the hold-tap listener logs it."""
    return [ln(ms, f"position_state_changed_listener: {pos} bubble (no undecided hold_tap active)")]


def output(ms, pos, code, page=0x07):
    return [ln(ms, f"on_keymap_binding_pressed: position {pos} keycode 0x{code:x}"),
            ln(ms, f"hid_listener_keycode_pressed: usage_page 0x{page:02x} keycode 0x{code:02x} "
                   "implicit_mods 0x00 explicit_mods 0x00")]


def kp(ms, pos, code, dur=80):
    return edge(ms, pos) + output(ms, pos, code) + edge(ms + dur, pos)


def ht_tap(ms, pos, code, dur=100):
    return (edge(ms, pos)
            + [ln(ms, f"on_hold_tap_binding_pressed: {pos} new undecided hold_tap"),
               ln(ms + dur, f"position_state_changed_listener: {pos} bubble undecided hold-tap keyrelease event"),
               ln(ms + dur, f"decide_hold_tap: {pos} decided tap (tap-preferred decision moment key-up)")]
            + output(ms + dur, pos, code)
            + [ln(ms + dur, f"on_hold_tap_binding_released: {pos} cleaning up hold-tap")])


def ht_hold(ms, pos, mod_code, mod_bits, end, inner=()):
    lines = (edge(ms, pos)
             + [ln(ms, f"on_hold_tap_binding_pressed: {pos} new undecided hold_tap"),
                ln(ms + 200, f"decide_hold_tap: {pos} decided hold-timer (tap-preferred decision moment timer)")]
             + output(ms + 200, pos, mod_code)
             + [ln(ms + 200, f"zmk_hid_register_mod: Modifiers set to 0x{mod_bits:02x}")])
    lines += list(inner)
    lines += edge(end, pos) + [ln(end, "zmk_hid_unregister_mod: Modifiers set to 0x00"),
                               ln(end, f"on_hold_tap_binding_released: {pos} cleaning up hold-tap")]
    return lines


def run(lines):
    s = ks.Summarizer()
    for line in lines:
        s.feed(line)
    return s.result("20261002", "test")


class HoldTap(unittest.TestCase):
    def test_tap_is_counted_with_its_timing(self):
        r = run(endpoint(0, "BLE:2") + ht_tap(1000, D, CODE["D"], dur=104))
        self.assertEqual(r["keys"]["BASE"][str(D)], {"D": 1})
        self.assertEqual(r["hold_tap"][str(D)]["taps"], 1)
        self.assertEqual(r["hold_tap"][str(D)]["tap_ms_100"], 1)
        self.assertEqual(r["outputs"], {"BLE:2": 1})

    def test_same_hand_and_other_hand_copy(self):
        lines = endpoint(0, "BLE:2")
        lines += ht_hold(1000, D, CODE["LCTRL"], 0x01, 1900,
                         kp(1300, C_POS, CODE["C"]) + kp(1600, V_POS, 0x19))
        lines += ht_hold(3000, K, CODE["RCTRL"], 0x10, 3600, kp(3300, C_POS, CODE["C"]))
        r = run(lines)
        self.assertEqual(r["shortcuts"], {"BLE:2": {"LCtrl+C": 1, "LCtrl+V": 1, "RCtrl+C": 1}})
        self.assertEqual(r["modifier_hand"], {"same": 2, "other": 1})
        self.assertEqual(r["hold_tap"][str(D)]["holds"], 1)
        self.assertNotIn("holds_alone", r["hold_tap"][str(D)])
        self.assertEqual(ks.metrics(r)["copy with the right hand %"], 50.0)

    def test_no_copies_is_no_data_not_zero(self):
        r = run(endpoint(0, "BLE:2") + ht_tap(1000, D, CODE["D"]))
        self.assertIsNone(ks.metrics(r)["copy with the right hand %"])
        self.assertEqual(ks.shown(None), "no data")

    def test_hold_with_no_other_key(self):
        lines = ht_hold(1000, F, 0xE3, 0x08, 1300) + ht_hold(5000, F, 0xE3, 0x08, 5700)
        c = run(lines)["hold_tap"][str(F)]
        self.assertEqual((c["holds"], c["holds_alone"], c["holds_alone_short"]), (2, 2, 1))

    def test_interrupted_tap_then_backspace(self):
        lines = (edge(1000, S)
                 + [ln(1000, f"on_hold_tap_binding_pressed: {S} new undecided hold_tap"),
                    ln(1040, f"position_state_changed_listener: {S} capturing {D + 1} down event"),
                    ln(1090, f"position_state_changed_listener: {S} bubble undecided hold-tap keyrelease event"),
                    ln(1090, f"decide_hold_tap: {S} decided tap (tap-preferred decision moment key-up)")]
                 + output(1090, S, 0x16)
                 + [ln(1090, f"release_captured_events: Releasing key position event for position {D + 1} pressed"),
                    ln(1090, f"position_state_changed_listener: {D + 1} bubble (no undecided hold_tap active)")]
                 + output(1090, D + 1, CODE["F"])
                 + [ln(1090, f"on_hold_tap_binding_released: {S} cleaning up hold-tap")]
                 + edge(1150, D + 1)
                 + kp(1600, 32, CODE["BSPC"]))
        r = run(lines)
        self.assertEqual(r["hold_tap"][str(S)]["interrupted_taps"], 1)
        self.assertEqual(r["hold_tap"][str(S)]["interrupted_taps_then_bspc"], 1)
        # The captured press and its re-raise are one physical press.
        self.assertEqual(r["presses"][str(D + 1)], 1)


class Pairs(unittest.TestCase):
    def test_close_neighbors_counted_far_or_released_not(self):
        lines = (edge(1000, TAB_NAV) + edge(1030, SPACE_NUMSYM) + edge(1100, TAB_NAV) + edge(1120, SPACE_NUMSYM)
                 + edge(3000, TAB_NAV) + edge(3020, TAB_NAV) + edge(3040, SPACE_NUMSYM) + edge(3100, SPACE_NUMSYM)
                 + edge(5000, D) + edge(5120, F) + edge(5200, D) + edge(5210, F)
                 + edge(7000, A) + edge(7010, F) + edge(7050, A) + edge(7060, F))
        r = run(lines)
        self.assertEqual(r["pairs"], {f"{TAB_NAV}+{SPACE_NUMSYM}": {"30": 1}})
        self.assertEqual(r["presses"][str(TAB_NAV)], 2)


class Layers(unittest.TestCase):
    def test_numsym_layer_output_and_system_use(self):
        lines = ([ln(900, "set_layer_state: layer_changed: layer 1 state 1 locked 0")]
                 + kp(1000, 1, 0x1F)
                 + [ln(1100, "set_layer_state: layer_changed: layer 1 state 0 locked 0"),
                    ln(2000, "conditional_layer_activate: layer 3"),
                    ln(2100, "zmk_ble_prof_select: profile 2"),
                    ln(2200, "conditional_layer_deactivate: layer 3"),
                    ln(4000, "conditional_layer_activate: layer 3"),
                    ln(4200, "conditional_layer_deactivate: layer 3")])
        r = run(lines)
        self.assertEqual(r["keys"], {"NUMSYM": {"1": {"2": 1}}})
        self.assertEqual(r["profiles"], {"2": 1})
        self.assertEqual(r["system_layer"], {"activations": 2, "used": 1})

    def test_reboot_drops_open_key_state(self):
        lines = (edge(500_000, D) + [ln(500_000, f"on_hold_tap_binding_pressed: {D} new undecided hold_tap")]
                 + edge(500, F) + kp(2000, C_POS, CODE["C"]) + edge(2500, F))
        r = run(lines)
        self.assertEqual(r["hold_tap"], {})
        self.assertEqual(r["keys"]["BASE"][str(C_POS)], {"C": 1})
        self.assertNotIn(f"{D}+{F}", r["pairs"])


class Backspace(unittest.TestCase):
    def test_runs_and_letters(self):
        lines = (kp(1000, 20, CODE["A"]) + kp(1500, 32, CODE["BSPC"]) + kp(1700, 32, CODE["BSPC"])
                 + kp(5000, 21, CODE["C"]) + kp(9000, 32, CODE["BSPC"]))
        b = run(lines)["backspace"]
        self.assertEqual(b, {"total": 3, "runs": {"2": 1, "1": 1}, "letters": 2, "letters_then_bspc": 1})


class CountsOnly(unittest.TestCase):
    """A summary must not be readable as typing: no lists, no free text."""

    def assert_counts_only(self, value, path="summary"):
        if isinstance(value, dict):
            for k, v in value.items():
                self.assertIsInstance(k, str, path)
                if path == "summary" and k in ("day", "source"):
                    continue
                self.assert_counts_only(v, f"{path}.{k}")
        else:
            self.assertIs(type(value), int, path)

    def test_every_summary_value_is_a_count(self):
        lines = (endpoint(0, "BLE:0") + ht_tap(1000, D, CODE["D"])
                 + ht_hold(2000, F, 0xE3, 0x08, 2600, kp(2300, C_POS, CODE["C"]))
                 + edge(3000, TAB_NAV) + edge(3020, SPACE_NUMSYM) + edge(3100, TAB_NAV) + edge(3110, SPACE_NUMSYM)
                 + kp(4000, 32, CODE["BSPC"]))
        self.assert_counts_only(run(lines))


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = self.tmp.name
        self.stats = os.path.join(self.logs, "stats")
        body = "".join(endpoint(0, "BLE:2") + ht_tap(1000, D, CODE["D"]))
        with gzip.open(os.path.join(self.logs, "left-20261001.log.gz"), "wt") as f:
            f.write(body)
        for name in ("left-20261002.log", "left-20261003.log"):
            with open(os.path.join(self.logs, name), "w") as f:
                f.write(body)
        with open(os.path.join(self.logs, "left-20260930.log.gz"), "wb") as f:
            f.write(b"\x1f\x8b not really gzip")

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_writes_finished_days_only(self):
        notes, failures = ks.update(self.logs, self.stats, "20261003")
        self.assertEqual(failures, 1)
        self.assertEqual(sorted(os.listdir(self.stats)), ["keys-20261001.json", "keys-20261002.json"])
        mode = stat.S_IMODE(os.stat(os.path.join(self.stats, "keys-20261001.json")).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertTrue(any(n.startswith("failed 20260930") for n in notes))

    def test_existing_summary_kept_unless_forced(self):
        ks.update(self.logs, self.stats, "20261003")
        out = os.path.join(self.stats, "keys-20261002.json")
        with open(out, "w") as f:
            f.write('{"marker": 1}')
        notes, _ = ks.update(self.logs, self.stats, "20261003")
        self.assertIn("kept 20261002", notes)
        with open(out) as f:
            self.assertEqual(f.read(), '{"marker": 1}')
        ks.update(self.logs, self.stats, "20261003", force=True)
        summaries, _ = ks.load_summaries(self.stats)
        self.assertEqual([s["day"] for s in summaries], ["20261001", "20261002"])

    def test_report_compares_periods(self):
        ks.update(self.logs, self.stats, "20261003")
        summaries, _ = ks.load_summaries(self.stats)
        text = ks.report(summaries, 1)
        self.assertIn("Typing summary: 1 day(s), 20261002 to 20261002, 1 keys", text)
        self.assertIn("(before: ", text)
        self.assertIn("other computer 100%", text)


if __name__ == "__main__":
    unittest.main()
