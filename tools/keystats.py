#!/usr/bin/env python3
"""Daily typing summaries from the Cradio left-half debug log.

Reads the day logs that ~/Library/Logs/zmk-hang/record.py writes and saves one
summary per finished day. A summary holds counts only. It never holds key
order, words or text, so a summary cannot be read back as typing.

  keystats.py update [--force]   summarize finished days that have no summary
  keystats.py report [--days N]  show the last N summarized days, and the
                                 change from the N days before when they exist

Summaries go to ~/Library/Logs/zmk-hang/stats/keys-YYYYMMDD.json. The log only
exists while the left half is cabled to the Mac and runs the logging firmware,
so time on battery is not counted.
"""
import argparse
import bisect
import collections
import glob
import gzip
import json
import os
import re
import sys
import time

LOG_DIR = os.path.expanduser("~/Library/Logs/zmk-hang")
STATS_DIR = os.path.join(LOG_DIR, "stats")
DAY_LOG = re.compile(r"left-(\d{8})\.log(\.gz)?$")
SUMMARY_NAME = "keys-{day}.json"
SCHEMA = 1

# Base-layer label of each of the 34 key positions.
POSITIONS = ("Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P",
             "A", "S", "D", "F", "G", "H", "J", "K", "L", "'",
             "Z", "X", "C", "V", "B", "N", "M", ",", ".", "/",
             "TAB", "ENTER", "BSPC", "SPACE")
LAYERS = ("BASE", "NUMSYM", "NAV", "SYSTEM")
# Which computer each output reaches. Profile 0 and the cable go to the Mac.
COMPUTERS = {"BLE:0": "Mac", "USB": "Mac", "BLE:2": "other computer"}
# The modifier each computer uses for copy and paste.
COPY_MODIFIER = {"Mac": "Cmd", "other computer": "Ctrl"}
SHORT_HOLD_S = 0.4        # a hold this short with no other key was likely meant as a tap
BSPC_WINDOW_S = 1.5       # a backspace this soon after a key counts as correcting it
SLOW_TAP_MS = 150         # taps this slow are close to the 200 ms hold limit
TAP_BUCKET_MS = 10
TAP_BUCKET_MAX_MS = 300
PAIR_WINDOW_MS = 100      # combo timeouts above this are not useful
PAIR_BUCKET_MS = 10
COMBO_RISK_MS = 50        # the report lists pairs pressed this close together

HID = {0x28: "ENTER", 0x29: "ESC", 0x2A: "BSPC", 0x2B: "TAB", 0x2C: "SPACE", 0x2D: "-", 0x2E: "=",
       0x2F: "[", 0x30: "]", 0x31: "\\", 0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".",
       0x38: "/", 0x4C: "DEL", 0x4F: "RIGHT", 0x50: "LEFT", 0x51: "DOWN", 0x52: "UP", 0x7F: "MUTE"}
HID.update({0x04 + i: chr(ord("A") + i) for i in range(26)})
HID.update({0x1E + i: str((i + 1) % 10) for i in range(10)})
HID.update({0x3A + i: f"F{i + 1}" for i in range(12)})
CONSUMER = {0xCD: "PLAY", 0xB5: "NEXT", 0xB6: "PREV", 0xE9: "VOL+", 0xEA: "VOL-"}
MODIFIER_CODES = range(0xE0, 0xE8)
BSPC_CODE = 0x2A
LETTER_CODES = range(0x04, 0x1E)
MODBITS = ((0x01, "LCtrl"), (0x02, "LShift"), (0x04, "LAlt"), (0x08, "LCmd"),
           (0x10, "RCtrl"), (0x20, "RShift"), (0x40, "RAlt"), (0x80, "RCmd"))
LEFT_MODS = 0x0F

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LINE = re.compile(r"\[(\d+):(\d+):(\d+)\.(\d+),(\d+)\] <\w+> \w+: (?:(\w+): )?(.*)")
ENDPOINT = re.compile(r"Endpoint changed: (\S+)")
LAYER_STATE = re.compile(r"layer (\d+) state (\d+)")
MODS_SET = re.compile(r"Modifiers set to (0x[0-9A-Fa-f]+)")
DECIDE = re.compile(r"(\d+) decided (\S+) \(.*decision moment (\S+)\)")
CAPTURE = re.compile(r"(\d+) capturing (\d+) (down|up) event")
BUBBLING = re.compile(r"(\d+) bubbling (\d+) (?:up|down) event")
RELEASE_CAPTURED = re.compile(r"Releasing key position event for position (\d+)")
BINDING = re.compile(r"position (\d+) keycode")
HID_PRESS = re.compile(r"usage_page (0x[0-9A-Fa-f]+) keycode (0x[0-9A-Fa-f]+) implicit_mods "
                       r"0x[0-9A-Fa-f]+ explicit_mods (0x[0-9A-Fa-f]+)")
PROFILE = re.compile(r"profile (\d+)")


def _neighbor_pairs() -> frozenset:
    pairs = {(r * 10 + c, r * 10 + c + 1) for r in range(3) for c in range(9)}
    pairs |= {(30, 31), (31, 32), (32, 33), (30, 33)}
    return frozenset(pairs)


PAIRS = _neighbor_pairs()


def hand(pos: int) -> str:
    if pos >= 30:
        return "L" if pos <= 31 else "R"
    return "L" if pos % 10 < 5 else "R"


def mods_text(bits: int) -> str:
    return "+".join(name for bit, name in MODBITS if bits & bit)


def key_name(page: int, code: int) -> str:
    if page == 0x0C:
        return CONSUMER.get(code, f"C{code:#x}")
    return HID.get(code, f"K{code:#x}")


def label(pos: str) -> str:
    i = int(pos)
    return POSITIONS[i] if 0 <= i < len(POSITIONS) else pos


Output = collections.namedtuple("Output", "t pos layer page code mods from_hold_tap endpoint")


class Summarizer:
    """Turns one day of log lines into counts. Feed lines in order, then call result()."""

    def __init__(self):
        self.offset = 0.0
        self.last_raw = -1.0
        self.endpoint = "unknown"
        self.layers = {0}
        self.mods = 0
        self.hold_taps = {}
        self.finished = []
        self.outputs = []
        self.pending = None
        self.down = {}
        self.skip = collections.Counter()
        self.presses = collections.Counter()
        self.pairs = collections.defaultdict(collections.Counter)
        self.profiles = collections.Counter()
        self.system = collections.Counter()
        self.system_on = False
        self.system_used = False

    def _reboot(self) -> None:
        # Key state from before a reboot is gone. Counted totals stay.
        self.layers = {0}
        self.mods = 0
        self.hold_taps.clear()
        self.pending = None
        self.down.clear()
        self.skip.clear()
        self.system_on = False

    def feed(self, raw: str) -> None:
        m = LINE.search(ANSI.sub("", raw))
        if not m:
            return
        h, mi, s, ms, us, fn, msg = m.groups()
        t_raw = int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1e3 + int(us) / 1e6
        if t_raw < self.last_raw - 1.0:
            self._reboot()
            self.offset += self.last_raw + 3600.0
        self.last_raw = t_raw
        t = t_raw + self.offset
        em = ENDPOINT.search(msg)
        if em:
            self.endpoint = em.group(1)
            return
        handler = getattr(self, f"_on_{fn}", None) if fn else None
        if handler:
            handler(msg, t)

    def _mark_during(self, pos: int) -> None:
        for r in self.hold_taps.values():
            if r["kind"] == "hold" and r["pos"] != pos:
                r["during"] += 1

    def _physical(self, pos: int, t: float) -> None:
        if pos in self.down:
            del self.down[pos]
            return
        for other, t0 in self.down.items():
            gap_ms = (t - t0) * 1000
            pair = (min(pos, other), max(pos, other))
            if gap_ms <= PAIR_WINDOW_MS and pair in PAIRS:
                bucket = int(gap_ms // PAIR_BUCKET_MS) * PAIR_BUCKET_MS
                self.pairs[f"{pair[0]}+{pair[1]}"][str(bucket)] += 1
        self.down[pos] = t
        self.presses[str(pos)] += 1

    def _on_set_layer_state(self, msg, _t):
        m = LAYER_STATE.search(msg)
        if m:
            (self.layers.add if m.group(2) == "1" else self.layers.discard)(int(m.group(1)))
            self.layers.add(0)

    def _on_conditional_layer_activate(self, _msg, _t):
        self.system_on, self.system_used = True, False
        self.system["activations"] += 1

    def _on_conditional_layer_deactivate(self, _msg, _t):
        if self.system_on:
            self.system["used"] += self.system_used
        self.system_on = False

    def _on_zmk_ble_prof_select(self, msg, _t):
        m = PROFILE.search(msg)
        if m:
            self.profiles[m.group(1)] += 1
        self.system_used = self.system_used or self.system_on
        self._mark_during(-1)

    def _on_zmk_hid_register_mod(self, msg, _t):
        m = MODS_SET.search(msg)
        if m:
            self.mods = int(m.group(1), 16)

    _on_zmk_hid_unregister_mod = _on_zmk_hid_register_mod

    def _on_on_hold_tap_binding_pressed(self, msg, t):
        pos = int(msg.split()[0])
        self._mark_during(pos)
        self.hold_taps[pos] = {"pos": pos, "t0": t, "kind": None, "moment": None, "td": None,
                               "captured": 0, "during": 0, "t1": None}

    def _on_decide_hold_tap(self, msg, t):
        m = DECIDE.match(msg)
        if m and int(m.group(1)) in self.hold_taps:
            r = self.hold_taps[int(m.group(1))]
            r["kind"] = "hold" if m.group(2).startswith("hold") else "tap"
            r["moment"], r["td"] = m.group(3), t

    def _on_on_hold_tap_binding_released(self, msg, t):
        r = self.hold_taps.pop(int(msg.split()[0]), None)
        if r and r["kind"]:
            r["t1"] = t
            self.finished.append(r)

    def _on_position_state_changed_listener(self, msg, t):
        # One line per physical key event. A captured event is logged as
        # "capturing" and its later re-raise is skipped, so each counts once.
        m = CAPTURE.match(msg)
        if m:
            owner, pos = int(m.group(1)), int(m.group(2))
            if owner in self.hold_taps and m.group(3) == "down":
                self.hold_taps[owner]["captured"] += 1
            self._physical(pos, t)
            return
        m = BUBBLING.match(msg)
        pos = int(m.group(2)) if m else int(msg.split()[0])
        if self.skip[pos]:
            self.skip[pos] -= 1
            return
        self._physical(pos, t)

    def _on_release_captured_events(self, msg, _t):
        m = RELEASE_CAPTURED.search(msg)
        if m:
            self.skip[int(m.group(1))] += 1

    def _on_on_keymap_binding_pressed(self, msg, t):
        m = BINDING.match(msg)
        if m:
            pos = int(m.group(1))
            r = self.hold_taps.get(pos)
            self.pending = (t, pos, max(self.layers), bool(r and r["kind"]))

    def _on_hid_listener_keycode_pressed(self, msg, _t):
        m = HID_PRESS.search(msg)
        if not (m and self.pending):
            return
        t, pos, layer, from_hold_tap = self.pending
        self.pending = None
        page, code = int(m.group(1), 16), int(m.group(2), 16)
        self._mark_during(pos)
        if page == 0x07 and code in MODIFIER_CODES:
            return
        self.outputs.append(Output(t, pos, layer, page, code, self.mods | int(m.group(3), 16),
                                   from_hold_tap, self.endpoint))
        self.system_used = self.system_used or self.system_on

    def result(self, day: str, source: str) -> dict:
        bspc = sorted(o.t for o in self.outputs if o.page == 0x07 and o.code == BSPC_CODE)

        def bspc_follows(t: float) -> bool:
            i = bisect.bisect_right(bspc, t)
            return i < len(bspc) and bspc[i] - t <= BSPC_WINDOW_S

        keys = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
        shortcuts = collections.defaultdict(collections.Counter)
        modifier_hand = collections.Counter()
        letters = letters_then_bspc = 0
        runs = collections.Counter()
        run = 0
        for o in self.outputs:
            name = key_name(o.page, o.code)
            layer = LAYERS[o.layer] if o.layer < len(LAYERS) else str(o.layer)
            keys[layer][str(o.pos)][name] += 1
            if o.mods:
                shortcuts[o.endpoint][f"{mods_text(o.mods)}+{name}"] += 1
                if o.page == 0x07:
                    mod_hand = "L" if o.mods & LEFT_MODS else "R"
                    modifier_hand["same" if mod_hand == hand(o.pos) else "other"] += 1
            if o.page == 0x07 and o.code == BSPC_CODE:
                run += 1
                continue
            if run:
                runs[str(min(run, 6))] += 1
                run = 0
            if o.page == 0x07 and o.code in LETTER_CODES and o.layer == 0 and not o.from_hold_tap and not o.mods:
                letters += 1
                letters_then_bspc += bspc_follows(o.t)
        if run:
            runs[str(min(run, 6))] += 1

        hold_tap = {}
        for r in self.finished:
            c = hold_tap.setdefault(str(r["pos"]), collections.Counter())
            if r["kind"] == "tap":
                c["taps"] += 1
                if r["moment"] == "quick-tap":
                    c["quick_taps"] += 1
                if r["moment"] == "key-up":
                    ms = min((r["td"] - r["t0"]) * 1000, TAP_BUCKET_MAX_MS)
                    c[f"tap_ms_{int(ms // TAP_BUCKET_MS) * TAP_BUCKET_MS}"] += 1
                if r["captured"]:
                    c["interrupted_taps"] += 1
                    c["interrupted_taps_then_bspc"] += bspc_follows(r["td"])
            else:
                c["holds"] += 1
                if not r["during"]:
                    c["holds_alone"] += 1
                    c["holds_alone_short"] += (r["t1"] - r["t0"]) < SHORT_HOLD_S

        return {
            "schema": SCHEMA,
            "day": day,
            "source": source,
            "key_outputs": len(self.outputs),
            "outputs": dict(collections.Counter(o.endpoint for o in self.outputs)),
            "presses": dict(self.presses),
            "keys": {lay: {pos: dict(c) for pos, c in by_pos.items()} for lay, by_pos in keys.items()},
            "hold_tap": {pos: dict(c) for pos, c in hold_tap.items()},
            "shortcuts": {ep: dict(c) for ep, c in shortcuts.items()},
            "modifier_hand": dict(modifier_hand),
            "profiles": dict(self.profiles),
            "system_layer": dict(self.system),
            "pairs": {pair: dict(c) for pair, c in self.pairs.items()},
            "backspace": {"total": len(bspc), "runs": dict(runs), "letters": letters,
                          "letters_then_bspc": letters_then_bspc},
        }


def read_lines(path: str):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        yield from f


def summarize_file(path: str, day: str) -> dict:
    s = Summarizer()
    for line in read_lines(path):
        s.feed(line)
    return s.result(day, os.path.basename(path))


def write_json_atomic(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def update(log_dir: str, stats_dir: str, today: str, force: bool = False) -> tuple:
    """Summarize finished days. Returns (notes, failures)."""
    days = {}
    for path in sorted(glob.glob(os.path.join(log_dir, "left-*.log*"))):
        m = DAY_LOG.search(os.path.basename(path))
        # While a day file is being compressed both forms exist with the same content.
        if m and m.group(1) < today and (m.group(1) not in days or m.group(2)):
            days[m.group(1)] = path
    os.makedirs(stats_dir, mode=0o700, exist_ok=True)
    notes, failures = [], 0
    for day, path in sorted(days.items()):
        out = os.path.join(stats_dir, SUMMARY_NAME.format(day=day))
        if os.path.exists(out) and not force:
            notes.append(f"kept {day}")
            continue
        try:
            summary = summarize_file(path, day)
            write_json_atomic(out, summary)
        except (OSError, EOFError) as e:
            failures += 1
            notes.append(f"failed {day}: {e}")
            continue
        notes.append(f"wrote {day}: {summary['key_outputs']} keys")
    return notes, failures


def load_summaries(stats_dir: str) -> tuple:
    summaries, notes = [], []
    for path in sorted(glob.glob(os.path.join(stats_dir, SUMMARY_NAME.format(day="*")))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            notes.append(f"unreadable {os.path.basename(path)}: {e}")
            continue
        if data.get("schema") != SCHEMA:
            notes.append(f"skipped {os.path.basename(path)}: schema {data.get('schema')}")
            continue
        summaries.append(data)
    return summaries, notes


def merge(summaries: list) -> dict:
    def add(into: dict, src: dict) -> None:
        for k, v in src.items():
            if isinstance(v, dict):
                add(into.setdefault(k, {}), v)
            elif isinstance(v, int) and k != "schema":
                into[k] = into.get(k, 0) + v
    total = {}
    for s in summaries:
        add(total, s)
    return total


def rate(count: int, base: int, per: int):
    """count per `per` of base, or None when there is nothing to divide by."""
    return per * count / base if base else None


def metrics(total: dict) -> dict:
    """The few numbers worth comparing between periods. None means no data."""
    ht = total.get("hold_tap", {})
    home = [p for p in ("10", "11", "12", "13", "16", "17", "18", "19") if p in ht]
    letters = total.get("backspace", {}).get("letters", 0)
    hands = total.get("modifier_hand", {})
    copy_right = copy_all = 0
    for ep, sc in total.get("shortcuts", {}).items():
        mod = COPY_MODIFIER.get(COMPUTERS.get(ep, ""))
        if mod:
            right, left = sc.get(f"R{mod}+C", 0), sc.get(f"L{mod}+C", 0)
            copy_right, copy_all = copy_right + right, copy_all + right + left
    slow = sum(v for p in home for k, v in ht[p].items() if k.startswith("tap_ms_") and int(k[7:]) >= SLOW_TAP_MS)
    timed = sum(v for p in home for k, v in ht[p].items() if k.startswith("tap_ms_"))
    return {
        "accidental holds per 1000 letters": rate(sum(ht[p].get("holds_alone_short", 0) for p in home), letters, 1000),
        "copy with the right hand %": rate(copy_right, copy_all, 100),
        "shortcuts on one hand %": rate(hands.get("same", 0), sum(hands.values()), 100),
        f"home-row taps over {SLOW_TAP_MS} ms %": rate(slow, timed, 100),
        "backspace per 100 letters": rate(total.get("backspace", {}).get("total", 0), letters, 100),
    }


def shown(value) -> str:
    return "no data" if value is None else f"{value:.1f}"


def report(summaries: list, days: int) -> str:
    recent = summaries[-days:]
    earlier = summaries[-2 * days:-days] if len(summaries) >= 2 * days else []
    total = merge(recent)
    n = total.get("key_outputs", 0)
    out = [f"Typing summary: {len(recent)} day(s), {recent[0]['day']} to {recent[-1]['day']}, {n:,} keys"]
    by_computer = collections.Counter()
    for ep, v in total.get("outputs", {}).items():
        by_computer[COMPUTERS.get(ep, ep)] += v
    out.append("Computers: " + ", ".join(f"{k} {100 * v / max(1, n):.0f}%" for k, v in by_computer.most_common()))
    layer_counts = {lay: sum(sum(c.values()) for c in by_pos.values()) for lay, by_pos in total.get("keys", {}).items()}
    out.append("Layers: " + ", ".join(f"{k} {100 * v / max(1, n):.1f}%" for k, v in
                                      sorted(layer_counts.items(), key=lambda kv: -kv[1])))
    for lay in ("NUMSYM", "NAV"):
        used = collections.Counter()
        for by_name in total.get("keys", {}).get(lay, {}).values():
            used.update(by_name)
        if used:
            out.append(f"{lay} most used: " + ", ".join(f"{k} {v}" for k, v in used.most_common(8)))
    out.append("Hold-tap keys      taps  holds  accidental holds")
    for pos, c in sorted(total.get("hold_tap", {}).items(), key=lambda kv: int(kv[0])):
        out.append(f"  {label(pos):6s}      {c.get('taps', 0):6d} {c.get('holds', 0):6d} {c.get('holds_alone_short', 0):8d}")
    for ep, sc in sorted(total.get("shortcuts", {}).items()):
        computer = COMPUTERS.get(ep, ep)
        mod = COPY_MODIFIER.get(computer)
        if mod:
            parts = [f"{mod}+{k} left {sc.get(f'L{mod}+{k}', 0)} right {sc.get(f'R{mod}+{k}', 0)}" for k in ("C", "V")]
            out.append(f"Copy and paste on the {computer}: " + ", ".join(parts))
    risky = []
    for pair, buckets in total.get("pairs", {}).items():
        close = sum(v for b, v in buckets.items() if int(b) < COMBO_RISK_MS)
        if close:
            a, b = pair.split("+")
            risky.append((close, f"{label(a)}+{label(b)}"))
    if risky:
        out.append(f"Neighbor keys pressed within {COMBO_RISK_MS} ms (unsafe for a combo): "
                   + ", ".join(f"{name} {v}" for v, name in sorted(risky, reverse=True)[:6]))
    out.append(f"Profile switches: {sum(total.get('profiles', {}).values())} "
               + str(dict(sorted(total.get("profiles", {}).items()))))
    now = metrics(total)
    before = metrics(merge(earlier)) if earlier else None
    for k, v in now.items():
        tail = f"   (before: {shown(before[k])})" if before else ""
        out.append(f"{k}: {shown(v)}{tail}")
    if not earlier:
        out.append(f"No comparison yet: needs {2 * days} summarized days, have {len(summaries)}.")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    up = sub.add_parser("update", help="summarize finished days")
    up.add_argument("--force", action="store_true", help="rewrite summaries that already exist")
    rp = sub.add_parser("report", help="show the summarized days")
    rp.add_argument("--days", type=int, default=7)
    args = ap.parse_args(argv)
    if args.command == "update":
        notes, failures = update(LOG_DIR, STATS_DIR, time.strftime("%Y%m%d"), args.force)
        print("\n".join(notes) if notes else "no finished day logs found")
        return 1 if failures else 0
    summaries, notes = load_summaries(STATS_DIR)
    for note in notes:
        print(note, file=sys.stderr)
    if not summaries:
        print("no summaries yet: run keystats.py update first")
        return 1
    print(report(summaries, max(1, args.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
