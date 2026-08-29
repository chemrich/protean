#!/usr/bin/env python3
"""Settle one benchmark run's parameters, and say where each one came from.

This was bash inside the workflow until a run measured the wrong thing.

`run.conf` is append-only on purpose: each block is a measurement, kept in order,
so a table in a job summary can be traced back to the settings that produced it.
The bash read every key with `grep -E "^key=" run.conf | tail -1`, which reads
the last line *in the file* rather than the last line *in the block*. A block
that does not mention a key therefore inherits it from some earlier block —
silently, and across a boundary the file's own comments describe as a boundary.

That is not hypothetical. Run 33252777716 asked for twelve rows with the
occlusion pass on, and got `postprocessing=no-occlusion` from a block three
experiments older. Twelve rows, four minutes of runner time, and a clean table
that answered a question nobody had asked — about a regression that lives inside
the occlusion pass. The job echoed the setting, which is the only reason it was
caught rather than believed.

So: **a block is self-contained**. Resolution reads only from the last line
beginning `versions=` to the end of the file, and anything the block does not
say takes the documented default. And every value is printed with its
provenance — an input, a line number in the conf, or the default — because the
line that would have made this obvious at a glance is the one that says where a
value came from, not the one that says what it is.

Reads the overrides from the environment (`IN_VERSIONS`, `IN_LEVELS`, ...) so
that nothing from a workflow input is ever substituted into a shell script.
Writes `key=value` lines on stdout for `$GITHUB_OUTPUT`, and the provenance on
stderr for the log.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# key -> (default, pattern it must match).
#
# The patterns are the reason nothing downstream has to defend itself. `versions`
# carries `:`, `=`, `@` and `/` for the shader-transplant form, and no shell
# metacharacter, no space and no quote — every one of these reaches bash only
# inside a quoted expansion, and this charset is what makes that safe rather
# than merely intended.
SETTINGS = {
    "versions": ("4.18.0,5.11.0,4.18.0,5.11.0", r"^[0-9A-Za-z.,:=@/_+-]+$"),
    "levels": ("4,1", r"^[0-9,]+$"),
    "repeats": ("8", r"^[0-9]+$"),
    "warmup": ("2", r"^[0-9]+$"),
    "size": ("800x600", r"^[0-9]+x[0-9]+$"),
    "fullpath": ("2", r"^[0-9]+$"),
    "postprocessing": ("default", r"^(default|no-occlusion|no-antialiasing|none)$"),
    "occlusionsamples": ("0", r"^[0-9]+$"),
    "occlusionblur": ("0", r"^[0-9]+$"),
    "baseline": ("4.18.0", r"^[0-9A-Za-z.,:=@/_+-]*$"),
}

# Every block starts by naming what it measures, so this is the block boundary.
BLOCK_START = "versions="


def last_block(text: str) -> tuple[list[str], int]:
    """The lines of the final block, and the file line number it starts at."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(BLOCK_START)]
    if not starts:
        return [], 0
    return lines[starts[-1] :], starts[-1] + 1


def resolve(conf_text: str, env: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Return the settled values and, for each, where it came from."""
    block, offset = last_block(conf_text)
    from_block: dict[str, tuple[str, int]] = {}
    for n, line in enumerate(block):
        stripped = line.strip()
        if "=" not in stripped:
            continue
        # A comment cannot be mistaken for a setting, and not by accident: the
        # key has to match a name in SETTINGS exactly, and a commented line
        # carries its `#` into the key. There was a `startswith("#")` guard here
        # as well; it was removed because no test could make it matter, and an
        # unreachable guard is a place the next person stops looking.
        key, _, value = stripped.partition("=")
        if key in SETTINGS:
            # Last wins *within* the block, which is what a block being a record
            # of one run means.
            from_block[key] = (value, offset + n)

    values: dict[str, str] = {}
    provenance: dict[str, str] = {}
    for key, (default, _) in SETTINGS.items():
        override = env.get(f"IN_{key.upper()}", "").strip()
        if override:
            values[key], provenance[key] = override, "workflow input"
        elif key in from_block:
            value, line_no = from_block[key]
            values[key], provenance[key] = value, f"run.conf line {line_no}"
        else:
            values[key], provenance[key] = default, "default (not set by this block)"
    return values, provenance


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conf", type=Path, default=Path("bench/molstar-capture/run.conf"))
    args = ap.parse_args()

    conf_text = args.conf.read_text() if args.conf.is_file() else ""
    values, provenance = resolve(conf_text, dict(os.environ))

    bad = [
        f"::error::bad {key}: {values[key]}"
        for key, (_, pattern) in SETTINGS.items()
        if not re.match(pattern, values[key])
    ]
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1

    for key in SETTINGS:
        print(f"  {key:17} = {values[key]}   <- {provenance[key]}", file=sys.stderr)

    for key, value in values.items():
        if key == "size":
            width, _, height = value.partition("x")
            print(f"width={width}")
            print(f"height={height}")
        else:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
