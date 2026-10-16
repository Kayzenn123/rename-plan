#!/usr/bin/env python3
"""rename-plan: plan (and optionally apply) bulk file renames from the CLI.

Dry-run by default: prints a table of OLD -> NEW renames. Use --apply to
actually rename. Zero dependencies, stdlib only.
"""
import argparse
import json
import os
import re
import sys
import unicodedata

VERSION = "1.0.0"
PROG = "rename-plan"


# ---------------------------------------------------------------- core (pure)
def split_stem_ext(basename):
    stem, dot, ext = basename.rpartition(".")
    if not stem or basename.startswith(".") and "." not in basename[1:]:
        return basename, ""
    return stem, (dot + ext if dot else "")


def slugify(text):
    norm = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in norm if not unicodedata.combining(c))
    ascii_only = ascii_only.encode("ascii", "ignore").decode("ascii")
    out = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower())
    out = re.sub(r"-{2,}", "-", out).strip("-")
    return out or "file"


def apply_rules(basename, rules, counter=None):
    """Apply the rule list to a basename. Returns the new basename."""
    stem, ext = split_stem_ext(basename)
    numbered = False
    for kind, arg in rules:
        if kind == "replace":
            old, new = arg
            stem = stem.replace(old, new)
        elif kind == "regex":
            pattern, repl = arg
            stem = pattern.sub(repl, stem)
        elif kind == "strip":
            stem = stem.strip(arg)
        elif kind == "lower":
            stem = stem.lower()
        elif kind == "upper":
            stem = stem.upper()
        elif kind == "slug":
            stem = slugify(stem)
        elif kind in ("prefix", "suffix"):
            text = arg
            if "{n}" in text:
                if counter is None:
                    raise ValueError("--number FORMAT is required when using {n}")
                text = text.replace("{n}", counter)
                numbered = True
            stem = text + stem if kind == "prefix" else stem + text
    if counter is not None and not numbered:
        stem = stem + counter
    new_name = (stem or "file") + ext
    return new_name


def format_counter(fmt, i):
    try:
        out = fmt % i
    except (TypeError, ValueError) as e:
        raise ValueError(f"bad --number format {fmt!r}: {e}")
    if out == fmt:
        raise ValueError(f"--number format {fmt!r} has no conversion (e.g. %03d)")
    return out


def build_plan(entries, rules, number_fmt=None):
    """entries: list of (display, abspath). Returns list of plan dicts."""
    ordered = sorted(entries, key=lambda e: e[1])
    plan = []
    for idx, (display, abspath) in enumerate(ordered, start=1):
        counter = format_counter(number_fmt, idx) if number_fmt else None
        try:
            new_base = apply_rules(os.path.basename(abspath), rules, counter)
        except ValueError as e:
            plan.append({"old": display, "new": display, "status": "error",
                         "reason": str(e), "path": abspath})
            continue
        new_abs = os.path.join(os.path.dirname(abspath), new_base)
        if new_abs == abspath:
            plan.append({"old": display, "new": display, "status": "unchanged",
                         "path": abspath})
        else:
            plan.append({"old": display, "new": os.path.join(
                os.path.dirname(display), new_base) if os.path.dirname(display)
                else new_base, "status": "rename", "path": abspath,
                "target": new_abs})
    # collision detection
    seen = {}
    sources = {p["path"] for p in plan if p["status"] == "rename"}
    for p in plan:
        if p["status"] != "rename":
            continue
        tgt = p["target"]
        if tgt in seen:
            p["status"] = "collision"
            p["reason"] = f"also targeted by {seen[tgt]['old']}"
            seen[tgt]["status"] = "collision"
            seen[tgt]["reason"] = f"also targeted by {p['old']}"
        elif os.path.lexists(tgt) and tgt not in sources:
            p["status"] = "collision"
            p["reason"] = "target already exists on disk"
        else:
            seen[tgt] = p
    return plan


def execute_plan(plan):
    """Two-phase rename so swaps/cycles are safe. Returns list of errors."""
    errors = []
    renames = [p for p in plan if p["status"] == "rename"]
    if any(p["status"] == "collision" for p in plan):
        return ["aborted: collisions detected; resolve them first"]
    temps = {}
    for i, p in enumerate(renames):
        tmp = p["path"] + f".rename-plan-tmp-{i}-{os.getpid()}"
        try:
            os.rename(p["path"], tmp)
            temps[p["path"]] = tmp
        except OSError as e:
            errors.append(f"cannot stage {p['old']}: {e.strerror or e}")
    for p in renames:
        if p["path"] not in temps:
            continue
        try:
            os.rename(temps[p["path"]], p["target"])
        except OSError as e:
            errors.append(f"cannot rename {p['old']}: {e.strerror or e}")
    return errors


# ------------------------------------------------------------------- output
def use_color(args):
    if args.no_color or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def render_table(plan, color):
    rows = [(p["old"], p["new"], p["status"]) for p in plan]
    w_old = max([len("OLD")] + [len(r[0]) for r in rows])
    w_new = max([len("NEW")] + [len(r[1]) for r in rows])
    w_st = max([len("STATUS")] + [len(r[2]) for r in rows])
    colors = {"rename": "\033[32m", "unchanged": "\033[2m",
              "collision": "\033[31m", "error": "\033[31m"}
    reset = "\033[0m"
    lines = [f"{'OLD':<{w_old}}  {'NEW':<{w_new}}  {'STATUS':<{w_st}}",
             f"{'-' * w_old}  {'-' * w_new}  {'-' * w_st}"]
    for old, new, status in rows:
        st = status
        if color and status in colors:
            st = colors[status] + status + reset
        lines.append(f"{old:<{w_old}}  {new:<{w_new}}  {st:<{w_st}}")
    return "\n".join(lines)


def render_json(plan, applied):
    return json.dumps({
        "applied": applied,
        "plan": [
            {"old": p["old"], "new": p["new"], "status": p["status"],
             **({"reason": p["reason"]} if "reason" in p else {})}
            for p in plan
        ],
    }, indent=2, ensure_ascii=False)


# -------------------------------------------------------------------- input
def parse_rule_pair(value, flag):
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            f"{flag} needs FROM:TO (got {value!r})")
    return value.split(":", 1)


def parse_regex_pair(value):
    old, new = parse_rule_pair(value, "--regex")
    try:
        return re.compile(old), new
    except re.error as e:
        raise argparse.ArgumentTypeError(f"--regex bad pattern: {e}")


def collect_entries(paths, include_dirs, from_stdin, null_sep):
    entries = []
    seen = set()
    if from_stdin:
        data = sys.stdin.buffer.read()
        parts = data.split(b"\0") if null_sep else data.splitlines()
        paths = [p.decode("utf-8", "replace").strip()
                 for p in parts if p.strip()]
    for p in paths:
        ap = os.path.abspath(p)
        if ap in seen:
            continue
        seen.add(ap)
        if not os.path.lexists(ap):
            raise FileNotFoundError(f"no such file or directory: {p}")
        if os.path.isdir(ap) and not os.path.islink(ap):
            try:
                names = sorted(os.listdir(ap))
            except OSError as e:
                raise OSError(f"cannot list {p}: {e.strerror or e}")
            for n in names:
                if n in (".", ".."):
                    continue
                child = os.path.join(ap, n)
                if os.path.isdir(child) and not os.path.islink(child) \
                        and not include_dirs:
                    continue
                entries.append((n, child))
        else:
            if os.path.isdir(ap) and not include_dirs:
                continue
            entries.append((p, ap))
    return entries


def build_parser():
    ap = argparse.ArgumentParser(
        prog=PROG,
        description="Plan bulk file renames with pattern rules. "
                    "Dry-run by default; nothing is changed unless --apply is given.")
    ap.add_argument("paths", nargs="*", help="files or directories to rename")
    ap.add_argument("--replace", action="append", default=[], metavar="FROM:TO",
                    type=lambda v: parse_rule_pair(v, "--replace"),
                    help="literal substring replacement on the name (repeatable)")
    ap.add_argument("--regex", action="append", default=[], metavar="PATTERN:REPL",
                    type=parse_regex_pair,
                    help="regex substitution on the name (repeatable)")
    ap.add_argument("--strip", metavar="CHARS",
                    help="strip these characters from both ends of the name")
    ap.add_argument("--lower", action="store_true", help="lowercase the name")
    ap.add_argument("--upper", action="store_true", help="uppercase the name")
    ap.add_argument("--slug", action="store_true",
                    help="slugify the name (lowercase, spaces/punct -> '-')")
    ap.add_argument("--prefix", metavar="TEXT",
                    help="prepend TEXT; supports {n} with --number")
    ap.add_argument("--suffix", metavar="TEXT",
                    help="append TEXT before the extension; supports {n}")
    ap.add_argument("--number", metavar="FORMAT",
                    help="insert counter before extension, e.g. %%03d (sorted order)")
    ap.add_argument("--dirs", action="store_true",
                    help="also rename directories, not just files")
    ap.add_argument("--apply", action="store_true",
                    help="execute the renames (default is dry-run plan)")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output instead of the table")
    ap.add_argument("--null", action="store_true",
                    help="read NUL-separated paths from stdin")
    ap.add_argument("--no-color", action="store_true", help="disable colors")
    ap.add_argument("--version", action="version", version=f"{PROG} {VERSION}")
    return ap


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    rules = []
    for old, new in args.replace:
        rules.append(("replace", (old, new)))
    for pattern, repl in args.regex:
        rules.append(("regex", (pattern, repl)))
    if args.strip:
        rules.append(("strip", args.strip))
    if args.lower:
        rules.append(("lower", None))
    if args.upper:
        rules.append(("upper", None))
    if args.slug:
        rules.append(("slug", None))
    if args.prefix:
        rules.append(("prefix", args.prefix))
    if args.suffix:
        rules.append(("suffix", args.suffix))
    if not rules and not args.number:
        parser.error("no rename rules given; see --help")

    try:
        from_stdin = not args.paths and not sys.stdin.isatty()
        entries = collect_entries(args.paths, args.dirs, from_stdin, args.null)
    except (FileNotFoundError, OSError) as e:
        print(f"{PROG}: error: {e}", file=sys.stderr)
        return 1
    if not entries:
        print(f"{PROG}: nothing to rename", file=sys.stderr)
        return 0

    try:
        plan = build_plan(entries, rules, args.number)
    except ValueError as e:
        print(f"{PROG}: error: {e}", file=sys.stderr)
        return 1

    if args.apply:
        errors = execute_plan(plan)
        if errors:
            for e in errors:
                print(f"{PROG}: error: {e}", file=sys.stderr)
            return 1
        applied = True
    else:
        applied = False

    if args.json:
        print(render_json(plan, applied))
    else:
        print(render_table(plan, use_color(args)))
        if not args.apply:
            n = sum(1 for p in plan if p["status"] == "rename")
            print(f"\ndry-run: {n} rename(s) planned, nothing changed "
                  f"(use --apply to execute)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
