"""Command line of the SPC health check.

    spc-check data.csv --columns                                   show the column names of the file
    spc-check data.csv --value 量測值 --characteristic 特性 --subgroup 子組 --time 時間 \\
              --lsl-col LSL --usl-col USL --lcl-col LCL --ucl-col UCL --action 處置
    spc-check sheet.xlsx --sheet 金厚 --value X1 --value X2 --value X3 --value X4 --value X5 --lsl 0.03 --usl 0.1

One `--value` column: one row per measured value (with `--subgroup` the values are subgroups, without it individuals).
Several `--value` columns: one row per subgroup. Limits come from columns (`--lsl-col` ...) or are numbers (`--lsl` ...).
The report is written next to the file (`<file>.spc-check.html`) unless `--out` names another place. Nothing is sent anywhere.

Exit code 0: the report was written. 2: the file or the options have a problem (the message says which, with line numbers).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, replace
from pathlib import Path

from spc.data.csv_io import DataImportError
from spc.healthcheck.checks import Settings, check_all
from spc.healthcheck.reader import LIMITS, Columns, read_file, read_header
from spc.healthcheck.report import render_html, summary_text, to_json
from spc.healthcheck.texts import LANGUAGES


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spc-check", description="SPC health check: what keeps the control charts of a file from working. Offline; nothing is sent anywhere.")
    p.add_argument("file", type=Path, help="CSV file (UTF-8 or Big5) or *.xlsx workbook")
    p.add_argument("--columns", action="store_true", help="show the column names of the file and stop")
    p.add_argument("--value", action="append", default=[], metavar="COLUMN", help="column of the measured value; several times: the values of one subgroup per row")
    p.add_argument("--characteristic", metavar="COLUMN", help="column that names the characteristic (a file with several characteristics)")
    p.add_argument("--subgroup", metavar="COLUMN", help="column of the subgroup")
    p.add_argument("--time", metavar="COLUMN", help="column of the date and time")
    for key in LIMITS:
        p.add_argument(f"--{key}-col", metavar="COLUMN", help=f"column of the {key.upper()}")
        p.add_argument(f"--{key}", type=float, metavar="NUMBER", help=f"the {key.upper()} as a number, for every characteristic of the file")
    p.add_argument("--alarm", metavar="COLUMN", help="column with the alarm flag of the system the data come from (yes/no)")
    p.add_argument("--action", metavar="COLUMN", help="column with what was done about a point (any text counts)")
    p.add_argument("--sheet", help="sheet of the workbook (default: the first)")
    p.add_argument("--header-row", type=int, default=1, help="line of the column names (default 1)")
    p.add_argument("--delimiter", help="delimiter of the CSV file (default: found from the header line)")
    p.add_argument("--decimal", choices=(".", ","), default=".", help="decimal sign (default .)")
    p.add_argument("--encoding", default="auto", help="encoding of the CSV file (default: UTF-8, then Big5)")
    p.add_argument("--lang", choices=LANGUAGES, default="zh-TW", help="language of the report (default zh-TW)")
    p.add_argument("--out", type=Path, help="the report (default: <file>.spc-check.html next to the file)")
    p.add_argument("--json", type=Path, help="also write the result as JSON")
    p.add_argument("--target-pk", type=float, help=f"target of the capability index (default {Settings().target_pk})")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    read_options = {"sheet": args.sheet, "header_row": args.header_row, "delimiter": args.delimiter, "encoding": args.encoding}
    try:
        if args.columns:
            for name in read_header(args.file, **read_options):
                print(name)
            return 0
        if not args.value:
            print("name the column of the measured value with --value (see the columns with --columns)", file=sys.stderr)
            return 2
        columns = Columns(value=tuple(args.value), characteristic=args.characteristic, subgroup=args.subgroup, time=args.time, alarm=args.alarm,
                          action=args.action, **{key: getattr(args, f"{key}_col") for key in LIMITS})
        constants = {key: getattr(args, key) for key in LIMITS if getattr(args, key) is not None}
        series, info = read_file(args.file, columns, constants=constants, decimal=args.decimal, **read_options)
    except DataImportError as exc:
        print(f"{len(exc.issues)} problem(s) in the file:", file=sys.stderr)
        for issue in exc.issues[:50]:
            print(("  line %d: " % issue.line if issue.line else "  ") + issue.message, file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings = Settings() if args.target_pk is None else replace(Settings(), target_pk=args.target_pk)
    result = check_all(series, settings)
    file_info = asdict(info)
    out = args.out or args.file.with_name(args.file.name + ".spc-check.html")
    out.write_text(render_html(result, file_info, args.lang), encoding="utf-8")
    if args.json:
        args.json.write_text(to_json(result, file_info), encoding="utf-8")
    print(summary_text(result, args.lang))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
