"""Briefings (BUILD_SPEC 13, 10.2, 10.3): facts from the demo company, the writer (code and fake AI), the schedule."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pytest

from app.agent.tools import ToolContext
from app.briefings import facts as briefing_facts
from app.briefings import schedule, writer
from app.llm.base import LLMError, ToolCall
from app.llm.fake import FakeLLM, FakeTurn
from app.models import BriefingSetting
from tests import analytics_data as demo


def _facts(today: date, reported: set[str] | None = None) -> briefing_facts.Facts:
    ctx: ToolContext = demo.agent_context()
    ctx.today = today
    return briefing_facts.compute(ctx, ctx.access, ctx.visible, reported=reported or set())


@pytest.fixture(scope="module")
def outage() -> briefing_facts.Facts:
    """The morning after the planted App outage of 2026-08-15 (ANSWER_KEY.md, finding 5)."""
    return _facts(date(2026, 8, 16))


@pytest.fixture(scope="module")
def end_of_data() -> briefing_facts.Facts:
    """Long after the demo data ends (2026-09-30)."""
    return _facts(date(2026, 11, 13))


class TestFacts:
    def test_latest_day_is_yesterday_when_data_is_fresh(self, outage: briefing_facts.Facts) -> None:
        assert outage.as_of == date(2026, 8, 15)
        assert {k["metric"] for k in outage.kpis} == {"revenue", "orders", "aov"}
        assert {k["span"] for k in outage.kpis} == {"day", "week"}

    def test_the_outage_is_an_alert(self, outage: briefing_facts.Facts) -> None:
        alert = outage.alerts[0]
        assert alert["bucket"] == "2026-08-15" and alert["direction"] == "down" and alert["bad"]
        assert alert["change_pct"] < -40

    def test_growth_is_west_beauty(self, outage: briefing_facts.Facts) -> None:
        assert outage.growth[0]["segment"] == "West × Beauty"  # noqa: RUF001

    def test_old_data_is_said(self, end_of_data: briefing_facts.Facts) -> None:
        assert end_of_data.as_of == date(2026, 9, 30)
        assert {"code": "briefing_data_old", "detail": "2026-09-30"} in end_of_data.notes
        assert any(r["kind"] == "stale" for r in end_of_data.risks)

    def test_reported_findings_are_not_repeated(self, outage: briefing_facts.Facts) -> None:
        again = _facts(date(2026, 8, 16), reported=set(outage.fingerprints))
        assert not set(again.fingerprints) & set(outage.fingerprints)
        assert all(u["bucket"] != "2026-08-15" or u["series"] != "Total" for u in again.unusual)

    def test_every_fact_has_provenance(self, outage: briefing_facts.Facts) -> None:
        ids = {k["result_id"] for k in outage.kpis} | {u["result_id"] for u in outage.unusual}
        assert ids <= set(outage.store.provenance)

    def test_the_writer_sees_no_rows(self, outage: briefing_facts.Facts) -> None:
        compact = outage.compact()
        assert "rows" not in str(compact) and "email" not in str(compact)


class TestCodeWriter:
    def test_alert_leads_and_every_figure_is_checked(self, outage: briefing_facts.Facts) -> None:
        written = writer.write_with_code(outage)
        assert written.by == "code"
        assert written.headline.startswith("Revenue on 15 Aug")
        assert written.key_figure and written.key_figure in written.headline
        assert writer.check(written, outage) is not None
        assert written.sections["growth"][0].startswith("West × Beauty")  # noqa: RUF001
        assert written.sections["health"] == []  # something is notable

    def test_health_summary_when_nothing_is_notable(
        self, end_of_data: briefing_facts.Facts
    ) -> None:
        quiet = _facts(date(2026, 11, 13), reported=set(end_of_data.fingerprints))
        quiet.risks = []
        written = writer.write_with_code(quiet)
        assert written.sections["health"] and written.headline == written.sections["health"][0]


def _ai(script: list[Any]) -> FakeLLM:
    return FakeLLM(script)


class TestAiWriter:
    def test_a_good_answer_is_kept_and_cited(self, outage: briefing_facts.Facts) -> None:
        alert = outage.alerts[0]
        rid = alert["result_id"]
        client = _ai(
            [
                FakeTurn(
                    tool_calls=[
                        ToolCall(
                            "w1",
                            "write_briefing",
                            {
                                "subject": "App orders fell on 15 Aug",
                                "headline": f"Revenue fell {alert['change_pct']:.1f}% [{rid}] on 15 Aug.",
                                "key_figure": f"{alert['change_pct']:.1f}%",
                                "alerts": ["Check the app: revenue fell on 15 Aug."],
                                "unusual": ["Revenue was ₹9.9 Cr on 12 Aug."],  # not in any result
                            },
                        )
                    ]
                )
            ]
        )
        seen: list[Any] = []
        written = writer.write_with_ai(client, outage, on_response=seen.append)
        assert written is not None and written.by == "ai" and len(seen) == 1
        assert written.sections["unusual"] == []  # the invented figure was removed
        assert {"code": "numbers_removed", "detail": "1"} in written.notes
        # Only facts went to the model: no rows, one small call with the playbook.
        call = client.calls[0]
        assert "daily briefing" in call.system.lower() and "write_briefing" in [
            t.name for t in call.tools
        ]

    def test_an_invented_headline_is_refused(self, outage: briefing_facts.Facts) -> None:
        client = _ai(
            [
                FakeTurn(
                    tool_calls=[
                        ToolCall(
                            "w1",
                            "write_briefing",
                            {"subject": "x", "headline": "Sales hit ₹7.7 Cr."},
                        )
                    ]
                )
            ]
        )
        assert writer.write_with_ai(client, outage) is None

    def test_errors_and_plain_text_fall_back(self, outage: briefing_facts.Facts) -> None:
        assert (
            writer.write_with_ai(_ai([LLMError("quota", "gemini", "daily quota")]), outage) is None
        )
        assert writer.write_with_ai(_ai(["Here is your briefing"]), outage) is None

    def test_cache_key_changes_with_the_facts(
        self, outage: briefing_facts.Facts, end_of_data: briefing_facts.Facts
    ) -> None:
        assert writer.cache_key(outage, "en") != writer.cache_key(end_of_data, "en")
        assert writer.cache_key(outage, "en") == writer.cache_key(outage, "en")


IST = "Asia/Kolkata"


def _settings(**kw: Any) -> schedule.Effective:
    base: dict[str, Any] = {
        "enabled": True,
        "local_time": time(6, 0),
        "frequency": "daily",
        "weekday": 0,
        "language": "en",
        "timezone": IST,
        "default": False,
    }
    return schedule.Effective(**{**base, **kw})


class TestSchedule:
    def test_defaults_owner_on_others_off(self) -> None:
        assert schedule.effective(None, "owner", IST).enabled
        assert not schedule.effective(None, "analyst", IST).enabled
        assert not schedule.effective(None, "viewer", IST).enabled
        assert schedule.effective(None, "owner", IST).local_time == time(6, 0)

    def test_six_am_ist_is_half_past_midnight_utc(self) -> None:
        slot = schedule.slot_on(datetime(2026, 11, 13, 3, 0, tzinfo=UTC), _settings())
        assert slot == datetime(2026, 11, 13, 0, 30, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("now", "due"),
        [
            (datetime(2026, 11, 13, 0, 24, tzinfo=UTC), False),
            (datetime(2026, 11, 13, 0, 26, tzinfo=UTC), True),  # within 5 minutes before
            (datetime(2026, 11, 13, 0, 55, tzinfo=UTC), True),  # catching up
            (datetime(2026, 11, 13, 1, 1, tzinfo=UTC), False),
        ],
    )
    def test_due_window(self, now: datetime, due: bool) -> None:
        assert (schedule.due_slot(now, _settings()) is not None) is due

    def test_weekly_only_on_its_day(self) -> None:
        friday = datetime(2026, 11, 13, 0, 30, tzinfo=UTC)
        assert schedule.due_slot(friday, _settings(frequency="weekly", weekday=4)) is not None
        assert schedule.due_slot(friday, _settings(frequency="weekly", weekday=0)) is None
        assert schedule.next_slot(friday, _settings(frequency="weekly", weekday=0)) == datetime(
            2026, 11, 16, 0, 30, tzinfo=UTC
        )

    def test_off_never_runs(self) -> None:
        row = BriefingSetting(
            enabled=True, frequency="off", local_time=time(6), weekday=0, language="en"
        )
        effective = schedule.effective(row, "owner", IST)
        assert not effective.enabled
        assert schedule.next_slot(datetime(2026, 11, 13, tzinfo=UTC), effective) is None

    def test_own_time_zone_wins(self) -> None:
        row = BriefingSetting(
            enabled=True,
            frequency="daily",
            local_time=time(7, 30),
            weekday=0,
            language="en",
            timezone="Europe/London",
        )
        effective = schedule.effective(row, "analyst", IST)
        assert schedule.slot_on(datetime(2026, 11, 13, 12, tzinfo=UTC), effective) == datetime(
            2026, 11, 13, 7, 30, tzinfo=UTC
        )


class TestEachPointOnce:
    """M19: "no data after 30 Sep" was said again under "Upcoming risks"."""

    def test_the_stale_quality_warning_does_not_repeat_the_alert(self) -> None:
        ctx: ToolContext = demo.agent_context()
        ctx.today = date(2026, 11, 13)
        key = next(iter(ctx.dictionary.datasets))
        stale = {
            "kind": "stale",
            "severity": "warning",
            "message": "The newest row is from 30 Sep 2026, 44 days ago. Recent activity may be missing.",
            "details": {"newest": "2026-09-30", "age_days": 44},
        }
        nulls = {"kind": "nulls", "severity": "warning", "message": "12% of city is empty."}
        ctx.quality = {key: [stale, nulls], "other_table": [nulls, nulls]}
        facts = briefing_facts.compute(ctx, ctx.access, ctx.visible, reported=set())
        assert [r["kind"] for r in facts.alerts if r.get("kind") == "stale"] == ["stale"]
        quality = [r for r in facts.risks if r["kind"] == "quality"]
        assert [r["issue"] for r in quality] == [
            "nulls",
            "nulls",
        ]  # once per table, no stale repeat
        written = writer.write_with_code(facts)
        assert written.sections["alerts"] == [
            f"{facts.alerts[0]['table']} has no data after 30 Sep."
        ]
        assert not any("30 Sep" in item for item in written.sections["risks"])

    def test_repeats_from_the_ai_are_removed(self, end_of_data: briefing_facts.Facts) -> None:
        sections = {
            "alerts": ["shopify_orders has no data after 30 Sep [r1]."],
            "unusual": ["Shopify_orders has no data after 30 Sep."],
            "risks": [
                "Orders stopped on 30 Sep 2026; recent sales may be missing.",
                "Payments: 3% of rows have no amount.",
                "Payments: 3% of rows have no amount.",
            ],
            "health": [],
        }
        out = writer.without_repeats(sections, end_of_data)
        assert out["alerts"] == sections["alerts"]
        assert out["unusual"] == []
        assert out["risks"] == ["Payments: 3% of rows have no amount."]

    def test_without_a_stale_alert_risks_about_dates_stay(
        self, outage: briefing_facts.Facts
    ) -> None:
        sections = {"risks": ["Stock of Beauty runs out by 30 Sep."]}
        assert writer.without_repeats(sections, outage)["risks"] == sections["risks"]
