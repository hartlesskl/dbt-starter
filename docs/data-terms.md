# Data terms

The MIT license covers this project's code only; each data source keeps its publisher's terms (see docs/data-terms.md).

This file is the one place that holds the full credit for each source's data. Each source section below lists its link, the date its terms were read, and the publisher's own accuracy disclaimer. Short copies of the credit appear in `uv run load --help`, and a convention test fails when a copy differs from this file. The reading of each source's terms is recorded in the decision record at `docs/decisions/0008-data-terms-and-license.md`.

The access date is the day the publisher's terms were read. It is not a download date, and it changes only when someone reads the terms again.

<!-- example:begin -->
## NYC TLC Trip Record Data

- Source: NYC Taxi and Limousine Commission (TLC) Trip Record Data, https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page
- Accessed: 2026-11-26
- The TLC page says: "TLC makes no representations as to the accuracy of these data."

The data is downloaded at run time into `data/raw/`, which git ignores, and is never committed. The template commits no real TLC row; its tests use a small synthetic fixture. The TLC page names no license for the files, so this project does not redistribute them.
<!-- example:end -->
