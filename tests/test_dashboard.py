from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from dashboard import app


class _FakeStreamlit:
    def __init__(self) -> None:
        self.headers: list[str] = []
        self.subheaders: list[str] = []
        self.markdowns: list[str] = []
        self.captions: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.frames: list[pd.DataFrame] = []
        self.charts = 0
        self.figures = []
        self.metrics: list[tuple[str, str]] = []

    def header(self, value: str) -> None:
        self.headers.append(value)

    def subheader(self, value: str) -> None:
        self.subheaders.append(value)

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def markdown(self, value: str) -> None:
        self.markdowns.append(value)

    def dataframe(self, frame: pd.DataFrame, *args, **kwargs) -> None:
        self.frames.append(frame)

    def info(self, value: str) -> None:
        self.infos.append(value)

    def warning(self, value: str) -> None:
        self.warnings.append(value)

    def plotly_chart(self, *args, **kwargs) -> None:
        self.charts += 1
        if args:
            self.figures.append(args[0])

    def metric(self, label: str, value: str) -> None:
        self.metrics.append((label, value))

    def columns(self, count: int):
        return [self for _ in range(count)]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        pass


def test_table_column_config_uses_localized_format_for_numeric_columns() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def number_column(**kwargs):
        calls.append(("number", kwargs))
        return kwargs

    st = SimpleNamespace(column_config=SimpleNamespace(NumberColumn=number_column))
    frame = pd.DataFrame({"rent_amount": [15000.0], "area_sqft": [1250.5], "currency_type": ["MVR"]})

    config = app._table_column_config(st, frame, {"source_url": "link"})

    assert config["source_url"] == "link"
    assert set(config) == {"rent_amount", "area_sqft", "source_url"}
    assert calls == [("number", {"format": "localized"}), ("number", {"format": "localized"})]


def test_dashboard_import_and_dataset_loading_do_not_scrape(monkeypatch, tmp_path: Path) -> None:
    frame = pd.DataFrame(
        [
            {
                "listing_type": "RESIDENTIAL",
                "room_count": 1,
                "maid_room_count": pd.NA,
                "rent_amount": 15000.0,
                "currency_type": "MVR",
                "rent_frequency": "MONTHLY",
                "area_sqft": 500.0,
                "location_zone": "HULHUMALE",
                "address": "Example",
                "last_updated": "2026-06-23",
                "status": "AVAILABLE",
                "source_url": "https://ibay.com.mv/example-o123.html",
                "source_name": "ibay",
                "scraped_at": "2026-07-01T00:00:00Z",
            }
        ],
        columns=app.CANONICAL_COLUMNS,
    )
    calls = []

    def fake_read_parquet(path):
        calls.append(path)
        return frame

    monkeypatch.setattr(app.pd, "read_parquet", fake_read_parquet)
    dataset_path = tmp_path / "ibay_rentals_master.parquet"
    dataset_path.touch()
    loaded = app.load_dataset(dataset_path)
    assert list(loaded.columns) == app.CANONICAL_COLUMNS
    assert len(calls) == 1


def test_missing_dashboard_dataset_message_points_to_pipeline(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.csv"

    try:
        app.load_dataset(missing_path)
    except FileNotFoundError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected FileNotFoundError")

    assert "py -m ibay_rentals pipeline" in message
    assert "scrape and preprocess all sources" in message
    assert "--max-listings 25" not in message
    assert str(missing_path) in message


def test_source_coverage_preserves_source_mix_without_trimming_prices() -> None:
    frame = pd.DataFrame(
        {
            "source_name": ["ibay", "ibay", "property_mv", "property_mv"],
            "rent_amount": [10_000.0, 11_000.0, 12_000.0, 5_000_000.0],
        }
    )

    coverage = app._source_coverage(frame)

    assert coverage.to_dict("records") == [
        {"source_name": "TOTAL", "listing_count": 4, "share_of_filtered_listings_pct": 100.0},
        {"source_name": "ibay", "listing_count": 2, "share_of_filtered_listings_pct": 50.0},
        {"source_name": "property_mv", "listing_count": 2, "share_of_filtered_listings_pct": 50.0},
    ]


def test_dashboard_percentile_cap_is_source_and_type_aware() -> None:
    frame = pd.DataFrame(
        {
            "source_name": ["ibay", "ibay", "property_mv", "property_mv"],
            "listing_type": ["RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL"],
            "rent_amount": [10_000.0, 11_000.0, 12_000.0, 5_000_000.0],
            "currency_type": ["MVR", "MVR", "MVR", "MVR"],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY", "MONTHLY"],
        }
    )

    capped = app._apply_rent_percentile_cap(frame, 0.90)

    assert 5_000_000.0 not in capped["rent_amount"].tolist()
    assert {10_000.0, 12_000.0}.issubset(set(capped["rent_amount"].tolist()))


def test_rent_statistics_exclude_zero_and_negative_rents() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL"],
            "rent_amount": [0.0, -100.0, 12000.0],
            "currency_type": ["MVR", "MVR", "MVR"],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY"],
            "location_zone": ["MALE", "MALE", "MALE"],
            "room_count": [1, 1, 2],
            "area_sqft": [100.0, 100.0, 600.0],
        }
    )

    stats = app._group_stats(frame)
    monthly = app._monthly_type_location_summary(frame, "MVR")
    per_room = app._rent_per_room_summary(frame)
    per_sqft = app._rent_per_sqft_summary(frame)

    assert stats[["count", "mean", "median", "min", "max"]].to_dict("records") == [
        {"count": 1, "mean": 12000.0, "median": 12000.0, "min": 12000.0, "max": 12000.0}
    ]
    assert monthly[["count", "median", "mean"]].to_dict("records") == [
        {"count": 1, "median": 12000.0, "mean": 12000.0}
    ]
    assert per_room[["count", "mean"]].to_dict("records") == [{"count": 1, "mean": 6000.0}]
    assert per_sqft[["count", "median", "mean"]].to_dict("records") == [
        {"count": 1, "median": 20.0, "mean": 20.0}
    ]


def test_dashboard_filters_are_positive_allowlists_with_empty_meaning_all() -> None:
    frame = pd.DataFrame(
        {
            "status": ["AVAILABLE", "RENTED"],
            "listing_type": ["RESIDENTIAL", "COMMERCIAL"],
            "use_case": [pd.NA, "Office Space"],
            "currency_type": ["MVR", "USD"],
            "rent_frequency": ["MONTHLY", "DAILY"],
            "location_zone": ["MALE", "HULHUMALE"],
            "room_count": [1, 2],
            "source_name": ["ibay", "ibay"],
            "last_updated": ["2026-06-01", "2026-07-01"],
        }
    )

    unfiltered = app.apply_filters(
        frame,
        status=[],
        listing_type=[],
        use_case=[],
        currency=[],
        frequency=[],
        location=[],
        rooms=[],
        source_name=[],
    )
    filtered = app.apply_filters(
        frame,
        status=["AVAILABLE"],
        listing_type=[],
        use_case=[],
        currency=["MVR"],
        frequency=[],
        location=[],
        rooms=[],
        source_name=[],
    )

    assert len(unfiltered) == 2
    assert filtered["currency_type"].tolist() == ["MVR"]


def test_dashboard_filters_by_use_case() -> None:
    frame = pd.DataFrame(
        {
            "status": ["AVAILABLE", "AVAILABLE"],
            "listing_type": ["COMMERCIAL", "COMMERCIAL"],
            "use_case": ["Office Space", "Warehouse"],
            "currency_type": ["MVR", "MVR"],
            "rent_frequency": ["MONTHLY", "MONTHLY"],
            "location_zone": ["MALE", "MALE"],
            "room_count": [pd.NA, pd.NA],
            "source_name": ["property_mv", "property_mv"],
        }
    )

    filtered = app.apply_filters(
        frame,
        status=[],
        listing_type=[],
        use_case=["Warehouse"],
        currency=[],
        frequency=[],
        location=[],
        rooms=[],
        source_name=[],
    )

    assert filtered["use_case"].tolist() == ["Warehouse"]


def test_visual_category_frame_drops_nullable_missing_values() -> None:
    frame = pd.DataFrame(
        {
            "location_zone": ["MALE", pd.NA],
            "currency_type": ["MVR", pd.NA],
            "rent_amount": [10000.0, 12000.0],
        }
    )

    cleaned = app._visual_category_frame(frame, ["location_zone", "currency_type"])

    assert cleaned["location_zone"].tolist() == ["MALE"]
    assert cleaned["currency_type"].tolist() == ["MVR"]
    assert cleaned["location_zone"].map(type).eq(str).all()


def test_overview_metric_rows_include_blank_buckets() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "COMMERCIAL", pd.NA],
            "rent_amount": [12000.0, 15000.0, 18000.0],
            "currency_type": ["MVR", "USD", pd.NA],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY"],
            "location_zone": ["MALE", "HULHUMALE", pd.NA],
            "room_count": [1, 2, 3],
            "area_sqft": [500.0, 600.0, 700.0],
            "scraped_at": ["2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_overview(fake_st, frame)

    assert ("Total listings", "3") in fake_st.metrics
    assert ("Residential", "1") in fake_st.metrics
    assert ("Commercial", "1") in fake_st.metrics
    assert ("Unknown or blank type", "1") in fake_st.metrics
    assert ("Blank location", "1") in fake_st.metrics
    assert ("Blank currency", "1") in fake_st.metrics
    assert "Residential Listings Overview" in fake_st.subheaders
    assert "Commercial Listings Overview" in fake_st.subheaders
    assert ("Residential MVR", "1") in fake_st.metrics
    assert ("Residential USD", "0") in fake_st.metrics
    assert ("Commercial MVR", "0") in fake_st.metrics
    assert ("Commercial USD", "1") in fake_st.metrics
    assert "Residential listings by currency" not in fake_st.subheaders
    assert "Commercial listings by currency" not in fake_st.subheaders
    assert any("tally to total listings" in caption for caption in fake_st.captions)


def test_overview_and_rent_explorer_summary_tables_match_for_same_rows() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "COMMERCIAL", pd.NA],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY"],
            "rent_amount": [10000.0, 20000.0, 30000.0],
            "currency_type": ["MVR", "MVR", "MVR"],
            "location_zone": ["MALE", "HULHUMALE", pd.NA],
            "room_count": [1, pd.NA, pd.NA],
            "area_sqft": [500.0, 1000.0, 1000.0],
            "address": ["A", "B", "C"],
            "source_name": ["ibay", "property_mv", "property_mv"],
            "scraped_at": ["2026-07-01T00:00:00Z"] * 3,
        }
    )
    overview_st = _FakeStreamlit()
    rent_st = _FakeStreamlit()

    app.render_overview(overview_st, frame)
    app.render_rent_explorer(rent_st, frame)

    pd.testing.assert_frame_equal(overview_st.frames[1].reset_index(drop=True), rent_st.frames[0].reset_index(drop=True))


def test_finished_bar_keeps_value_labels_visible() -> None:
    frame = pd.DataFrame(
        {
            "room_count": [1, 2],
            "mean": [10000.0, 25000.0],
            "location_zone": ["MALE", "HULHUMALE"],
            "bar_value": ["10,000", "25,000"],
        }
    )
    fig = app.px.bar(
        frame,
        x="room_count",
        y="mean",
        color="location_zone",
        text="bar_value",
        barmode="group",
    )

    finished = app._finish_bar(fig)

    assert all(trace.textposition == "outside" for trace in finished.data)
    assert all(trace.cliponaxis is False for trace in finished.data)
    assert finished.layout.uniformtext.mode == "show"
    assert finished.layout.yaxis.rangemode == "tozero"


def test_location_bar_uses_stable_male_hulhumale_order() -> None:
    summary = pd.DataFrame(
        {
            "location_zone": ["HULHUMALE"],
            "count": [3],
            "mean": [20000.0],
        }
    )
    fake_st = _FakeStreamlit()

    app._location_rate_bar(
        fake_st,
        summary,
        value_column="mean",
        title="Mean rent by location",
        y_label="Mean rent",
    )

    assert fake_st.figures[0].layout.xaxis.categoryarray == tuple(app.LOCATION_ORDER)
    assert [trace.name for trace in fake_st.figures[0].data] == ["Male'", "Hulhumale'", "Others"]
    assert [trace.legendgroup for trace in fake_st.figures[0].data] == ["Male'", "Hulhumale'", "Others"]
    plotted_locations = [location for trace in fake_st.figures[0].data for location in trace.x]
    assert plotted_locations == app.LOCATION_ORDER
    plotted_values = {location: value for trace in fake_st.figures[0].data for location, value in zip(trace.x, trace.y)}
    assert plotted_values == {"MALE": 0, "HULHUMALE": 20000.0, "OTHERS": 0}


def test_complete_location_summary_does_not_copy_aggregate_values_to_missing_locations() -> None:
    summary = pd.DataFrame(
        {
            "currency_type": ["USD"],
            "rent_frequency": ["MONTHLY"],
            "location_zone": ["MALE"],
            "count": [2],
            "mean": [2.68],
            "median": [2.68],
        }
    )

    completed = app._complete_location_summary(summary, ["mean"])

    completed_by_location = completed.set_index("location_zone")
    assert completed_by_location.loc["MALE", "mean"] == 2.68
    assert completed_by_location.loc["HULHUMALE", "count"] == 0
    assert completed_by_location.loc["HULHUMALE", "mean"] == 0
    assert completed_by_location.loc["HULHUMALE", "median"] == 0
    assert completed_by_location.loc["OTHERS", "count"] == 0
    assert completed_by_location.loc["OTHERS", "mean"] == 0
    assert completed_by_location.loc["OTHERS", "median"] == 0


def test_residential_explorer_renders_daily_residential_sections() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "RESIDENTIAL", "COMMERCIAL"],
            "rent_frequency": ["DAILY", "DAILY", "MONTHLY"],
            "rent_amount": [500.0, 750.0, 20000.0],
            "currency_type": ["MVR", "USD", "MVR"],
            "location_zone": ["MALE", "HULHUMALE", "MALE"],
            "room_count": [1, 2, pd.NA],
            "maid_room_count": [pd.NA, pd.NA, pd.NA],
            "area_sqft": [250.0, 300.0, 1200.0],
            "address": ["A", "B", "C"],
            "source_name": ["ibay", "ibay", "ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_residential_explorer(fake_st, frame)

    assert "Daily Residential Rent" in fake_st.headers
    assert "Daily MVR residential rent" in fake_st.subheaders
    assert ("Total listings", "2") in fake_st.metrics
    assert ("Male'", "1") in fake_st.metrics
    assert ("Hulhumale'", "1") in fake_st.metrics
    assert ("Other", "0") in fake_st.metrics
    assert ("MVR", "1") in fake_st.metrics
    assert ("USD", "1") in fake_st.metrics
    assert any("tally to total listings" in caption for caption in fake_st.captions)
    assert fake_st.charts >= 3


def test_residential_daily_mvr_sqft_chart_includes_missing_male_location() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL"],
            "rent_frequency": ["DAILY"],
            "rent_amount": [750.0],
            "currency_type": ["MVR"],
            "location_zone": ["HULHUMALE"],
            "room_count": [2],
            "maid_room_count": [pd.NA],
            "area_sqft": [300.0],
            "address": ["B"],
            "source_name": ["ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_residential_explorer(fake_st, frame)

    sqft_figures = [
        figure
        for figure in fake_st.figures
        if figure.layout.title.text == "Median daily residential rent per sqft area (MVR) by location"
    ]
    assert sqft_figures
    plotted_locations = [location for trace in sqft_figures[0].data for location in trace.x]
    assert plotted_locations == app.LOCATION_ORDER


def test_daily_mvr_residential_room_count_charts_are_hidden() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL"],
            "rent_frequency": ["DAILY", "DAILY", "DAILY"],
            "rent_amount": [500.0, 500.0, 500.0],
            "currency_type": ["MVR", "MVR", "MVR"],
            "location_zone": ["MALE", "HULHUMALE", "OTHERS"],
            "room_count": [1, 3, 4],
            "maid_room_count": [pd.NA, pd.NA, pd.NA],
            "area_sqft": [250.0, 700.0, 900.0],
            "address": ["A", "B", "C"],
            "source_name": ["ibay", "ibay", "ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_residential_explorer(fake_st, frame)

    titles = {figure.layout.title.text for figure in fake_st.figures}
    assert "Daily MVR residential rent distribution by location" in titles
    assert "Daily MVR residential rent distribution by room count and location" not in titles
    assert "Median daily MVR residential rent by room count and location" not in titles
    assert "Daily MVR residential rent by room count 1 and location" not in fake_st.subheaders
    assert any("Daily MVR residential room-count charts are hidden" in caption for caption in fake_st.captions)


def test_residential_room_count_boxplot_appears_before_mean_room_count_bar() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL"],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY", "MONTHLY", "MONTHLY"],
            "rent_amount": [12000.0, 18000.0, 30000.0, 35000.0, 50000.0],
            "currency_type": ["MVR", "MVR", "MVR", "MVR", "MVR"],
            "location_zone": ["MALE", "HULHUMALE", "MALE", "HULHUMALE", "HULHUMALE"],
            "room_count": [1, 2, 3, 4, 5],
            "maid_room_count": [pd.NA, pd.NA, pd.NA, pd.NA, pd.NA],
            "area_sqft": [500.0, 700.0, 1000.0, 1200.0, 1800.0],
            "address": ["A", "B", "C", "D", "E"],
            "source_name": ["ibay", "ibay", "ibay", "ibay", "ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_residential_explorer(fake_st, frame)

    titles = [figure.layout.title.text for figure in fake_st.figures]
    lower_box_title = "Monthly MVR residential rent distribution by room count 1-2 and location"
    middle_box_title = "Monthly MVR residential rent distribution by room count 3-4 and location"
    higher_box_title = "Monthly MVR residential rent distribution by room count 5+ and location"
    median_title = "Median monthly MVR residential rent by room count and location"
    assert lower_box_title in titles
    assert middle_box_title in titles
    assert higher_box_title in titles
    assert median_title in titles
    assert titles.index(lower_box_title) < titles.index(middle_box_title) < titles.index(higher_box_title) < titles.index(median_title)
    lower_box = fake_st.figures[titles.index(lower_box_title)]
    middle_box = fake_st.figures[titles.index(middle_box_title)]
    higher_box = fake_st.figures[titles.index(higher_box_title)]
    assert {"Min: 12,000", "Median: 12,000", "Max: 12,000"}.issubset(
        {annotation.text for annotation in lower_box.layout.annotations}
    )
    assert {"Min: 30,000", "Median: 30,000", "Max: 30,000"}.issubset(
        {annotation.text for annotation in middle_box.layout.annotations}
    )
    assert {"Min: 50,000", "Median: 50,000", "Max: 50,000"}.issubset(
        {annotation.text for annotation in higher_box.layout.annotations}
    )
    for figure in (lower_box, middle_box, higher_box):
        assert all(annotation.xshift == 0 for annotation in figure.layout.annotations)
        assert all(annotation.xanchor == "center" for annotation in figure.layout.annotations)
        assert all(not isinstance(annotation.x, str) for annotation in figure.layout.annotations)
    lower_min = next(annotation for annotation in lower_box.layout.annotations if annotation.text == "Min: 12,000")
    middle_min = next(annotation for annotation in middle_box.layout.annotations if annotation.text == "Min: 30,000")
    assert round(lower_min.x, 2) == 0.88
    assert round(middle_min.x, 2) == 0.88


def test_monthly_usd_residential_room_count_boxplot_is_split_like_mvr() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL"],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY", "MONTHLY", "MONTHLY"],
            "rent_amount": [1000.0, 1200.0, 1800.0, 2200.0, 3500.0],
            "currency_type": ["USD", "USD", "USD", "USD", "USD"],
            "location_zone": ["MALE", "HULHUMALE", "MALE", "HULHUMALE", "HULHUMALE"],
            "room_count": [1, 2, 3, 4, 5],
            "maid_room_count": [pd.NA, pd.NA, pd.NA, pd.NA, pd.NA],
            "area_sqft": [500.0, 700.0, 1000.0, 1200.0, 1800.0],
            "address": ["A", "B", "C", "D", "E"],
            "source_name": ["ibay", "ibay", "ibay", "ibay", "ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_residential_explorer(fake_st, frame)

    titles = [figure.layout.title.text for figure in fake_st.figures]
    lower_box_title = "Monthly USD residential rent distribution by room count 1-2 and location"
    middle_box_title = "Monthly USD residential rent distribution by room count 3-4 and location"
    higher_box_title = "Monthly USD residential rent distribution by room count 5+ and location"
    median_title = "Median monthly USD residential rent by room count and location"
    assert lower_box_title in titles
    assert middle_box_title in titles
    assert higher_box_title in titles
    assert titles.index(lower_box_title) < titles.index(middle_box_title) < titles.index(higher_box_title) < titles.index(median_title)


def test_residential_room_count_boxplot_values_can_be_hidden() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "RESIDENTIAL", "RESIDENTIAL"],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY"],
            "rent_amount": [12000.0, 18000.0, 30000.0],
            "currency_type": ["MVR", "MVR", "MVR"],
            "location_zone": ["MALE", "HULHUMALE", "MALE"],
            "room_count": [1, 2, 3],
            "maid_room_count": [pd.NA, pd.NA, pd.NA],
            "area_sqft": [500.0, 700.0, 1000.0],
            "address": ["A", "B", "C"],
            "source_name": ["ibay", "ibay", "ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_residential_explorer(fake_st, frame, show_box_values=False)

    box_titles = {
        "Monthly MVR residential rent distribution by location",
        "Monthly MVR residential rent distribution by room count 1-2 and location",
        "Monthly MVR residential rent distribution by room count 3-4 and location",
    }
    box_figures = [figure for figure in fake_st.figures if figure.layout.title.text in box_titles]
    assert box_figures
    assert all(len(figure.layout.annotations) == 0 for figure in box_figures)


def test_monthly_type_location_summary_keeps_residential_commercial_locations_only() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "COMMERCIAL", "UNKNOWN", "RESIDENTIAL"],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY", "MONTHLY"],
            "currency_type": ["MVR", "MVR", "MVR", "MVR"],
            "rent_amount": [10000.0, 20000.0, 999.0, 5000.0],
            "location_zone": ["MALE", "HULHUMALE", "MALE", pd.NA],
        }
    )

    summary = app._monthly_type_location_summary(frame, "MVR")

    assert summary[["listing_type", "location_zone", "median"]].to_dict("records") == [
        {"listing_type": "COMMERCIAL", "location_zone": "HULHUMALE", "median": 20000.0},
        {"listing_type": "RESIDENTIAL", "location_zone": "MALE", "median": 10000.0},
    ]


def test_rent_explorer_adds_monthly_type_location_bars_and_skips_unknown_graphs() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["RESIDENTIAL", "COMMERCIAL", "UNKNOWN"],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY"],
            "rent_amount": [10000.0, 20000.0, 30000.0],
            "currency_type": ["MVR", "MVR", "MVR"],
            "location_zone": ["MALE", "HULHUMALE", "MALE"],
            "room_count": [1, pd.NA, pd.NA],
            "area_sqft": [500.0, 1000.0, 1000.0],
            "address": ["A", "B", "C"],
            "source_name": ["ibay", "ibay", "ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_rent_explorer(fake_st, frame)

    assert "Monthly rent by location and listing type" in fake_st.subheaders
    assert "**MVR**" in fake_st.markdowns
    assert "Unknown rent distributions" not in fake_st.subheaders
    assert "Residential rent distributions" in fake_st.subheaders
    assert "Commercial rent distributions" in fake_st.subheaders
    assert "Commercial rent per sqft area by location" not in fake_st.subheaders


def test_commercial_explorer_renders_rent_per_sqft_section() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["COMMERCIAL", "COMMERCIAL", "RESIDENTIAL"],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY"],
            "rent_amount": [20000.0, 30000.0, 12000.0],
            "currency_type": ["MVR", "USD", "USD"],
            "location_zone": ["MALE", "HULHUMALE", "MALE"],
            "room_count": [pd.NA, pd.NA, 2],
            "area_sqft": [1000.0, 1500.0, 700.0],
            "address": ["A", "B", "C"],
            "source_name": ["ibay", "ibay", "ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_commercial_explorer(fake_st, frame)

    assert "Commercial Explorer" in fake_st.headers
    assert "Commercial rent per sqft area by location" in fake_st.subheaders
    assert "**MVR - Monthly**" in fake_st.markdowns
    assert "**USD - Monthly**" in fake_st.markdowns
    assert ("Total listings", "2") in fake_st.metrics
    assert ("Male'", "1") in fake_st.metrics
    assert ("Hulhumale'", "1") in fake_st.metrics
    assert ("Other", "0") in fake_st.metrics
    assert ("MVR", "1") in fake_st.metrics
    assert ("USD", "1") in fake_st.metrics
    assert any("tally to total listings" in caption for caption in fake_st.captions)
    assert any(figure.layout.title.text == "Median monthly MVR commercial rent by location" for figure in fake_st.figures)
    assert fake_st.charts == 3


def test_commercial_explorer_renders_monthly_use_case_rent_and_sqft_bars() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["COMMERCIAL", "COMMERCIAL", "COMMERCIAL", "RESIDENTIAL"],
            "use_case": ["Office Space", "Warehouse", pd.NA, pd.NA],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY", "MONTHLY"],
            "rent_amount": [20000.0, 3000.0, 10000.0, 12000.0],
            "currency_type": ["MVR", "USD", "MVR", "USD"],
            "location_zone": ["MALE", "HULHUMALE", "MALE", "MALE"],
            "room_count": [pd.NA, pd.NA, pd.NA, 2],
            "area_sqft": [1000.0, 1500.0, 900.0, 700.0],
            "address": ["A", "B", "C", "D"],
            "source_name": ["ibay", "property_mv", "ibay", "ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_commercial_explorer(fake_st, frame)

    assert "Commercial Use Case Counts" in fake_st.subheaders
    assert ("Office Space", "1") in fake_st.metrics
    assert ("Warehouse", "1") in fake_st.metrics
    assert ("Blank use case", "1") in fake_st.metrics
    assert any("use-case counts tally to total commercial listings" in caption for caption in fake_st.captions)
    assert "Commercial sqft area by use case" in fake_st.subheaders
    assert "Monthly commercial rent by use case" in fake_st.subheaders
    assert "Monthly commercial rent per sqft area by use case" in fake_st.subheaders
    titles = {figure.layout.title.text for figure in fake_st.figures}
    assert "Commercial sqft area distribution by use case" in titles
    assert "Median monthly MVR commercial rent by use case" in titles
    assert "Median monthly USD commercial rent by use case" in titles
    assert "Median monthly MVR commercial rent per sqft area by use case" in titles
    assert "Median monthly USD commercial rent per sqft area by use case" in titles
    sqft_box = next(figure for figure in fake_st.figures if figure.layout.title.text == "Commercial sqft area distribution by use case")
    assert all(trace.boxmean is True for trace in sqft_box.data)
    assert {"Min: 1,000", "Median: 1,000", "Max: 1,000"}.issubset(
        {annotation.text for annotation in sqft_box.layout.annotations}
    )
    assert {trace.name: trace.marker.color for trace in sqft_box.data} == {
        "Office Space": app.USE_CASE_COLOR_MAP["Office Space"],
        "Warehouse": app.USE_CASE_COLOR_MAP["Warehouse"],
    }
    mvr_rent = next(figure for figure in fake_st.figures if figure.layout.title.text == "Median monthly MVR commercial rent by use case")
    assert {trace.name: trace.marker.color for trace in mvr_rent.data} == {
        "Office Space": app.USE_CASE_COLOR_MAP["Office Space"]
    }
    mvr_sqft = next(figure for figure in fake_st.figures if figure.layout.title.text == "Median monthly MVR commercial rent per sqft area by use case")
    plotted_values = {
        use_case: value
        for trace in mvr_sqft.data
        for use_case, value in zip(trace.x, trace.y)
    }
    assert plotted_values["Office Space"] == 20.0


def test_commercial_sqft_area_boxplot_values_can_be_hidden() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["COMMERCIAL", "COMMERCIAL"],
            "use_case": ["Office Space", "Warehouse"],
            "rent_frequency": ["MONTHLY", "MONTHLY"],
            "rent_amount": [20000.0, 3000.0],
            "currency_type": ["MVR", "USD"],
            "location_zone": ["MALE", "HULHUMALE"],
            "room_count": [pd.NA, pd.NA],
            "area_sqft": [1000.0, 1500.0],
            "address": ["A", "B"],
            "source_name": ["ibay", "property_mv"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_commercial_explorer(fake_st, frame, show_box_values=False)

    sqft_box = next(
        figure
        for figure in fake_st.figures
        if figure.layout.title.text == "Commercial sqft area distribution by use case"
    )
    assert len(sqft_box.layout.annotations) == 0


def test_commercial_usd_sqft_chart_zeroes_missing_locations() -> None:
    frame = pd.DataFrame(
        {
            "listing_type": ["COMMERCIAL"],
            "rent_frequency": ["MONTHLY"],
            "rent_amount": [2000.0],
            "currency_type": ["USD"],
            "location_zone": ["MALE"],
            "room_count": [pd.NA],
            "area_sqft": [1000.0],
            "address": ["A"],
            "source_name": ["ibay"],
        }
    )
    fake_st = _FakeStreamlit()

    app.render_commercial_explorer(fake_st, frame)

    sqft_figures = [
        figure
        for figure in fake_st.figures
        if figure.layout.title.text == "Median USD commercial rent per sqft area by location"
    ]
    assert sqft_figures
    plotted_values = {
        location: value
        for trace in sqft_figures[0].data
        for location, value in zip(trace.x, trace.y)
    }
    plotted_counts = {
        location: count
        for trace in sqft_figures[0].data
        for location, (count, *_display) in zip(trace.x, trace.customdata)
    }
    assert plotted_values == {"MALE": 2.0, "HULHUMALE": 0, "OTHERS": 0}
    assert plotted_counts == {"MALE": 1, "HULHUMALE": 0, "OTHERS": 0}


def test_listings_table_shows_source_count_overview() -> None:
    frame = pd.DataFrame(
        {
            "rent_amount": [10000.0, 20000.0, 30000.0],
            "currency_type": ["MVR", "MVR", "USD"],
            "rent_frequency": ["MONTHLY", "MONTHLY", "MONTHLY"],
            "listing_type": ["RESIDENTIAL", "COMMERCIAL", "COMMERCIAL"],
            "use_case": [pd.NA, "Office Space", "Warehouse"],
            "room_count": [1, pd.NA, pd.NA],
            "maid_room_count": [pd.NA, pd.NA, pd.NA],
            "area_sqft": [500.0, 1000.0, 2000.0],
            "location_zone": ["MALE", "MALE", "HULHUMALE"],
            "address": ["A", "B", "C"],
            "last_updated": ["2026-07-01", "2026-07-01", "2026-07-01"],
            "status": ["AVAILABLE", "AVAILABLE", "AVAILABLE"],
            "source_name": ["ibay", "property_mv", "property_mv"],
            "source_url": ["https://ibay.com.mv/a-o1.html", "https://www.property.mv/property/b", "https://www.property.mv/property/c"],
            "scraped_at": ["2026-07-01T00:00:00Z"] * 3,
        }
    )
    fake_st = _FakeStreamlit()

    app.render_listings_table(fake_st, frame)

    assert "Listings by source" in fake_st.subheaders
    assert fake_st.frames[0].to_dict("records") == [
        {"source_name": "TOTAL", "listing_count": 3},
        {"source_name": "property_mv", "listing_count": 2},
        {"source_name": "ibay", "listing_count": 1},
    ]
    assert fake_st.frames[1]["location_zone"].tolist() == ["Male'", "Male'", "Hulhumale'"]
