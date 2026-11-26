"""The committed sample the swap-out guide lands: real USGS earthquake rows as Python literals.

Credit: U.S. Geological Survey. USGS-authored or produced data is in the U.S. public domain, and the
USGS asks for credit; the reading of its terms, the query window below and the row counts are
recorded in docs/decisions/0008-data-terms-and-license.md.

The rows are literals, not a data file, so the repository holds no data file (ADR 0008, D-14). They
are the whole answer of one query to the USGS FDSN event service, kept exactly as published (text,
in the published column order), so the guide's test lands real, unedited rows. CI never downloads:
the guide's test writes these rows to a temporary folder and lands them with `--from-dir`. The
real download in the guide uses the same query, so the committed rows are the file the guide's
`SAMPLE_URL` returns. The USGS revises past events now and then (the `updated` column), so a
download made later can differ from these rows; CI never refreshes them.

Two of the real rows have a depth below zero (the event is above the datum), so the guide's warn
rule fires on real data. No real row fails an error rule, so `PLANTED_ROWS` adds three rows that do,
each copied from a real row and changed by hand, with a comment that says what changed and why.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import NamedTuple


class SampleWindow(NamedTuple):
    """The fixed query that produced the rows: a two-hour window, ordered by time, no magnitude floor."""

    start: str
    end: str
    orderby: str


SAMPLE_WINDOW = SampleWindow(
    start="2024-01-01T04:00:00", end="2024-01-01T06:00:00", orderby="time-asc"
)
SAMPLE_URL = (
    "https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv"
    f"&starttime={SAMPLE_WINDOW.start}&endtime={SAMPLE_WINDOW.end}&orderby={SAMPLE_WINDOW.orderby}"
)
FILE_NAME = "quakes.csv"

# The 22 column names, as the service publishes them (the loader maps them case-insensitively).
HEADER: tuple[str, ...] = (
    "time", "latitude", "longitude", "depth", "mag", "magType", "nst", "gap", "dmin", "rms", "net",
    "id", "updated", "place", "type", "horizontalError", "depthError", "magError", "magNst",
    "status", "locationSource", "magSource",
)  # fmt: skip

# The 34 real rows of the query, in published order. Event 04:53:08 and event 04:54:47 have a
# depth below zero; every event has an id, a readable time, a magnitude and a latitude in range.
# fmt: off
SAMPLE_ROWS: list[tuple[str, ...]] = [
    ('2024-01-01T04:05:02.992Z', '39.5624', '-119.8064', '5.4', '-0.1', 'ml', '12', '77.29', '0.01', '0.1715', 'nn', 'nn00871140', '2024-01-01T15:58:57.420Z', '3 km N of Reno, Nevada', 'earthquake', '', '1.1', '0', '1', 'reviewed', 'nn', 'nn'),
    ('2024-01-01T04:06:58.991Z', '65.9371', '-149.5649', '4.7', '1', 'ml', '', '', '', '0.73', 'ak', 'ak0241pyl9e', '2024-01-21T02:56:01.933Z', '22 km WSW of Stevens Village, Alaska', 'earthquake', '', '0.5', '', '', 'reviewed', 'ak', 'ak'),
    ('2024-01-01T04:15:47.930Z', '19.257333755493', '-155.3913269043', '30.489999771118', '2.22000003', 'md', '45', '131', '', '0.140000001', 'hv', 'hv73706022', '2024-01-01T04:19:04.270Z', '10 km ENE of Pāhala, Hawaii', 'earthquake', '0.55', '0.569999993', '0.219999999', '9', 'automatic', 'hv', 'hv'),
    ('2024-01-01T04:16:19.094Z', '29.326', '-97.463', '7.5215', '1.9', 'ml', '11', '68', '0.1', '0.2', 'tx', 'tx2024aail', '2025-07-02T20:08:26.048Z', '17 km ENE of Smiley, Texas', 'earthquake', '0.98956148914526', '1.144648496741', '0.1', '6', 'reviewed', 'tx', 'tx'),
    ('2024-01-01T04:21:51.080Z', '37.5741667', '-118.8345', '9.65', '0.27', 'md', '14', '270', '0.0164', '0.02', 'nc', 'nc73983621', '2024-01-01T19:49:10.514Z', '14 km W of Toms Place, CA', 'earthquake', '1.15', '1.53', '0.188', '12', 'reviewed', 'nc', 'nc'),
    ('2024-01-01T04:26:14.080Z', '38.8136667', '-122.8186667', '2.65', '1.26', 'md', '44', '48', '0.009508', '0.05', 'nc', 'nc73983626', '2024-01-09T05:09:14.666Z', '7 km NW of The Geysers, CA', 'earthquake', '0.14', '0.28', '0.14', '37', 'reviewed', 'nc', 'nc'),
    ('2024-01-01T04:31:31.487Z', '69.1334', '-146.5153', '6.4', '1.3', 'ml', '', '', '', '0.34', 'ak', 'ak0241q3so8', '2024-01-22T19:24:33.994Z', '119 km NNW of Arctic Village, Alaska', 'earthquake', '', '0.4', '', '', 'reviewed', 'ak', 'ak'),
    ('2024-01-01T04:31:41.038Z', '60.3567', '-152.5034', '88.3', '1.7', 'ml', '', '', '', '0.55', 'ak', 'ak0241q3t9z', '2024-01-21T02:56:07.957Z', '57 km NW of Ninilchik, Alaska', 'earthquake', '', '0.8', '', '', 'reviewed', 'ak', 'ak'),
    ('2024-01-01T04:35:53.490Z', '57.046666666667', '-135.64533333333', '5.83', '0.63', 'ml', '4', '171', '', '0.05', 'av', 'av91126293', '2024-01-03T01:26:28.920Z', '19 km W of Sitka, Alaska', 'earthquake', '0.9', '0.78', '0.12972172405102', '4', 'reviewed', 'av', 'av'),
    ('2024-01-01T04:38:39.720Z', '38.8255', '-122.7978333', '2.08', '0.44', 'md', '24', '41', '0.00979', '0.03', 'nc', 'nc73983631', '2024-01-01T21:14:14.007Z', '6 km NW of The Geysers, CA', 'earthquake', '0.18', '0.23', '0.177', '22', 'reviewed', 'nc', 'nc'),
    ('2024-01-01T04:39:13.814Z', '62.5382', '-150.7412', '76.1', '1.3', 'ml', '', '', '', '0.73', 'ak', 'ak0241q5gwl', '2024-01-21T02:56:08.171Z', '4 km NNE of Petersville, Alaska', 'earthquake', '', '0.3', '', '', 'reviewed', 'ak', 'ak'),
    ('2024-01-01T04:39:53.550Z', '46.196', '-122.19516666667', '8.2', '0.07', 'ml', '11', '104', '0.004822', '0.17', 'uw', 'uw61978526', '2024-01-02T18:58:12.010Z', '37 km NNE of Amboy, Washington', 'earthquake', '0.71', '1.01', '0.11313843372686', '6', 'reviewed', 'uw', 'uw'),
    ('2024-01-01T04:42:43.243Z', '39.5607', '-119.8052', '4.9', '0.7', 'ml', '12', '77.55', '0.011', '0.1893', 'nn', 'nn00871143', '2024-01-01T16:02:07.284Z', '3 km NNE of Reno, Nevada', 'earthquake', '', '1', '0.5', '4', 'reviewed', 'nn', 'nn'),
    ('2024-01-01T04:43:35.400Z', '19.02799987793', '-155.3681640625', '32.290000915527', '1.80999994', 'md', '40', '214', '', '0.140000001', 'hv', 'hv73706052', '2024-01-01T04:46:55.550Z', '22 km SSE of Pāhala, Hawaii', 'earthquake', '0.69', '0.980000019', '1.11000001', '8', 'automatic', 'hv', 'hv'),
    ('2024-01-01T04:49:01.244Z', '44.4338', '149.243', '35', '4.5', 'mb', '79', '135', '4.786', '0.5', 'us', 'us6000m0x6', '2024-03-09T21:38:14.040Z', '139 km SE of Kuril’sk, Russia', 'earthquake', '9.32', '1.875', '0.077', '49', 'reviewed', 'us', 'us'),
    ('2024-01-01T04:49:20.360Z', '33.2101667', '-116.0543333', '10.54', '1.27', 'ml', '57', '54', '0.07391', '0.2', 'ci', 'ci40453335', '2024-01-01T18:00:56.250Z', '10 km NE of Ocotillo Wells, CA', 'earthquake', '0.16', '0.43', '0.143', '27', 'reviewed', 'ci', 'ci'),
    ('2024-01-01T04:49:54.374Z', '60.5725', '-151.0168', '47', '1.9', 'ml', '', '', '', '0.69', 'ak', 'ak0241q7oyq', '2024-01-21T02:56:02.350Z', '5 km NE of Ridgeway, Alaska', 'earthquake', '', '0.6', '', '', 'reviewed', 'ak', 'ak'),
    ('2024-01-01T04:51:02.177Z', '62.5718', '-151.2719', '86.5', '2.8', 'ml', '', '', '', '0.65', 'ak', 'ak0241q80m4', '2024-03-09T21:38:14.040Z', '27 km WNW of Petersville, Alaska', 'earthquake', '', '0.3', '', '', 'reviewed', 'ak', 'ak'),
    ('2024-01-01T04:53:08.170Z', '38.4805', '-112.847', '-0.15', '-0.71', 'md', '7', '107', '0.02447', '0.04', 'uu', 'uu80033701', '2024-01-02T17:06:23.400Z', '17 km ENE of Milford, Utah', 'earthquake', '0.4', '0.16', '0.28942852117737', '3', 'reviewed', 'uu', 'uu'),
    ('2024-01-01T04:54:47.440Z', '60.050666666667', '-153.06383333333', '-2.97', '-0.69', 'ml', '5', '146', '', '0.09', 'av', 'av92170236', '2024-01-03T21:03:45.870Z', '65 km ENE of Pedro Bay, Alaska', 'earthquake', '0.61', '0.94', '0.23077030123969', '5', 'reviewed', 'av', 'av'),
    ('2024-01-01T05:04:22.779Z', '59.7524', '-153.0367', '93.4', '2.1', 'ml', '', '', '', '0.51', 'ak', 'ak0241qjf33', '2024-01-21T02:56:02.960Z', '60 km E of Pedro Bay, Alaska', 'earthquake', '', '0.5', '', '', 'reviewed', 'ak', 'ak'),
    ('2024-01-01T05:08:43.543Z', '-15.0626', '167.4895', '121.933', '4.4', 'mb', '29', '81', '6.252', '0.61', 'us', 'us6000m33r', '2024-03-09T21:40:06.040Z', '44 km E of Port-Olry, Vanuatu', 'earthquake', '10.56', '5.305', '0.127', '20', 'reviewed', 'us', 'us'),
    ('2024-01-01T05:15:35.840Z', '33.4068333', '-117.0305', '17.17', '1.44', 'ml', '63', '29', '0.01777', '0.19', 'ci', 'ci40453351', '2024-01-03T16:45:50.883Z', '6 km NE of Pala, CA', 'earthquake', '0.16', '0.41', '0.199', '35', 'reviewed', 'ci', 'ci'),
    ('2024-01-01T05:19:00.363Z', '-23.3645', '-179.8743', '540.187', '4.5', 'mb', '74', '99', '4.846', '0.65', 'us', 'us6000m0x8', '2024-03-09T21:38:14.040Z', 'south of the Fiji Islands', 'earthquake', '13.65', '7.749', '0.071', '58', 'reviewed', 'us', 'us'),
    ('2024-01-01T05:21:20.600Z', '32.9633333', '-115.5408333', '5.78', '1.36', 'ml', '20', '75', '0.06392', '0.2', 'ci', 'ci40453359', '2024-01-03T16:50:10.304Z', '2 km SSW of Brawley, CA', 'earthquake', '0.3', '0.5', '0.206', '7', 'reviewed', 'ci', 'ci'),
    ('2024-01-01T05:22:53.884Z', '-7.8936', '117.8591', '10', '4.7', 'mb', '55', '61', '0.931', '0.39', 'us', 'us6000m0x9', '2024-03-09T21:38:15.040Z', '70 km NE of Pototano, Indonesia', 'earthquake', '5.91', '1.355', '0.086', '41', 'reviewed', 'us', 'us'),
    ('2024-01-01T05:24:49.941Z', '59.3326', '-152.8244', '61.3', '1.7', 'ml', '', '', '', '0.66', 'ak', 'ak0241qnrk2', '2024-01-21T02:56:08.382Z', '51 km W of Nanwalek, Alaska', 'earthquake', '', '0.3', '', '', 'reviewed', 'ak', 'ak'),
    ('2024-01-01T05:30:00.261Z', '37.4704', '-117.8724', '1.6', '1.1', 'ml', '8', '89.91', '0.13', '0.1278', 'nn', 'nn00871082', '2024-01-02T22:24:14.700Z', '27 km SE of Dyer, Nevada', 'earthquake', '', '3.1', '0.44', '6', 'reviewed', 'nn', 'nn'),
    ('2024-01-01T05:37:40.490Z', '38.8253326', '-122.7646637', '2.05', '0.71', 'md', '13', '126', '0.006568', '0.01', 'nc', 'nc73983636', '2024-01-01T06:28:10.583Z', '4 km W of Cobb, CA', 'earthquake', '0.36', '0.55', '0.07', '14', 'automatic', 'nc', 'nc'),
    ('2024-01-01T05:41:05.040Z', '34.294', '-117.9301667', '9.47', '1.46', 'ml', '57', '30', '0.08842', '0.19', 'ci', 'ci40453367', '2024-01-01T17:46:48.640Z', '17 km NNE of Bradbury, CA', 'earthquake', '0.15', '0.45', '0.249', '31', 'reviewed', 'ci', 'ci'),
    ('2024-01-01T05:44:20.820Z', '53.892333333333', '-166.937', '7.66', '-0.27', 'ml', '5', '223', '', '0.11', 'av', 'av92170261', '2024-01-03T01:34:57.400Z', '25 km W of Dutch Harbor, Alaska', 'earthquake', '0.6', '1.05', '0.16743545237866', '5', 'reviewed', 'av', 'av'),
    ('2024-01-01T05:51:41.640Z', '35.487', '-118.29', '5.5', '1', 'ml', '25', '55', '0.1318', '0.11', 'ci', 'ci40453383', '2024-01-03T16:57:31.012Z', '22 km ESE of Bodfish, CA', 'earthquake', '0.14', '0.7', '0.203', '11', 'reviewed', 'ci', 'ci'),
    ('2024-01-01T05:53:56.165Z', '59.8479', '-152.5882', '85.9', '1.9', 'ml', '', '', '', '0.45', 'ak', 'ak0241qtzr3', '2024-01-21T02:56:03.217Z', '43 km W of Anchor Point, Alaska', 'earthquake', '', '0.5', '', '', 'reviewed', 'ak', 'ak'),
    ('2024-01-01T05:56:55.370Z', '38.8141667', '-122.8181667', '2.73', '1.23', 'md', '51', '33', '0.0101', '0.03', 'nc', 'nc73983646', '2024-01-09T04:10:12.345Z', '7 km NW of The Geysers, CA', 'earthquake', '0.13', '0.2', '0.163', '41', 'reviewed', 'nc', 'nc'),
]
# fmt: on

# Rows that fail an error rule on purpose. Each is a real row of SAMPLE_ROWS with the changes named
# in its comment, so the guide's quarantine path runs. They are not USGS data.
# fmt: off
PLANTED_ROWS: list[tuple[str, ...]] = [
    # Copy of nc73983621 with the id blanked: a feed that drops an id. Fails QUAKE_ID_MISSING only.
    ('2024-01-01T04:21:51.080Z', '37.5741667', '-118.8345', '9.65', '0.27', 'md', '14', '270', '0.0164', '0.02', 'nc', '', '2024-01-01T19:49:10.514Z', '14 km W of Toms Place, CA', 'earthquake', '1.15', '1.53', '0.188', '12', 'reviewed', 'nc', 'nc'),
    # Copy of ak0241q3so8 with a new id and the time written with an offset instead of Z: a feed
    # that changes its time format. The guide strips only a Z, so the offset stays and the time
    # does not parse. Fails QUAKE_TIME_UNPARSEABLE only.
    ('2024-01-01T04:31:31.487+05:00', '69.1334', '-146.5153', '6.4', '1.3', 'ml', '', '', '', '0.34', 'ak', 'planted-2', '2024-01-22T19:24:33.994Z', '119 km NNW of Arctic Village, Alaska', 'earthquake', '', '0.4', '', '', 'reviewed', 'ak', 'ak'),
    # Copy of nn00871140 with a new id, the latitude mistyped as 139.5624 and the magnitude blanked:
    # a typo and a gap in one row. Fails QUAKE_MAGNITUDE_MISSING and QUAKE_LATITUDE_INVALID.
    ('2024-01-01T04:05:02.992Z', '139.5624', '-119.8064', '5.4', '', 'ml', '12', '77.29', '0.01', '0.1715', 'nn', 'planted-3', '2024-01-01T15:58:57.420Z', '3 km N of Reno, Nevada', 'earthquake', '', '1.1', '0', '1', 'reviewed', 'nn', 'nn'),
]
# fmt: on

# The reason codes each planted row must get, in rule order (the guide's rules, plan order).
PLANTED_REASON_CODES: tuple[str, ...] = (
    "QUAKE_ID_MISSING",
    "QUAKE_TIME_UNPARSEABLE",
    "QUAKE_MAGNITUDE_MISSING|QUAKE_LATITUDE_INVALID",
)


def write_sample(folder: Path) -> Path:
    """Write the real rows then the planted rows to `folder/quakes.csv` and return the path.

    The folder is created when it is missing. The file has the published header line, UTF-8 text
    and unix line endings.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / FILE_NAME
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows([*SAMPLE_ROWS, *PLANTED_ROWS])
    return path
