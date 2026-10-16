# rename-plan

> Plan and apply bulk file renames with pattern rules — dry-run table first, `--json` for machines. Daily Tools #10.

## Why

Renaming 200 camera files or a messy Downloads folder by hand is slow and error-prone, and a wrong bulk rename is painful to undo. The usual answers — `perl-rename` or `mmv` — are not always installed, print no structured preview, and have no machine-readable output. `rename-plan` is a single zero-dependency Python file that shows you a before→after table with collision detection, stays in dry-run until you pass `--apply`, and can emit the whole plan as JSON so scripts can consume it.

## Install

```bash
git clone https://github.com/Kayzenn123/rename-plan.git
cd rename-plan
# no dependencies — stdlib only
```

Requires: Python >= 3.9

## Usage

```bash
python3 rename_plan.py --replace "IMG_:bali-trip-" --number "%02d" examples/camera-dump/
python3 rename_plan.py --slug --apply ~/Downloads/
find . -name "*.txt" | python3 rename_plan.py --upper --json
python3 rename_plan.py --help
```

| Flag | Function | Default |
| --- | --- | --- |
| `--replace FROM:TO` | literal substring replacement (repeatable) | — |
| `--regex PATTERN:REPL` | regex substitution (repeatable) | — |
| `--strip CHARS` | strip chars from both ends of the name | — |
| `--lower` / `--upper` | change case of the name | — |
| `--slug` | slugify: lowercase, spaces/punct → `-` | — |
| `--prefix TEXT` / `--suffix TEXT` | add text around the stem; supports `{n}` | — |
| `--number FORMAT` | insert counter before extension, e.g. `%03d` | — |
| `--dirs` | also rename directories | files only |
| `--apply` | execute the renames | dry-run |
| `--json` | machine-readable plan instead of the table | table |
| `--null` | read NUL-separated paths from stdin | newline |
| `--no-color` | disable colors (also honors `NO_COLOR`) | auto |

Exit codes: `0` ok, `1` runtime error (collision on apply, missing path, rename failed), `2` bad usage.

## Example output

Command:

```bash
rename-plan --replace "IMG_:bali-trip-" --number "%02d" demo1/
```

Output (real, copied from an actual run):

```text
OLD                      NEW                              STATUS
-----------------------  -------------------------------  ------
IMG_20261016_093211.jpg  bali-trip-20261016_09321101.jpg  rename
IMG_20261016_093310.jpg  bali-trip-20261016_09331002.jpg  rename
IMG_20261016_094502.jpg  bali-trip-20261016_09450203.jpg  rename
IMG_20261016_101133.jpg  bali-trip-20261016_10113304.jpg  rename
IMG_20261016_101200.MP4  bali-trip-20261016_10120005.MP4  rename
```

`--apply` really renames; a collision aborts everything (exit 1, nothing touched):

```text
rename-plan: error: aborted: collisions detected; resolve them first
```

And `--json` from a piped file list:

```json
{
  "applied": false,
  "plan": [
    {
      "old": "demo3/a.txt",
      "new": "demo3/z.txt",
      "status": "collision",
      "reason": "also targeted by demo3/b.txt"
    },
    {
      "old": "demo3/b.txt",
      "new": "demo3/z.txt",
      "status": "collision",
      "reason": "also targeted by demo3/a.txt"
    },
    {
      "old": "demo3/keep.txt",
      "new": "demo3/keep.txt",
      "status": "unchanged"
    }
  ]
}
```

## How it works

Rules apply in a fixed order — replace → regex → strip → case/slug → prefix/suffix → numbering — to the file stem; the extension is preserved. Numbering counts in sorted order so runs are deterministic. Collision detection marks two sources mapping to the same target, or a target that already exists on disk and is not itself being renamed away. On `--apply`, renames happen in two phases (source → unique temp name → final target) so swaps and rename cycles are safe. Core logic (`apply_rules`, `build_plan`, `execute_plan`) is pure and separated from CLI I/O, which is why the test suite covers it directly.

## Limitations

- Non-recursive: directories are scanned one level deep; nested trees are not walked.
- Collision detection is exact-string based; case-only collisions on case-insensitive filesystems (macOS/Windows) are not flagged.
- `--number` counts every planned file in sorted order, including files whose names end up unchanged by other rules.
- No undo: preview the dry-run table carefully before `--apply`.
- Renaming across filesystems/mounts is not special-cased (`os.rename` semantics apply).

## Development

```bash
python3 -m unittest discover -s test
```

## License

MIT

---
Ringkasan (Bahasa Indonesia): rename-plan merencanakan rename massal file lewat aturan pola (ganti teks, regex, slug, prefix/suffix, penomoran) dan menampilkan tabel before→after plus deteksi tabrakan — default-nya dry-run, tidak ada yang berubah sampai flag `--apply` dipakai, dan ada output `--json` untuk kebutuhan script. Cara pakai: `python3 rename_plan.py --slug --apply <folder>`. Batasan utama: tidak rekursif ke subfolder dan tidak ada fitur undo.
