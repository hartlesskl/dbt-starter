# 0008. The code is MIT; each data source keeps its terms

- **Date:** 2026-11-26
- **Status:** accepted
- **Reversibility:** one-way. Copies made under MIT keep MIT.

## Context

This repository is public and is meant to be copied as a template. Two questions follow. Under what terms may a copy use the code, and under what terms may anyone use the data the template shows? The answers differ, because the author wrote the code and a public body publishes each data source.

This record keeps the answers apart: one license for the code, and for each data source the publisher's own terms, read on a stated date and credited wherever the data is shown. The reading is the author's. It is not legal advice, and a team that copies the template and swaps in its own data has to read its own sources' terms.

<!-- example:begin -->
The example source is the NYC Taxi and Limousine Commission (TLC) Trip Record Data. It is the only data the template downloads by default, so its terms decide whether the template may commit any of it and what credit it owes.
<!-- example:end -->

## Evidence

The pages below were read on 2026-11-26, the access date, except where a row says otherwise. That date is the day the terms were read, not a download date, and it changes only when someone reads the terms again.

<!-- example:begin -->
| Source read | URL that works today | Finding |
|-------------|----------------------|---------|
| TLC Trip Record Data page | https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page | The page says: "TLC makes no representations as to the accuracy of these data." It follows a sentence saying the trip data was not created by the TLC. The page names no license and grants no reuse terms for the files. Its footer carries the City of New York copyright and trademark line. |
| NYC.gov Terms of Use | https://www.nyc.gov/main/terms-of-use | Section IV says the site's materials are the City's property with all rights reserved, and that a request to use City intellectual property goes to the agency concerned. Section VI says the materials are provided as is and as available, and that downloaded material is obtained at the user's own risk. |
| NYC Administrative Code, section 23-502, subdivision d (the Open Data Law) | https://nyc-laws.readthedocs.io/en/latest/nycadmincode/t23/c05/ | Read via a mirror; primary source not confirmed (the Administrative Code site, codelibrary.amlegal.com, answered HTTP 403 to the fetch). As the mirror gives it, public data sets are made available without a registration requirement, a license requirement or restrictions on their use, and a third party may be required to identify the source and version of a data set and to describe any modification. |

The old terms address `https://www.nyc.gov/html/data/terms-of-use.html` returns 404, and the NYC Open Data overview page carries no accuracy disclaimer or license statement, so neither is cited.
<!-- example:end -->

| Source read | URL | Finding |
|-------------|-----|---------|
| The code's license | `LICENSE` at the repository root, and the `license` field in `pyproject.toml` | The standard MIT text, copyright held by the author. It grants rights in the code and names no data. |
| USGS copyright and credits page | https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits | Read on 2026-11-27 (HTTP 200, no redirect), a day after the access date above, through a web-fetch agent and not by the author directly. The page says: "USGS-authored or produced data and information are considered to be in the U.S. Public Domain." It links the Department of the Interior disclaimer. It asks for proper credit through a citation and suggests the credit lines U.S. Geological Survey and Department of the Interior/USGS. It also says some non-USGS photographs, images and graphics on the site are used with the holder's permission and are marked as copyrighted. |

## Decision

1. **The MIT license covers the code only.**

   The `LICENSE` file, the `license` field in `pyproject.toml`, the README, `load --help`, `demo --help` and the Pages root page all say so in one sentence, and nothing says or implies that MIT covers any source's data. Each source keeps its publisher's terms. Those terms are read and recorded in this record, and the full credit for each source is in `docs/data-terms.md`. A convention test keeps every short copy of the credit in agreement with that file.

<!-- example:begin -->
2. **The TLC data is used as published, and none of it is committed.**

   - The data is public data of a City agency, and the Open Data Law, as the mirror gives it, bars license requirements and use restrictions on public data sets. So downloading it at run time into `data/raw/` and processing it is acceptable.
   - The TLC page and the nyc.gov terms reserve rights and grant no explicit reuse for the files, and the page does not say how far the statute reaches over files hosted on the TLC page rather than on the Open Data portal. So the template commits no real TLC row, sample or database file. The repository keeps a small synthetic fixture and downloads the real months at run time. A real sample would add legal doubt for no benefit.
   - Wherever the data is shown (the README, `load --help`, `demo --help`, the Pages root page and `docs/data-terms.md`), the template credits TLC with the link, the access date and the page's accuracy disclaimer, and states that the MIT license covers code only.
   - The access date is 2026-11-26. It is not a download date.
<!-- example:end -->

3. **A second public source may be committed as a few dozen rows, because it is public domain.**

   The swap-out guide uses a public-domain source (USGS earthquake data) as its worked example, and may commit a few dozen rows of it as Python literals in a support module, not as data files. The USGS page, read on 2026-11-27 (see the Evidence table), says USGS-authored or produced data is in the U.S. public domain and asks for credit through a citation; the credit used is U.S. Geological Survey. The page's exception for third-party photographs, images and graphics marked as copyrighted does not touch earthquake catalog rows. The guide's sample module names the query window the rows came from.

   The sample is the whole answer of one query to the USGS FDSN event service, fetched on 2026-11-27: the window `starttime=2024-01-01T04:00:00` to `endtime=2024-01-01T06:00:00`, `orderby=time-asc`, no magnitude floor. It holds 34 real rows, kept exactly as published, and 3 planted rows, each copied from a real row and changed by hand to fail a rule no real row fails, with a comment in the module saying what changed. The rows are Python literals in `tests/guide/usgs_sample.py`, not a data file. The credit is U.S. Geological Survey.

4. **These readings are the author's, not legal advice.**

   A later change to any page above is not detected until someone reads it again and moves the access date.

## Consequences

- Every place that shows data carries its credit and the code-only license sentence, and the convention test fails when a copy differs from `docs/data-terms.md`.
- Removing the example removes its credit, its evidence rows and its decision point, and leaves the MIT statement, the USGS row and the not-legal-advice point readable.
- A team that adds a source adds a `DATA_TERMS` entry to its source module, a section to `docs/data-terms.md` and an evidence row here, from its own reading of that source's terms.
- If the publisher's pages change, the credit and this record go stale until someone reads them again, and nothing in the build detects that.
