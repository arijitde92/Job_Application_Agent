"""
Tests for the ``calculate_yoe`` resume tool.

The tool is what the resume_analyzer agent calls to turn a list of
(start_date, end_date) position pairs into the ``years_of_experience`` value,
so these tests exercise it the way the agent does — through ``.run()``, which
also runs CrewAI's argument validation — and check that every failure raises an
exception carrying an actionable message the agent can retry from.

Expected values follow the tool's documented arithmetic: the day spans of all
pairs are summed, the total is split into whole years (365 days) and whole
months (365/12 ≈ 30.44 days) with the leftover days dropped, one month is
discounted for every calendar month shared by two neighbouring positions, and
the result is ``years + months / 12`` rounded to one decimal place.
"""

from datetime import date, timedelta

import pytest

from app.services.crew.resume_tools import (
    _calculate_yoe,
    _count_shared_months,
    calculate_yoe,
)


def _days_ago(days: int) -> str:
    """ISO date string for ``days`` days before today."""
    return (date.today() - timedelta(days=days)).isoformat()


# Five hand-checked samples covering the shapes a real resume produces.
VALID_SAMPLES = [
    pytest.param(
        # 1. One completed position, canonical YYYY-MM-DD, as a list of tuples.
        #    2020-01-01 -> 2021-12-31 = 730 days = 2 years + 0 months.
        [("2020-01-01", "2021-12-31")],
        2.0,
        id="single_completed_position",
    ),
    pytest.param(
        # 2. Two consecutive positions; the gap between them is not counted.
        #    731 days + 579 days = 1310 days = 3 years + 215 days
        #    -> 215 // 30.44 = 7 months -> 43 months. June 2020 is shared (the
        #    first ends 2020-06-01, the second starts 2020-06-15), so one month
        #    is discounted -> 42 months -> 42/12 = 3.5.
        [("2018-06-01", "2020-06-01"), ("2020-06-15", "2022-01-15")],
        3.5,
        id="two_consecutive_positions_sharing_a_month",
    ),
    pytest.param(
        # 3. One past position + the current one (null end_date -> today).
        #    365 days + 183 days = 548 days = 1 year + 183 days
        #    -> 183 // 30.44 = 6 months -> 1 + 6/12 = 1.5.
        [(_days_ago(1000), _days_ago(635)), (_days_ago(183), None)],
        1.5,
        id="current_position_null_end_date",
    ),
    pytest.param(
        # 4. Every accepted date format, incl. a partial date (-> first day of
        #    the month/year), an ISO datetime and padded whitespace.
        #    366 days + 516 days = 882 days = 2 years + 152 days
        #    -> 152 // 30.44 = 4 months -> 2 + 4/12 = 2.333 -> 2.3.
        [("2019/03/01", "2020-03-01T00:00:00"), (" 2021 ", "2022-06")],
        2.3,
        id="multiple_valid_date_formats",
    ),
    pytest.param(
        # 5. A sub-year internship: no whole year, months only.
        #    181 days -> 181 // 30.44 = 5 months -> 5/12 = 0.416 -> 0.4.
        [("2023-01-01", "2023-07-01")],
        0.4,
        id="sub_year_internship",
    ),
]


@pytest.mark.parametrize("date_ranges, expected", VALID_SAMPLES)
def test_calculate_yoe_valid_samples(date_ranges, expected):
    result = calculate_yoe.run(date_ranges=date_ranges)
    assert isinstance(result, float)
    assert result == expected


def test_no_positions_returns_zero():
    assert calculate_yoe.run(date_ranges=[]) == 0.0


def test_null_end_date_uses_today():
    # Frozen "today" so the substitution is checked exactly:
    # 2024-07-28 -> 2026-07-28 = 730 days = 2 years.
    assert _calculate_yoe([("2024-07-28", None)], today=date(2026, 7, 28)) == 2.0


@pytest.mark.parametrize("end_date", ["", "  ", "Present", "current", "NULL", "N/A"])
def test_present_style_end_dates_are_treated_as_current(end_date):
    """LLMs often write "Present" instead of null for the current position."""
    assert calculate_yoe.run(date_ranges=[(_days_ago(730), end_date)]) == 2.0


def test_accepts_json_string_argument():
    """A stringified array (a common LLM slip) is decoded rather than rejected."""
    assert calculate_yoe.func('[["2020-01-01", "2021-12-31"]]') == 2.0


# --- shared-month discount ------------------------------------------------


def test_shared_month_is_discounted_once():
    """
    A month that ends one position and starts the next belongs to both spans,
    so it is counted once. These two inputs differ only in whether June 2020 is
    shared: the one with FEWER days scores higher because nothing is discounted.
    """
    shares_june_2020 = [("2018-06-01", "2020-06-01"), ("2020-06-15", "2022-01-15")]
    shares_nothing = [("2018-06-01", "2020-05-30"), ("2020-06-15", "2022-01-15")]

    assert calculate_yoe.run(date_ranges=shares_june_2020) == 3.5
    assert calculate_yoe.run(date_ranges=shares_nothing) == 3.6


def test_back_to_back_positions_share_the_handover_month():
    """
    Positions that meet on the same day: 365 + 334 = 699 days = 1 year +
    334 days -> 10 months -> 22 months, minus the shared January 2020 -> 21
    months -> 21/12 = 1.75 -> 1.8. (One decimal place absorbs this particular
    discount; test_shared_month_is_discounted_once pins a visible one.)
    """
    assert calculate_yoe.run(
        date_ranges=[("2019-01-01", "2020-01-01"), ("2020-01-01", "2020-11-30")]
    ) == 1.8


def test_shared_month_counted_for_each_occurrence():
    """Three back-to-back positions share two month boundaries -> -2 months."""
    chained = [
        ("2019-01-01", "2020-06-01"),
        ("2020-06-01", "2022-01-01"),
        ("2022-01-01", "2023-01-01"),
    ]
    # 517 + 579 + 365 = 1461 days = 4 years + 1 day -> 48 months, minus the two
    # shared months (June 2020, January 2022) -> 46/12 = 3.833 -> 3.8.
    assert calculate_yoe.run(date_ranges=chained) == 3.8


def test_shared_month_detected_when_positions_are_newest_first():
    """Resumes usually list the current job first; the discount still applies."""
    oldest_first = [
        ("2019-01-01", "2020-06-01"),
        ("2020-06-01", "2022-01-01"),
        ("2022-01-01", "2023-01-01"),
    ]
    assert calculate_yoe.run(date_ranges=list(reversed(oldest_first))) == 3.8


@pytest.mark.parametrize(
    "spans, expected_shared",
    [
        # One position ends and the next starts on 2020-01-01: January 2020 is
        # inside both spans, so it is shared.
        ([(date(2019, 1, 1), date(2020, 1, 1)), (date(2020, 1, 1), date(2020, 11, 30))], 1),
        # Same month, different days still counts.
        ([(date(2019, 1, 1), date(2020, 1, 3)), (date(2020, 1, 28), date(2020, 11, 30))], 1),
        # Adjacent months do not.
        ([(date(2019, 1, 1), date(2020, 1, 31)), (date(2020, 2, 1), date(2020, 11, 30))], 0),
        # Same month number in a different year does not.
        ([(date(2019, 1, 1), date(2020, 1, 1)), (date(2021, 1, 1), date(2021, 11, 30))], 0),
        # A single position has no neighbour to share with.
        ([(date(2019, 1, 1), date(2020, 1, 1))], 0),
    ],
)
def test_count_shared_months(spans, expected_shared):
    assert _count_shared_months(spans) == expected_shared


# --- error handling -------------------------------------------------------


@pytest.mark.parametrize(
    "bad_date",
    ["15-01-2020", "01/15/2020", "March 2020", "2020-13-01", "2020-02-30", "20200101"],
)
def test_invalid_date_format_raises(bad_date):
    with pytest.raises(ValueError, match="is not a valid date"):
        calculate_yoe.run(date_ranges=[(bad_date, "2021-01-01")])


def test_future_date_raises():
    future = (date.today() + timedelta(days=30)).isoformat()
    with pytest.raises(ValueError, match="is in the future"):
        calculate_yoe.run(date_ranges=[("2020-01-01", future)])
    with pytest.raises(ValueError, match="is in the future"):
        calculate_yoe.run(date_ranges=[(future, None)])


@pytest.mark.parametrize("implausible", ["0219-01-01", "1899-12-31", "0001"])
def test_implausibly_old_date_raises(implausible):
    """Parses as a date, but cannot be an employment date — a misread year."""
    with pytest.raises(ValueError, match="cannot be a real employment date"):
        calculate_yoe.run(date_ranges=[(implausible, "2020-01-01")])


def test_invalid_datatype_inside_pair_raises():
    with pytest.raises(TypeError, match="must be a date string in YYYY-MM-DD format"):
        calculate_yoe.run(date_ranges=[(20200101, "2021-01-01")])


def test_invalid_datatype_for_date_ranges_raises():
    # Through .func(), which skips CrewAI's schema validation, so the tool's own
    # message is what the agent would see.
    with pytest.raises(TypeError, match="date_ranges must be a list"):
        calculate_yoe.func(42)
    # Through .run(), CrewAI's own argument validation rejects it first.
    with pytest.raises(ValueError):
        calculate_yoe.run(date_ranges=42)


def test_entry_that_is_not_a_pair_raises():
    with pytest.raises(TypeError, match="Entry 1 of date_ranges must be a"):
        calculate_yoe.run(date_ranges=["2020-01-01"])


@pytest.mark.parametrize(
    "malformed",
    [
        [("2020-01-01",)],                                   # single value
        [("2020-01-01", "2021-01-01", "2022-01-01")],        # three values
        [()],                                                # empty pair
    ],
)
def test_malformed_tuple_raises(malformed):
    with pytest.raises(ValueError, match="must have EXACTLY 2"):
        calculate_yoe.run(date_ranges=malformed)


def test_multiple_null_end_dates_raise():
    with pytest.raises(ValueError, match="only ONE"):
        calculate_yoe.run(
            date_ranges=[("2019-01-01", None), ("2021-01-01", None)]
        )


@pytest.mark.parametrize("missing_start", [None, "", "   "])
def test_none_start_date_raises(missing_start):
    with pytest.raises(ValueError, match="start_date of entry 1 is missing"):
        calculate_yoe.run(date_ranges=[(missing_start, "2021-01-01")])


def test_end_date_before_start_date_raises():
    with pytest.raises(ValueError, match="ends before it starts"):
        calculate_yoe.run(date_ranges=[("2021-01-01", "2020-01-01")])


def test_error_message_identifies_the_offending_entry():
    """The agent needs to know WHICH pair to fix, not just that one is wrong."""
    with pytest.raises(ValueError, match="entry 2"):
        calculate_yoe.run(
            date_ranges=[("2019-01-01", "2020-01-01"), ("01/15/2021", None)]
        )
