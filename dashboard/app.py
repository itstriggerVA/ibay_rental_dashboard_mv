"""Streamlit + Plotly dashboard for the processed rental dataset."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

try:
    from ibay_rentals.settings import PROCESSED_DIR
except ImportError:  # pragma: no cover - direct dashboard use before editable install.
    PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"


PROCESSED_DATA_DIR = PROCESSED_DIR
DATASET_CANDIDATES = [
    PROCESSED_DATA_DIR / "ibay_rentals_master.csv.gz",
    PROCESSED_DATA_DIR / "ibay_rentals_master.csv",
    PROCESSED_DATA_DIR / "ibay_rentals_master.parquet",
]
MISSING_DATASET_MESSAGE = (
    "Processed dashboard dataset not found. The dashboard only reads already-processed files. "
    "Run `py -m ibay_rentals pipeline` from the repository root to scrape and preprocess all sources, "
    "then restart the dashboard."
)
THEME_COLORS = ["#2563EB", "#F97316", "#7C3AED"]
LOCATION_ORDER = ["MALE", "HULHUMALE", "OTHERS"]
LOCATION_COLOR_MAP = {"MALE": THEME_COLORS[0], "HULHUMALE": THEME_COLORS[1], "OTHERS": THEME_COLORS[2]}
LOCATION_LABELS = {"MALE": "Male'", "HULHUMALE": "Hulhumale'", "OTHERS": "Others"}
USE_CASE_BLANK_LABEL = "Blank use case"
USE_CASE_ORDER = ["Commercial Space", "Land", "Office Space", "Restaurant", "Retail", "Warehouse"]
USE_CASE_COLOR_MAP = {
    "Commercial Space": "#E81416",
    "Land": "#4B369D",
    "Office Space": "#FFA500",
    "Restaurant": "#FAEB36",
    "Retail": "#79C314",
    "Warehouse": "#487DE7",
}
SUMMARY_VALUE_COLUMNS = {"mean", "median", "min", "max"}
ROOM_BLANK_FILTER = "<Blank>"
CANONICAL_COLUMNS = [
    "listing_type",
    "use_case",
    "room_count",
    "maid_room_count",
    "rent_amount",
    "currency_type",
    "rent_frequency",
    "area_sqft",
    "location_zone",
    "address",
    "last_updated",
    "status",
    "source_url",
    "source_name",
    "scraped_at",
]


def _default_dataset_path() -> Path:
    for candidate in DATASET_CANDIDATES:
        if candidate.exists():
            return candidate
    return DATASET_CANDIDATES[0]


def _dataset_missing_details(dataset_path: Path | None = None) -> str:
    checked = [dataset_path] if dataset_path is not None else DATASET_CANDIDATES
    checked_text = "\n".join(f"- `{path}`" for path in checked)
    return f"{MISSING_DATASET_MESSAGE}\n\nChecked:\n{checked_text}"


@st.cache_data(show_spinner=False)
def _read_dataset(dataset_path_text: str, modified_at_ns: int) -> pd.DataFrame:
    """Read one version of the processed dataset; the mtime key invalidates stale cache entries."""
    dataset_path = Path(dataset_path_text)
    if dataset_path.suffix == ".parquet":
        return pd.read_parquet(dataset_path)
    return pd.read_csv(dataset_path)


def load_dataset(dataset_path: Path | None = None) -> pd.DataFrame:
    """Load the processed dataset without starting a scraper."""
    explicit_dataset_path = dataset_path
    dataset_path = dataset_path or _default_dataset_path()
    if not dataset_path.exists():
        raise FileNotFoundError(_dataset_missing_details(explicit_dataset_path))
    frame = _read_dataset(str(dataset_path), dataset_path.stat().st_mtime_ns)
    if list(frame.columns) != CANONICAL_COLUMNS:
        raise ValueError("Processed dataset does not match the required canonical schema and column order")
    frame.attrs["dataset_path"] = str(dataset_path)
    frame.attrs["dataset_updated_at"] = pd.Timestamp(dataset_path.stat().st_mtime, unit="s").isoformat()
    return frame


def _available_values(frame: pd.DataFrame, column: str) -> list[str]:
    return sorted(frame[column].dropna().astype(str).unique().tolist())


def _valid_rent_mask(frame: pd.DataFrame) -> pd.Series:
    return frame["rent_amount"].notna() & (frame["rent_amount"] > 0)


def _priced_frame(frame: pd.DataFrame, extra_columns: Iterable[str] = ()) -> pd.DataFrame:
    columns = ["rent_amount", *extra_columns]
    return frame[_valid_rent_mask(frame)].dropna(subset=columns).copy()


def _invalid_rent_count(frame: pd.DataFrame) -> int:
    return int(frame["rent_amount"].notna().sum() - _valid_rent_mask(frame).sum())


def _warn_invalid_rents(st, frame: pd.DataFrame, rent_label: str = "rent") -> None:
    invalid_count = _invalid_rent_count(frame)
    if invalid_count:
        st.warning(
            f"{invalid_count:,} listing(s) with zero or negative {rent_label} are excluded from "
            f"{rent_label} statistics and {rent_label} visualizations."
        )


def _blank_count(frame: pd.DataFrame, column: str) -> int:
    values = frame[column].astype("string")
    empty_strings = values.notna() & values.str.strip().eq("")
    return int(values.isna().sum() + empty_strings.sum())


def _location_display(value: object) -> object:
    if pd.isna(value):
        return value
    return LOCATION_LABELS.get(str(value), value)


def _with_location_display(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "location_zone" in result.columns:
        result["location_display"] = result["location_zone"].map(_location_display)
    return result


def _metric_row(st, metrics: Iterable[tuple[str, int]]) -> None:
    values = list(metrics)
    columns = st.columns(len(values))
    for column, (label, value) in zip(columns, values, strict=True):
        column.metric(label, f"{int(value):,}")


def _render_filter_overview_metrics(st, frame: pd.DataFrame) -> None:
    st.subheader("Listing Type Counts")
    _metric_row(
        st,
        [
            ("Total listings", len(frame)),
            ("Residential", int((frame["listing_type"] == "RESIDENTIAL").sum())),
            ("Commercial", int((frame["listing_type"] == "COMMERCIAL").sum())),
            ("Unknown or blank type", int((~frame["listing_type"].isin(["RESIDENTIAL", "COMMERCIAL"])).sum())),
        ],
    )
    st.subheader("Location Counts")
    _metric_row(
        st,
        [
            ("Total listings", len(frame)),
            ("Male'", int((frame["location_zone"] == "MALE").sum())),
            ("Hulhumale'", int((frame["location_zone"] == "HULHUMALE").sum())),
            ("Other", int((frame["location_zone"] == "OTHERS").sum())),
            ("Blank location", _blank_count(frame, "location_zone")),
        ],
    )
    st.subheader("Currency Counts")
    _metric_row(
        st,
        [
            ("Total listings", len(frame)),
            ("MVR", int((frame["currency_type"] == "MVR").sum())),
            ("USD", int((frame["currency_type"] == "USD").sum())),
            ("Blank currency", _blank_count(frame, "currency_type")),
        ],
    )
    st.caption("Blank buckets are included where needed so displayed counts tally to total listings.")


def _render_listing_overview_metrics(st, frame: pd.DataFrame) -> None:
    st.subheader("Location Counts")
    _metric_row(
        st,
        [
            ("Total listings", len(frame)),
            ("Male'", int((frame["location_zone"] == "MALE").sum())),
            ("Hulhumale'", int((frame["location_zone"] == "HULHUMALE").sum())),
            ("Other", int((frame["location_zone"] == "OTHERS").sum())),
            ("Blank location", _blank_count(frame, "location_zone")),
        ],
    )
    st.subheader("Currency Counts")
    _metric_row(
        st,
        [
            ("Total listings", len(frame)),
            ("MVR", int((frame["currency_type"] == "MVR").sum())),
            ("USD", int((frame["currency_type"] == "USD").sum())),
            ("Blank currency", _blank_count(frame, "currency_type")),
        ],
    )
    st.caption(
        "Blank buckets are included where needed so displayed location and currency counts tally to total listings."
    )


def _render_commercial_use_case_metrics(st, frame: pd.DataFrame) -> None:
    st.subheader("Commercial Use Case Counts")
    total = len(frame)
    if "use_case" not in frame.columns:
        _metric_row(st, [("Total listings", total), (USE_CASE_BLANK_LABEL, total)])
        st.caption("Blank use cases are included so displayed use-case counts tally to total commercial listings.")
        return

    use_cases = frame["use_case"].astype("string").str.strip()
    nonblank = use_cases[use_cases.notna() & use_cases.ne("")]
    counts = nonblank.value_counts().to_dict()
    labels = list(USE_CASE_ORDER)
    labels.extend(label for label in sorted(counts) if label not in labels)
    metrics = [("Total listings", total)]
    metrics.extend((label, int(counts.get(label, 0))) for label in labels)
    metrics.append((USE_CASE_BLANK_LABEL, int(total - len(nonblank))))
    _metric_row(st, metrics)
    st.caption("Blank use cases are included so displayed use-case counts tally to total commercial listings.")


def _room_filter_options(frame: pd.DataFrame) -> list[int | str]:
    options: list[int | str] = sorted(frame["room_count"].dropna().astype(int).unique().tolist())
    if frame["room_count"].isna().any():
        options.append(ROOM_BLANK_FILTER)
    return options


def apply_filters(
    frame: pd.DataFrame,
    *,
    status: Iterable[str],
    listing_type: Iterable[str],
    use_case: Iterable[str],
    currency: Iterable[str],
    frequency: Iterable[str],
    location: Iterable[str],
    rooms: Iterable[int | str],
    source_name: Iterable[str],
) -> pd.DataFrame:
    """Apply positive global filters while preserving the original data types.

    Empty categorical selections mean "allow all" for that field. Non-empty
    selections act as an allowlist.
    """
    result = frame.copy()
    selections = {
        "status": list(status),
        "listing_type": list(listing_type),
        "use_case": list(use_case),
        "currency_type": list(currency),
        "rent_frequency": list(frequency),
        "location_zone": list(location),
        "source_name": list(source_name),
    }
    for column, selected in selections.items():
        if selected:
            result = result[result[column].isin(selected)]
    selected_rooms = list(rooms)
    if selected_rooms:
        include_blank_rooms = ROOM_BLANK_FILTER in selected_rooms
        selected_room_numbers = [
            int(room)
            for room in selected_rooms
            if room != ROOM_BLANK_FILTER and not pd.isna(room)
        ]
        room_mask = result["room_count"].isin(selected_room_numbers)
        if include_blank_rooms:
            room_mask = room_mask | result["room_count"].isna()
        result = result[room_mask]
    return result


def _money_label(currency: str | None) -> str:
    return currency or "Unspecified currency"


def _with_location_legend_labels(fig):
    for trace in fig.data:
        name = getattr(trace, "name", None)
        if name in LOCATION_LABELS:
            trace.name = LOCATION_LABELS[name]
        legendgroup = getattr(trace, "legendgroup", None)
        if legendgroup in LOCATION_LABELS:
            trace.legendgroup = LOCATION_LABELS[legendgroup]
    return fig


def _theme(fig):
    _with_location_legend_labels(fig)
    fig.update_layout(
        template="plotly_white",
        colorway=THEME_COLORS,
        margin={"l": 10, "r": 10, "t": 55, "b": 10},
        legend_title_text="",
    )
    return fig


def _with_location_order(fig):
    fig.update_xaxes(
        categoryorder="array",
        categoryarray=LOCATION_ORDER,
        tickmode="array",
        tickvals=LOCATION_ORDER,
        ticktext=[LOCATION_LABELS[location] for location in LOCATION_ORDER],
    )
    return fig


def _box_stat_label_frame(
    frame: pd.DataFrame,
    *,
    x_column: str,
    y_column: str,
    color_column: str | None = None,
) -> pd.DataFrame:
    group_columns = [x_column]
    if color_column and color_column not in group_columns:
        group_columns.append(color_column)
    stats = (
        frame.dropna(subset=[x_column, y_column])
        .groupby(group_columns, dropna=False)[y_column]
        .agg(minimum="min", median="median", maximum="max")
        .reset_index()
    )
    if stats.empty:
        return stats
    return stats.melt(
        id_vars=group_columns,
        value_vars=["minimum", "median", "maximum"],
        var_name="statistic",
        value_name="value",
    )


def _add_box_stat_labels(
    fig,
    frame: pd.DataFrame,
    *,
    x_column: str,
    y_column: str,
    color_column: str | None = None,
):
    labels = _box_stat_label_frame(frame, x_column=x_column, y_column=y_column, color_column=color_column)
    if labels.empty:
        return fig
    label_names = {"minimum": "Min", "median": "Median", "maximum": "Max"}
    y_shifts = {"minimum": -16, "median": 0, "maximum": 16}
    for row in labels.to_dict("records"):
        fig.add_annotation(
            x=row[x_column],
            y=row["value"],
            text=f"{label_names[row['statistic']]}: {_format_bar_value(row['value'])}",
            showarrow=False,
            xshift=0,
            yshift=y_shifts[row["statistic"]],
            xanchor="center",
            yanchor="middle",
            font={"size": 10, "color": "#111827"},
            bgcolor="rgba(255,255,255,0.78)",
            bordercolor="rgba(17,24,39,0.22)",
            borderpad=1,
            textangle=0,
            captureevents=False,
        )
    fig.update_traces(boxpoints="all")
    return fig


def _ordered_present_values(frame: pd.DataFrame, column: str, preferred_order: Iterable[str]) -> list[str]:
    present = set(frame[column].dropna().astype(str))
    values = [value for value in preferred_order if value in present]
    values.extend(value for value in sorted(present) if value not in values)
    return values


def _location_category_orders(extra: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    orders = {"location_zone": LOCATION_ORDER}
    if extra:
        orders.update(extra)
    return orders


def _use_case_category_orders(frame: pd.DataFrame | None = None) -> dict[str, list[str]]:
    values = list(USE_CASE_ORDER)
    if frame is not None and "use_case" in frame.columns:
        present = frame["use_case"].dropna().astype(str).unique().tolist()
        values.extend(value for value in sorted(present) if value not in values)
    return {"use_case": values}


def _complete_location_summary(summary: pd.DataFrame, value_columns: Iterable[str]) -> pd.DataFrame:
    if "location_zone" not in summary.columns:
        return summary
    value_column_names = set(value_columns) | (SUMMARY_VALUE_COLUMNS & set(summary.columns))
    rows: list[dict[str, object]] = []
    for location in LOCATION_ORDER:
        existing = summary[summary["location_zone"] == location]
        if existing.empty:
            row: dict[str, object] = {column: pd.NA for column in summary.columns}
            row["location_zone"] = location
            if "count" in row:
                row["count"] = 0
            for column in value_column_names:
                if column in row:
                    row[column] = 0
            for column in summary.columns:
                if column == "location_zone" or column == "count" or column in value_column_names:
                    continue
                if column in row and pd.notna(row[column]):
                    continue
                unique_values = summary[column].dropna().unique()
                if len(unique_values) == 1:
                    row[column] = unique_values[0]
            rows.append(row)
        else:
            rows.extend(existing.to_dict("records"))
    return pd.DataFrame(rows, columns=summary.columns)


def _format_bar_value(value: object) -> str:
    if pd.isna(value):
        return ""
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _table_column_config(st, frame: pd.DataFrame, extra: dict[str, object] | None = None) -> dict[str, object]:
    """Configure numeric dataframe columns to use locale-aware digit grouping."""
    config = dict(extra or {})
    if not hasattr(st, "column_config"):
        return config
    for column in frame.select_dtypes(include="number").columns:
        config[column] = st.column_config.NumberColumn(format="localized")
    return config


def _render_dataframe(st, frame: pd.DataFrame, *, column_config: dict[str, object] | None = None) -> None:
    config = _table_column_config(st, frame, column_config)
    kwargs: dict[str, object] = {"hide_index": True, "width": "stretch"}
    if config:
        kwargs["column_config"] = config
    st.dataframe(frame, **kwargs)


def _with_bar_values(frame: pd.DataFrame, value_column: str) -> pd.DataFrame:
    result = frame.copy()
    result["bar_value"] = result[value_column].map(_format_bar_value)
    return result


def _visual_category_frame(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    result = frame.copy()
    result = result.dropna(subset=list(columns))
    for column in columns:
        result[column] = result[column].astype(str)
    return result


def _finish_bar(fig):
    fig.update_traces(textposition="outside", texttemplate="%{text}", cliponaxis=False)
    fig.update_layout(uniformtext_minsize=10, uniformtext_mode="show")
    fig.update_yaxes(rangemode="tozero", automargin=True)
    if any(getattr(trace, "x", None) is not None and set(pd.Series(trace.x).dropna().astype(str)).intersection(LOCATION_ORDER) for trace in fig.data):
        _with_location_order(fig)
    return _theme(fig)


def _apply_rent_percentile_cap(frame: pd.DataFrame, percentile: float) -> pd.DataFrame:
    """Remove the upper tail only within comparable source and rent segments."""
    percentile = percentile / 100 if percentile > 1 else percentile
    if percentile >= 1 or frame.empty:
        return frame
    priced = _valid_rent_mask(frame)
    if not priced.any():
        return frame
    group_columns = [
        column
        for column in ("source_name", "listing_type", "currency_type", "rent_frequency")
        if column in frame.columns
    ]
    thresholds = frame.loc[priced].groupby(group_columns, dropna=False)["rent_amount"].transform(
        lambda values: values.quantile(percentile)
    )
    keep_priced = frame.loc[priced, "rent_amount"] <= thresholds
    return pd.concat([frame.loc[~priced], frame.loc[priced][keep_priced]]).sort_index()


def _source_listing_counts(frame: pd.DataFrame) -> pd.DataFrame:
    counts = (
        frame["source_name"]
        .fillna("<blank>")
        .astype(str)
        .value_counts(dropna=False)
        .rename_axis("source_name")
        .reset_index(name="listing_count")
        .sort_values(["listing_count", "source_name"], ascending=[False, True])
        .reset_index(drop=True)
    )
    total = pd.DataFrame([{"source_name": "TOTAL", "listing_count": int(counts["listing_count"].sum())}])
    return pd.concat([total, counts], ignore_index=True)


def _source_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    """Show the source mix so a convenience sample is not mistaken for a census."""
    counts = _source_listing_counts(frame)
    total = int(len(frame))
    counts["share_of_filtered_listings_pct"] = counts["listing_count"].div(total).mul(100) if total else 0.0
    counts.loc[counts["source_name"] == "TOTAL", "share_of_filtered_listings_pct"] = 100.0 if total else 0.0
    return counts


def _group_stats(frame: pd.DataFrame) -> pd.DataFrame:
    priced = _priced_frame(frame, ["currency_type", "rent_frequency"])
    if priced.empty:
        return pd.DataFrame(columns=["listing_type", "currency_type", "rent_frequency", "count", "mean", "median", "min", "max"])
    return (
        priced.groupby(["listing_type", "currency_type", "rent_frequency"], dropna=False)["rent_amount"]
        .agg(count="count", mean="mean", median="median", min="min", max="max")
        .reset_index()
        .sort_values(["listing_type", "currency_type", "rent_frequency"])
    )


def _monthly_type_location_summary(frame: pd.DataFrame, currency: str) -> pd.DataFrame:
    monthly = _priced_frame(frame, ["currency_type", "rent_frequency"])
    monthly = monthly[
        (monthly["rent_frequency"] == "MONTHLY")
        & (monthly["currency_type"] == currency)
    ].copy()
    monthly = _visual_category_frame(monthly, ["listing_type", "location_zone"])
    monthly = monthly[monthly["listing_type"].isin(["RESIDENTIAL", "COMMERCIAL"])]
    monthly = monthly[monthly["location_zone"].isin(["MALE", "HULHUMALE", "OTHERS"])]
    if monthly.empty:
        return pd.DataFrame(columns=["listing_type", "location_zone", "count", "median", "mean"])
    return (
        monthly.groupby(["listing_type", "location_zone"])["rent_amount"]
        .agg(count="count", median="median", mean="mean")
        .reset_index()
        .sort_values(["listing_type", "location_zone"])
    )


def _positive_area_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = _priced_frame(frame, ["area_sqft", "currency_type", "rent_frequency", "location_zone"])
    return result[result["area_sqft"] > 0]


def _rent_per_sqft_summary(frame: pd.DataFrame) -> pd.DataFrame:
    area_ready = _positive_area_frame(frame)
    if area_ready.empty:
        return pd.DataFrame(columns=["currency_type", "rent_frequency", "location_zone", "count", "median", "mean"])
    area_ready["rent_per_sqft"] = area_ready["rent_amount"] / area_ready["area_sqft"]
    return (
        area_ready.groupby(["currency_type", "rent_frequency", "location_zone"])["rent_per_sqft"]
        .agg(count="count", median="median", mean="mean")
        .reset_index()
        .sort_values(["currency_type", "rent_frequency", "location_zone"])
    )


def _monthly_rent_by_use_case_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if "use_case" not in frame.columns:
        return pd.DataFrame(columns=["currency_type", "use_case", "count", "median", "mean"])
    priced = _priced_frame(frame, ["currency_type", "rent_frequency", "use_case"])
    priced = priced[priced["rent_frequency"] == "MONTHLY"]
    priced = _visual_category_frame(priced, ["currency_type", "use_case"])
    if priced.empty:
        return pd.DataFrame(columns=["currency_type", "use_case", "count", "median", "mean"])
    return (
        priced.groupby(["currency_type", "use_case"])["rent_amount"]
        .agg(count="count", median="median", mean="mean")
        .reset_index()
        .sort_values(["currency_type", "use_case"])
    )


def _monthly_rent_per_sqft_by_use_case_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if "use_case" not in frame.columns:
        return pd.DataFrame(columns=["currency_type", "use_case", "count", "median", "mean"])
    area_ready = _positive_area_frame(frame)
    area_ready = area_ready[area_ready["rent_frequency"] == "MONTHLY"]
    area_ready = _visual_category_frame(area_ready, ["currency_type", "use_case"])
    if area_ready.empty:
        return pd.DataFrame(columns=["currency_type", "use_case", "count", "median", "mean"])
    area_ready["rent_per_sqft"] = area_ready["rent_amount"] / area_ready["area_sqft"]
    return (
        area_ready.groupby(["currency_type", "use_case"])["rent_per_sqft"]
        .agg(count="count", median="median", mean="mean")
        .reset_index()
        .sort_values(["currency_type", "use_case"])
    )


def _rent_per_room_summary(frame: pd.DataFrame) -> pd.DataFrame:
    room_ready = _priced_frame(frame, ["room_count", "currency_type", "rent_frequency", "location_zone"])
    room_ready = room_ready[room_ready["room_count"] > 0]
    if room_ready.empty:
        return pd.DataFrame(columns=["currency_type", "rent_frequency", "location_zone", "count", "median", "mean"])
    room_ready["rent_per_room"] = room_ready["rent_amount"] / room_ready["room_count"]
    return (
        room_ready.groupby(["currency_type", "rent_frequency", "location_zone"])["rent_per_room"]
        .agg(count="count", median="median", mean="mean")
        .reset_index()
        .sort_values(["currency_type", "rent_frequency", "location_zone"])
    )


def _rent_by_location_summary(frame: pd.DataFrame) -> pd.DataFrame:
    priced = _priced_frame(frame, ["currency_type", "rent_frequency", "location_zone"])
    if priced.empty:
        return pd.DataFrame(columns=["currency_type", "rent_frequency", "location_zone", "count", "median", "mean"])
    return (
        priced.groupby(["currency_type", "rent_frequency", "location_zone"])["rent_amount"]
        .agg(count="count", median="median", mean="mean")
        .reset_index()
        .sort_values(["currency_type", "rent_frequency", "location_zone"])
    )


def _location_rate_bar(
    st,
    summary: pd.DataFrame,
    *,
    value_column: str,
    title: str,
    y_label: str,
) -> None:
    summary = _complete_location_summary(summary, [value_column])
    labelled = _with_bar_values(summary, value_column)
    labelled = _with_location_display(labelled)
    fig = px.bar(
        labelled,
        x="location_zone",
        y=value_column,
        color="location_zone",
        text="bar_value",
        hover_data={"count": True, "location_display": True, "location_zone": False},
        title=title,
        labels={value_column: y_label, "location_zone": "Location zone", "location_display": "Location"},
        category_orders=_location_category_orders(),
        color_discrete_map=LOCATION_COLOR_MAP,
    )
    st.plotly_chart(_finish_bar(fig), width="stretch")


def _use_case_rate_bar(
    st,
    summary: pd.DataFrame,
    *,
    value_column: str,
    title: str,
    y_label: str,
) -> None:
    labelled = _with_bar_values(summary, value_column)
    fig = px.bar(
        labelled,
        x="use_case",
        y=value_column,
        color="use_case",
        text="bar_value",
        hover_data={"count": True},
        title=title,
        labels={value_column: y_label, "use_case": "Use case"},
        category_orders=_use_case_category_orders(labelled),
        color_discrete_map=USE_CASE_COLOR_MAP,
    )
    st.plotly_chart(_finish_bar(fig), width="stretch")


def _commercial_use_case_area_box(st, frame: pd.DataFrame, *, show_box_values: bool = True) -> None:
    if "use_case" not in frame.columns:
        st.info("Commercial sqft area by use case is unavailable because no filtered commercial listing has both area and use case.")
        return
    area_ready = frame.dropna(subset=["area_sqft", "use_case"]).copy()
    area_ready = area_ready[area_ready["area_sqft"] > 0]
    area_ready = _visual_category_frame(area_ready, ["use_case"])
    if area_ready.empty:
        st.info("Commercial sqft area by use case is unavailable because no filtered commercial listing has both area and use case.")
        return
    counts = area_ready.groupby("use_case")["area_sqft"].size()
    if (counts < 5).any():
        st.warning("One or more commercial sqft area by use case groups have fewer than five listings.")
    fig = px.box(
        area_ready,
        x="use_case",
        y="area_sqft",
        color="use_case",
        points="all",
        hover_data=["currency_type", "rent_frequency", "rent_amount", "location_zone", "address", "source_name"],
        title="Commercial sqft area distribution by use case",
        labels={"area_sqft": "Sqft area", "use_case": "Use case"},
        category_orders=_use_case_category_orders(area_ready),
        color_discrete_map=USE_CASE_COLOR_MAP,
    )
    fig.update_traces(boxmean=True)
    if show_box_values:
        fig = _add_box_stat_labels(
            fig,
            area_ready,
            x_column="use_case",
            y_column="area_sqft",
            color_column="use_case",
        )
    st.plotly_chart(_theme(fig), width="stretch")


def _rent_type_location_bar(st, summary: pd.DataFrame, *, currency: str, statistic: str) -> None:
    if summary.empty:
        st.info(f"No monthly {currency} Residential/Commercial records with location evidence match the current filters.")
        return
    labelled = _with_bar_values(summary, statistic)
    labelled = _with_location_display(labelled)
    fig = px.bar(
        labelled,
        x="location_zone",
        y=statistic,
        color="listing_type",
        text="bar_value",
        barmode="group",
        hover_data={"count": True, "location_display": True, "location_zone": False},
        title=f"{statistic.title()} monthly {currency} rent by location and listing type",
        labels={
            "location_zone": "Location",
            "location_display": "Location",
            statistic: f"{statistic.title()} monthly rent ({currency})",
            "listing_type": "Listing type",
        },
        category_orders=_location_category_orders({"listing_type": ["RESIDENTIAL", "COMMERCIAL"]}),
        color_discrete_map={"RESIDENTIAL": THEME_COLORS[0], "COMMERCIAL": THEME_COLORS[1]},
    )
    st.plotly_chart(_finish_bar(fig), width="stretch")


def _bucketed_room_box_figure(
    frame: pd.DataFrame,
    *,
    frequency_label: str,
    currency: str,
    title: str,
    x_column: str,
    category_orders: dict[str, list[str]],
    show_box_values: bool,
):
    ordered_buckets = [
        value
        for value in category_orders.get(x_column, [])
        if value in set(frame[x_column].dropna().astype(str))
    ]
    ordered_buckets.extend(
        value
        for value in sorted(frame[x_column].dropna().astype(str).unique().tolist())
        if value not in ordered_buckets
    )
    ordered_locations = _ordered_present_values(frame, "location_zone", LOCATION_ORDER)
    bucket_positions = {bucket: index + 1 for index, bucket in enumerate(ordered_buckets)}
    location_offsets = {
        location: (index - (len(ordered_locations) - 1) / 2) * 0.24
        for index, location in enumerate(ordered_locations)
    }

    positioned = frame.copy()
    positioned[x_column] = positioned[x_column].astype(str)
    positioned["location_zone"] = positioned["location_zone"].astype(str)
    positioned["_box_x"] = positioned[x_column].map(bucket_positions) + positioned["location_zone"].map(location_offsets)

    fig = go.Figure()
    for location in ordered_locations:
        location_frame = positioned[positioned["location_zone"] == location]
        customdata = location_frame[
            ["room_count", "location_zone", "maid_room_count", "area_sqft", "address", "source_name"]
        ].to_numpy()
        fig.add_trace(
            go.Box(
                x=location_frame["_box_x"],
                y=location_frame["rent_amount"],
                name=LOCATION_LABELS.get(location, location),
                legendgroup=LOCATION_LABELS.get(location, location),
                marker_color=LOCATION_COLOR_MAP.get(location),
                boxpoints="all",
                pointpos=0,
                jitter=0.18,
                width=0.18,
                customdata=customdata,
                hovertemplate=(
                    "Room count=%{customdata[0]}<br>"
                    "Location zone=%{customdata[1]}<br>"
                    "Maid room count=%{customdata[2]}<br>"
                    "Sqft area=%{customdata[3]}<br>"
                    "Address=%{customdata[4]}<br>"
                    "Source name=%{customdata[5]}<br>"
                    f"{frequency_label} residential rent ({currency})=%{{y:,.0f}}<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        title=title,
        boxmode="group",
        xaxis_title="Room count",
        yaxis_title=f"{frequency_label} residential rent ({currency})",
    )
    fig.update_xaxes(
        tickmode="array",
        tickvals=[bucket_positions[bucket] for bucket in ordered_buckets],
        ticktext=ordered_buckets,
    )
    if show_box_values:
        fig = _add_box_stat_labels(
            fig,
            positioned,
            x_column="_box_x",
            y_column="rent_amount",
            color_column="location_zone",
        )
    return fig


def _residential_room_box(
    st,
    frame: pd.DataFrame,
    *,
    frequency_label: str,
    currency: str,
    title: str,
    x_column: str,
    category_orders: dict[str, list[str]] | None = None,
    show_box_values: bool = True,
) -> None:
    if x_column == "room_count_bucket" and category_orders is not None:
        room_box = _bucketed_room_box_figure(
            frame,
            frequency_label=frequency_label,
            currency=currency,
            title=title,
            x_column=x_column,
            category_orders=category_orders,
            show_box_values=show_box_values,
        )
        st.plotly_chart(_theme(room_box), width="stretch")
        return

    room_box = px.box(
        frame,
        x=x_column,
        y="rent_amount",
        color="location_zone",
        points="all",
        hover_data=["room_count", "location_zone", "maid_room_count", "area_sqft", "address", "source_name"],
        title=title,
        labels={
            "rent_amount": f"{frequency_label} residential rent ({currency})",
            x_column: "Room count",
            "location_zone": "Location zone",
        },
        category_orders=_location_category_orders(category_orders),
        color_discrete_map=LOCATION_COLOR_MAP,
    )
    if show_box_values:
        room_box = _add_box_stat_labels(
            room_box,
            frame,
            x_column=x_column,
            y_column="rent_amount",
            color_column="location_zone",
        )
    st.plotly_chart(_theme(room_box), width="stretch")


def _monthly_mvr_room_count_bucket(value: object) -> str:
    room_count = int(value)
    if room_count <= 2:
        return str(room_count)
    if room_count <= 4:
        return "3-4"
    return "5+"


def _render_residential_room_count_boxplots(
    st,
    room_ready: pd.DataFrame,
    *,
    frequency: str,
    frequency_label: str,
    currency: str,
    show_box_values: bool = True,
) -> None:
    if frequency == "MONTHLY" and currency in {"MVR", "USD"}:
        labelled = room_ready.copy()
        labelled["room_count_bucket"] = labelled["room_count"].map(_monthly_mvr_room_count_bucket)
        lower_rooms = labelled[labelled["room_count"].isin([1, 2])]
        middle_rooms = labelled[labelled["room_count"].between(3, 4, inclusive="both")]
        higher_rooms = labelled[labelled["room_count"] >= 5]
        for group, title, order in (
            (
                lower_rooms,
                f"Monthly {currency} residential rent distribution by room count 1-2 and location",
                ["1", "2"],
            ),
            (
                middle_rooms,
                f"Monthly {currency} residential rent distribution by room count 3-4 and location",
                ["3-4"],
            ),
            (
                higher_rooms,
                f"Monthly {currency} residential rent distribution by room count 5+ and location",
                ["5+"],
            ),
        ):
            if group.empty:
                st.info(f"No listings are available for {title.lower()}.")
                continue
            _residential_room_box(
                st,
                group,
                frequency_label=frequency_label,
                currency=currency,
                title=title,
                x_column="room_count_bucket",
                category_orders={"room_count_bucket": order},
                show_box_values=show_box_values,
            )
        return

    _residential_room_box(
        st,
        room_ready,
        frequency_label=frequency_label,
        currency=currency,
        title=f"{frequency_label} {currency} residential rent distribution by room count and location",
        x_column="room_count",
        show_box_values=show_box_values,
    )


def _listing_currency_counts(frame: pd.DataFrame, listing_type: str) -> pd.DataFrame:
    typed = frame[frame["listing_type"] == listing_type]
    currencies = typed[typed["currency_type"].isin(["MVR", "USD"])]
    return (
        currencies["currency_type"]
        .value_counts()
        .rename_axis("currency_type")
        .reset_index(name="count")
        .sort_values("currency_type")
        .reset_index(drop=True)
    )


def _render_listing_currency_buckets(st, frame: pd.DataFrame) -> None:
    left, right = st.columns(2)
    for column, listing_type, title in (
        (left, "RESIDENTIAL", "Residential Listings Overview"),
        (right, "COMMERCIAL", "Commercial Listings Overview"),
    ):
        with column:
            st.subheader(title)
            currencies = _listing_currency_counts(frame, listing_type)
            counts = currencies.set_index("currency_type")["count"].to_dict()
            _metric_row(
                st,
                [
                    (f"{listing_type.title()} MVR", int(counts.get("MVR", 0))),
                    (f"{listing_type.title()} USD", int(counts.get("USD", 0))),
                ],
            )


def render_overview(st, filtered: pd.DataFrame) -> None:
    st.header("Overview")
    _render_filter_overview_metrics(st, filtered)
    _render_listing_currency_buckets(st, filtered)
    refresh = pd.to_datetime(filtered["scraped_at"], errors="coerce", utc=True)
    latest = refresh.max()
    st.caption(f"Latest filtered scrape timestamp: {latest.isoformat()}" if pd.notna(latest) else "No scrape timestamp is available.")
    if "source_name" in filtered:
        st.subheader("Source coverage")
        _render_dataframe(st, _source_coverage(filtered))
        st.caption(
            "This is an observed listing sample, not a census of the rental market or a transaction-price index. "
            "Compare like-for-like groups and inspect source mix before drawing conclusions."
        )

    stats = _group_stats(filtered)
    st.subheader("Rent summaries separated by property type, currency, and frequency")
    st.caption("Median is the primary robust estimate. Mean, minimum, and maximum are retained as diagnostics for distribution skew.")
    _warn_invalid_rents(st, filtered)
    if stats.empty:
        st.info("No priced records match the current filters.")
    else:
        _render_dataframe(st, stats)
        small = stats[stats["count"] < 5]
        if not small.empty:
            st.warning("Some rent groups contain fewer than five listings; interpret their summaries cautiously.")

    left, right = st.columns(2)
    with left:
        st.subheader("Listings by location")
        locations = filtered["location_zone"].dropna().value_counts().rename_axis("location_zone").reset_index(name="count")
        locations = _with_bar_values(locations, "count")
        locations = _with_location_display(locations)
        fig = px.bar(
            locations,
            x="location_zone",
            y="count",
            color="location_zone",
            text="bar_value",
            hover_data={"location_display": True, "location_zone": False},
            category_orders=_location_category_orders(),
            color_discrete_map=LOCATION_COLOR_MAP,
            labels={"location_zone": "Location", "location_display": "Location"},
        )
        st.plotly_chart(_finish_bar(fig), width="stretch")
    with right:
        st.subheader("Listings by listing type")
        types = filtered["listing_type"].dropna().value_counts().rename_axis("listing_type").reset_index(name="count")
        types = _with_bar_values(types, "count")
        fig = px.bar(
            types,
            x="listing_type",
            y="count",
            color="listing_type",
            text="bar_value",
            color_discrete_sequence=THEME_COLORS,
        )
        st.plotly_chart(_finish_bar(fig), width="stretch")

    st.subheader("Currency distribution")
    currencies = filtered["currency_type"].dropna().value_counts().rename_axis("currency_type").reset_index(name="count")
    if currencies.empty:
        st.info("No currency values match the current filters.")
    else:
        fig = px.pie(
            currencies,
            names="currency_type",
            values="count",
            hole=0.55,
            color_discrete_sequence=THEME_COLORS,
        )
        st.plotly_chart(_theme(fig), width="stretch")


def render_rent_explorer(st, filtered: pd.DataFrame, *, show_box_values: bool = True) -> None:
    st.header("Rent Explorer")
    st.caption(
        "Median advertised rent is the primary comparison because a small number of high-priced listings can distort a mean. "
        "Counts remain visible on every rate chart."
    )
    _warn_invalid_rents(st, filtered)
    priced = _priced_frame(filtered, ["currency_type", "rent_frequency"])
    if priced.empty:
        st.info("No priced listings match the current filters.")
        return

    stats = _group_stats(filtered)
    _render_dataframe(st, stats)
    visual_priced = _visual_category_frame(priced, ["listing_type", "currency_type", "rent_frequency", "location_zone"])
    if visual_priced.empty:
        st.info("No priced listings with complete visualization categories match the current filters.")
        return

    st.subheader("Monthly rent by location and listing type")
    for currency in ("MVR", "USD"):
        summary = _monthly_type_location_summary(visual_priced, currency)
        st.markdown(f"**{currency}**")
        _rent_type_location_bar(st, summary, currency=currency, statistic="median")

    for listing_type in ("RESIDENTIAL", "COMMERCIAL"):
        if listing_type not in set(visual_priced["listing_type"].dropna().unique()):
            continue
        type_frame = visual_priced[visual_priced["listing_type"] == listing_type]
        st.subheader(f"{listing_type.title()} rent distributions")
        for (currency, frequency), group in type_frame.groupby(["currency_type", "rent_frequency"]):
            title = f"{listing_type.title()} - {_money_label(currency)} - {frequency}"
            if len(group) < 5:
                st.warning(f"{title}: only {len(group)} listing(s); the median is unstable.")
            fig = px.box(
                group,
                x="location_zone",
                y="rent_amount",
                color="location_zone",
                points="all",
                hover_data=["room_count", "area_sqft", "address", "source_name"],
                title=title,
                labels={"rent_amount": f"Advertised rent ({currency}, {frequency})", "location_zone": "Location zone"},
                category_orders=_location_category_orders(),
                color_discrete_map=LOCATION_COLOR_MAP,
            )
            if show_box_values:
                fig = _add_box_stat_labels(
                    fig,
                    group,
                    x_column="location_zone",
                    y_column="rent_amount",
                    color_column="location_zone",
                )
            st.plotly_chart(_theme(_with_location_order(fig)), width="stretch")


def render_residential_explorer(st, filtered: pd.DataFrame, *, show_box_values: bool = True) -> None:
    st.header("Residential Explorer")
    residential_filtered = filtered[filtered["listing_type"] == "RESIDENTIAL"]
    _render_listing_overview_metrics(st, residential_filtered)
    _warn_invalid_rents(st, residential_filtered, "residential rent")
    residential_all = filtered[
        (filtered["listing_type"] == "RESIDENTIAL")
        & _valid_rent_mask(filtered)
    ].copy()
    if residential_all.empty:
        st.info("No residential listings match the current filters.")
        return

    st.caption("This page analyses residential listings. Daily and monthly residential rents, and MVR and USD values, are never combined.")
    for frequency in ("MONTHLY", "DAILY"):
        frequency_frame = residential_all[residential_all["rent_frequency"] == frequency].copy()
        if frequency_frame.empty:
            continue
        frequency_label = frequency.title()
        period_label = "month" if frequency == "MONTHLY" else "day"
        st.header(f"{frequency_label} Residential Rent")
        for currency in ("MVR", "USD"):
            residential = frequency_frame[frequency_frame["currency_type"] == currency].copy()
            if residential.empty:
                continue
            residential = _visual_category_frame(residential, ["currency_type", "location_zone"])
            if residential.empty:
                continue
            st.subheader(f"{frequency_label} {currency} residential rent")
            box = px.box(
                residential,
                x="location_zone",
                y="rent_amount",
                color="location_zone",
                points="all",
                hover_data=["room_count", "maid_room_count", "area_sqft", "address", "source_name"],
                title=f"{frequency_label} {currency} residential rent distribution by location",
                labels={"rent_amount": f"{frequency_label} residential rent ({currency})", "location_zone": "Location zone"},
                category_orders=_location_category_orders(),
                color_discrete_map=LOCATION_COLOR_MAP,
            )
            if show_box_values:
                box = _add_box_stat_labels(
                    box,
                    residential,
                    x_column="location_zone",
                    y_column="rent_amount",
                    color_column="location_zone",
                )
            st.plotly_chart(_theme(_with_location_order(box)), width="stretch")

            room_ready = residential.dropna(subset=["room_count"])
            if room_ready.empty:
                st.info(f"{frequency_label} {currency} room-count analysis is unavailable because no filtered residential listing has a room count.")
            elif frequency == "DAILY" and currency == "MVR":
                st.caption(
                    "Daily MVR residential room-count charts are hidden because daily room listings can describe "
                    "one rented room inside a larger apartment, which can make parsed room counts misleading."
                )
            else:
                summary = (
                    room_ready.groupby(["room_count", "location_zone"])["rent_amount"]
                    .agg(count="count", median="median", mean="mean")
                    .reset_index()
                    .sort_values(["room_count", "location_zone"])
                )
                if (summary["count"] < 5).any():
                    st.warning(f"One or more {frequency_label} {currency} room/location groups have fewer than five listings.")
                _render_residential_room_count_boxplots(
                    st,
                    room_ready,
                    frequency=frequency,
                    frequency_label=frequency_label,
                    currency=currency,
                    show_box_values=show_box_values,
                )
                median_summary = _with_bar_values(summary, "median")
                median_bar = px.bar(
                    median_summary,
                    x="room_count",
                    y="median",
                    color="location_zone",
                    text="bar_value",
                    barmode="group",
                    hover_data=["count"],
                    title=f"Median {frequency_label.lower()} {currency} residential rent by room count and location",
                    labels={"median": f"Median {frequency_label.lower()} residential rent ({currency})", "room_count": "Room count"},
                    category_orders=_location_category_orders(),
                    color_discrete_map=LOCATION_COLOR_MAP,
                )
                st.plotly_chart(_finish_bar(median_bar), width="stretch")

            per_room_summary = _rent_per_room_summary(residential)
            if per_room_summary.empty:
                st.info(f"{frequency_label} {currency} average-per-room analysis is unavailable because no filtered residential listing has a positive room count.")
            else:
                if (per_room_summary["count"] < 5).any():
                    st.warning(f"One or more {frequency_label} {currency} average-per-room groups have fewer than five listings.")
                currency_frequency_summary = per_room_summary[
                    (per_room_summary["currency_type"] == currency)
                    & (per_room_summary["rent_frequency"] == frequency)
                ]
                _location_rate_bar(
                    st,
                    currency_frequency_summary,
                    value_column="median",
                    title=f"Median {frequency_label.lower()} {currency} residential rent per room by location",
                    y_label=f"Median {currency} per room per {period_label}",
                )

            area_ready = _positive_area_frame(residential)
            if area_ready.empty:
                st.info(f"{frequency_label} {currency} residential rent per sqft area by location is unavailable because no filtered listings have both rent and area.")
            else:
                area_ready["rent_per_sqft"] = area_ready["rent_amount"] / area_ready["area_sqft"]
                sqft_summary = (
                    area_ready.groupby("location_zone")["rent_per_sqft"]
                    .agg(count="count", median="median", mean="mean")
                    .reset_index()
                    .sort_values("location_zone")
                )
                _location_rate_bar(
                    st,
                    sqft_summary,
                    value_column="median",
                    title=f"Median {frequency_label.lower()} residential rent per sqft area ({currency}) by location",
                    y_label=f"Median {currency} per sqft area per {period_label}",
                )


def render_commercial_explorer(st, filtered: pd.DataFrame, *, show_box_values: bool = True) -> None:
    st.header("Commercial Explorer")
    commercial_filtered = filtered[filtered["listing_type"] == "COMMERCIAL"]
    _render_listing_overview_metrics(st, commercial_filtered)
    _render_commercial_use_case_metrics(st, commercial_filtered)
    _warn_invalid_rents(st, commercial_filtered, "commercial rent")
    commercial = filtered[
        (filtered["listing_type"] == "COMMERCIAL")
        & _valid_rent_mask(filtered)
        & filtered["currency_type"].notna()
        & filtered["rent_frequency"].notna()
    ].copy()
    if commercial.empty:
        st.info("No commercial listings match the current filters.")
        return
    commercial = _visual_category_frame(commercial, ["currency_type", "rent_frequency", "location_zone"])
    if commercial.empty:
        st.info("No commercial listings with complete visualization categories match the current filters.")
        return

    st.subheader("MVR commercial rent by location")
    rent_summary = _rent_by_location_summary(commercial)
    mvr_rent_summary = rent_summary[rent_summary["currency_type"] == "MVR"]
    if mvr_rent_summary.empty:
        st.info("No priced commercial MVR listings match the current filters.")
    else:
        if (mvr_rent_summary["count"] < 5).any():
            st.warning("One or more MVR commercial rent groups have fewer than five listings.")
        for frequency, group in mvr_rent_summary.groupby("rent_frequency"):
            period_label = "month" if frequency == "MONTHLY" else "day"
            st.markdown(f"**MVR - {str(frequency).title()}**")
            _location_rate_bar(
                st,
                group,
                value_column="median",
                title=f"Median {str(frequency).lower()} MVR commercial rent by location",
                y_label=f"Median MVR per {period_label}",
            )

    st.subheader("Commercial sqft area by use case")
    _commercial_use_case_area_box(st, commercial, show_box_values=show_box_values)

    st.subheader("Monthly commercial rent by use case")
    use_case_rent_summary = _monthly_rent_by_use_case_summary(commercial)
    if use_case_rent_summary.empty:
        st.info("Monthly commercial rent-by-use-case analysis is unavailable because no filtered commercial listing has both rent and use case.")
    else:
        if (use_case_rent_summary["count"] < 5).any():
            st.warning("One or more monthly commercial rent-by-use-case groups have fewer than five listings.")
        for currency, group in use_case_rent_summary.groupby("currency_type"):
            if currency not in {"MVR", "USD"}:
                continue
            st.markdown(f"**{currency} - Monthly**")
            _use_case_rate_bar(
                st,
                group,
                value_column="median",
                title=f"Median monthly {currency} commercial rent by use case",
                y_label=f"Median {currency} per month",

            )

    st.subheader("Commercial rent per sqft area by location")
    sqft_summary = _rent_per_sqft_summary(commercial)
    if sqft_summary.empty:
        st.info("Commercial rent per sqft area by location is unavailable because no filtered commercial listing has both rent and area.")
    else:
        if (sqft_summary["count"] < 5).any():
            st.warning("One or more commercial rent per sqft area by location groups have fewer than five listings.")
        for (currency, frequency), group in sqft_summary.groupby(["currency_type", "rent_frequency"]):
            period_label = "month" if frequency == "MONTHLY" else "day"
            st.markdown(f"**{currency} - {frequency.title()}**")
            _location_rate_bar(
                st,
                group,
                value_column="median",
                title=f"Median {currency} commercial rent per sqft area by location",
                y_label=f"Median {currency} per sqft area per {period_label}",
            )

    st.subheader("Monthly commercial rent per sqft area by use case")
    use_case_sqft_summary = _monthly_rent_per_sqft_by_use_case_summary(commercial)
    if use_case_sqft_summary.empty:
        st.info("Monthly commercial rent per sqft area by use case is unavailable because no filtered commercial listing has rent, area, and use case.")
        return
    if (use_case_sqft_summary["count"] < 5).any():
        st.warning("One or more monthly commercial rent per sqft area by use case groups have fewer than five listings.")
    for currency, group in use_case_sqft_summary.groupby("currency_type"):
        if currency not in {"MVR", "USD"}:
            continue
        st.markdown(f"**{currency} - Monthly**")
        _use_case_rate_bar(
            st,
            group,
            value_column="median",
            title=f"Median monthly {currency} commercial rent per sqft area by use case",
            y_label=f"Median {currency} per sqft area per month",
        )


def render_listings_table(st, filtered: pd.DataFrame) -> None:
    st.header("Listings Table")
    display_columns = [
        "rent_amount",
        "currency_type",
        "rent_frequency",
        "listing_type",
        "use_case",
        "room_count",
        "maid_room_count",
        "area_sqft",
        "location_zone",
        "address",
        "last_updated",
        "status",
        "source_name",
        "source_url",
        "scraped_at",
    ]
    table = filtered.loc[:, display_columns].copy()
    if table.empty:
        st.info("No listings match the current filters.")
        return
    table["location_zone"] = table["location_zone"].map(_location_display)
    st.subheader("Listings by source")
    _render_dataframe(st, _source_listing_counts(filtered))
    link_config = (
        {"source_url": st.column_config.LinkColumn("Source URL")}
        if hasattr(st, "column_config")
        else None
    )
    _render_dataframe(st, table, column_config=link_config)


def render_methodology(st) -> None:
    st.header("Methodology and Data Quality")
    st.markdown(
        """
**Scope.** The pipeline supports public iBay rental categories and public Property.mv residential/commercial rental search pages. iBay uses Scrapy for requests, pagination, throttling, retries, and robots.txt compliance; BeautifulSoup parses detail-page HTML. Property.mv uses source-specific HTTP discovery and emits the same raw-record contract before preprocessing.

**Schema-aligned imports.** Separately maintained canonical datasets can be placed in `data/imports/schema_aligned/`. Accepted import rows are written to `data/processed/imports/schema_aligned_imports.csv` and appended to the dashboard master; rejected import rows are traceable in `schema_aligned_import_review.csv`.

**Dashboard input and display.** The dashboard reads only processed `.csv.gz`, `.csv`, or `.parquet` datasets and does not start scraping from Streamlit. It includes all status values by default for historical analysis. Overview reports source coverage and source share of the filtered sample. Rate charts use median advertised rent with group counts, while mean, minimum, and maximum remain summary-table diagnostics. The rent-percentile control removes the upper tail only within matching source, property-type, currency, and rent-frequency segments. Canonical `MALE` and `HULHUMALE` values display as `Male'` and `Hulhumale'`.

**Extraction evidence hierarchy.** Price candidates are considered only inside the selected primary listing container: primary price block, then matching title/description evidence, then title, then description. Similar Items, related content, promoted cards, navigation, footer, ads, and seller-profile cards are excluded before extraction.

**Currency and frequency.** Explicit USD and MVR indicators are preserved. If permitted price evidence contains both MVR and USD candidates, the USD rent candidate is selected. When a positive rent exists but no explicit currency appears, the pipeline defaults the currency to MVR and records this in review evidence. Missing frequency defaults to MONTHLY only when the listing is not an advance, upfront, or multi-year payment. No currency conversion is performed. Daily and monthly rents are always presented separately.

**Dates and exclusions.** The `last_updated` date comes from the listing metadata line containing `Last Updated`. Sale/non-rental pages, missing prices, contact numbers parsed as price, multi-year lump sums, unresolved material price conflicts, and records with parsed SQFT area below 100 are excluded from the processed master dataset. The raw JSONL retains extraction evidence. `ibay_extraction_review.csv` contains conflicts and parsing review reasons; `ibay_validation_issues.csv` contains data-quality findings, including conservative high-price review flags.

**Local access.** The desktop launcher starts the dashboard on `127.0.0.1` so it is available only from the local computer by default. Docker binds to `0.0.0.0` inside the container so host port publishing works as expected.

**Limitations.** Listing pages are unstructured and can change. This is an observed listing sample, not a full market census or a transaction-rent index. The percentile control is intended to reduce the influence of mislabelled sale and long-term-lease values when source evidence alone cannot resolve them.
        """
    )


def main() -> None:
    st.set_page_config(page_title="iBay Rental Dashboard", layout="wide")
    st.title("iBay Rental Dashboard")
    try:
        frame = load_dataset()
    except FileNotFoundError as exc:
        st.error(str(exc) or _dataset_missing_details())
        return
    except Exception as exc:
        st.error(f"Unable to load the processed dataset: {exc}")
        return
    st.caption(
        f"Loaded {len(frame):,} processed listing(s) from `{frame.attrs.get('dataset_path', 'unknown dataset')}`"
        + (
            f" updated {frame.attrs['dataset_updated_at']}"
            if frame.attrs.get("dataset_updated_at")
            else ""
        )
    )

    st.sidebar.header("Filters")
    st.sidebar.caption("Select values to include. Leave a filter empty to include all values.")
    status = st.sidebar.multiselect("Status", _available_values(frame, "status"), placeholder="All statuses")
    listing_type = st.sidebar.multiselect("Listing type", _available_values(frame, "listing_type"), placeholder="All listing types")
    use_case = st.sidebar.multiselect("Use case", _available_values(frame, "use_case"), placeholder="All use cases")
    currency = st.sidebar.multiselect("Currency", _available_values(frame, "currency_type"), placeholder="All currencies")
    frequency = st.sidebar.multiselect("Rent frequency", _available_values(frame, "rent_frequency"), placeholder="All frequencies")
    location = st.sidebar.multiselect(
        "Location zone",
        _available_values(frame, "location_zone"),
        placeholder="All locations",
        format_func=_location_display,
    )
    rooms = st.sidebar.multiselect(
        "Room count",
        _room_filter_options(frame),
        placeholder="All room counts",
    )
    source_name = st.sidebar.multiselect("Source name", _available_values(frame, "source_name"), placeholder="All sources")
    rent_percentile = st.sidebar.slider(
        "Max rent percentile for analysis",
        min_value=0.90,
        max_value=1.00,
        value=0.99,
        step=0.01,
        format="%.2f",
        help="Exclude the highest-rent values within each source, property type, currency, and rent-frequency segment.",
    )
    if rent_percentile < 1:
        st.sidebar.caption(
            f"Analysis excludes rent above the {rent_percentile:.2f} percentile within comparable source and rent segments."
        )
    show_box_values = st.sidebar.checkbox(
        "Show exact boxplot values",
        value=True,
        help="Show or hide minimum, median, and maximum value labels on boxplots.",
    )

    filtered = apply_filters(
        frame,
        status=status,
        listing_type=listing_type,
        use_case=use_case,
        currency=currency,
        frequency=frequency,
        location=location,
        rooms=rooms,
        source_name=source_name,
    )
    if filtered.empty:
        st.warning(
            f"The processed dataset loaded successfully with {len(frame):,} row(s), "
            "but the current sidebar filter selections match 0 rows. Clear one or more filters to show data."
        )
        return
    visualized = _apply_rent_percentile_cap(filtered, rent_percentile)
    if visualized.empty:
        st.warning("The current filters and rent percentile cap leave no rows for analysis. Increase the cap toward 1.00.")
        return
    page = st.sidebar.radio("Page", ["Overview", "Rent Explorer", "Residential Explorer", "Commercial Explorer", "Listings Table", "Methodology and Data Quality"])
    if page == "Overview":
        render_overview(st, visualized)
    elif page == "Rent Explorer":
        render_rent_explorer(st, visualized, show_box_values=show_box_values)
    elif page == "Residential Explorer":
        render_residential_explorer(st, visualized, show_box_values=show_box_values)
    elif page == "Commercial Explorer":
        render_commercial_explorer(st, visualized, show_box_values=show_box_values)
    elif page == "Listings Table":
        render_listings_table(st, visualized)
    else:
        render_methodology(st)


if __name__ == "__main__":
    main()
