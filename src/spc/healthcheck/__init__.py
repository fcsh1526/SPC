"""SPC health check: reads the data that another SPC system (or a spreadsheet) holds and reports what keeps its control charts from working.

It is an offline tool for one person on one computer: it reads a file, writes a report next to it, and sends nothing anywhere.
It does not judge the process, it judges the *use* of SPC on it:

  limits       control limits copied from the specification, far wider or narrower than the process, or off its centre
  subgroups    more variation between the subgroups than within them: limits from the within variation are too narrow (false alarms)
  alarms       too many alarms, none at all, alarms without a recorded action
  capability   an index from a process that is not stable; an index below the target
  measurement  a resolution too coarse for the tolerance or for the variation; the same value again and again

    reader    one file -> the series of every characteristic (the user names the columns; nothing is guessed)
    checks    the checks and their settings (every threshold is a named setting and is printed in the report)
    texts     the findings and the report in Traditional Chinese and English
    report    one self-contained HTML file (inline SVG, no script, no external file) and a JSON record

Command line: `spc-check` (see spc.healthcheck.__main__).
"""

from spc.healthcheck.checks import Finding, Settings, check_series, check_all
from spc.healthcheck.reader import Columns, Series, read_file

__all__ = ["Columns", "Finding", "Series", "Settings", "check_all", "check_series", "read_file"]
