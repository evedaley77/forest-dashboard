from pathlib import Path

import pandas as pd
import altair as alt
import streamlit as st

import loader

st.set_page_config(page_title="CNN User Journeys Regression Testing - Automation Dashboard", page_icon=":material/monitoring:", layout="wide",
                   initial_sidebar_state="collapsed")

STATUS_COLORS = {"Passed": "#2E9D5B", "Failed": "#E5484D", "Unknown": "#9AA0A6"}


@st.cache_data(show_spinner="Reading export (large files are parsed once, then cached)...")
def load(path: str, size: int, mtime: float) -> pd.DataFrame:
    # size and mtime are part of the cache key so a re-downloaded file is re-read (schema v2: artifact_name).
    return loader.load_export(Path(path))


def fmt_mb(path: Path) -> str:
    return f"{path.stat().st_size / 1_048_576:,.1f} MB"


# ---------------------------------------------------------------- header + filter bar (top of page)
TITLE = "CNN User Journeys Regression Testing - Automation Dashboard"
st.title(TITLE)
caption_slot = st.empty()

ALL = "All test names"
with st.container(border=True):
    row1 = st.columns(4)
    row2 = st.columns(5, vertical_alignment="bottom")

    # Folder + import live in a popover so the bar stays compact.
    with row2[4].popover("Import / folder", icon=":material/upload_file:", width="stretch"):
        folder = Path(
            st.text_input("Look for exports in", value=str(Path.home() / "Downloads"), help="Matches Forest2_Results*.csv")
        ).expanduser()
        uploaded = st.file_uploader("Import another log file", type=["csv"], help="Saved to the imports folder")
        if uploaded is not None and st.session_state.get("_last_upload") != uploaded.file_id:
            loader.IMPORT_DIR.mkdir(exist_ok=True)
            dest = loader.IMPORT_DIR / uploaded.name
            dest.write_bytes(uploaded.getbuffer())
            st.session_state["_last_upload"] = uploaded.file_id
            st.session_state["files"] = [str(dest.resolve())]
            st.rerun()

    exports = loader.list_exports(loader.IMPORT_DIR, folder)
    by_path = {str(p.resolve()): p for p in exports}
    if not by_path:
        st.info(f"No Forest2_Results*.csv files in {folder} or imports. Use Import / folder above.")
        st.stop()

    st.session_state["files"] = [f for f in st.session_state.get("files", []) if f in by_path] or [next(iter(by_path))]
    with row1[0]:
        chosen = st.multiselect(
            "Log file",
            options=list(by_path),
            key="files",
            format_func=lambda k: f"{by_path[k].name} ({fmt_mb(by_path[k])})",
            help="Pick one or several exports; runs are de-duplicated by source file.",
        )
    if not chosen:
        st.info("Select at least one log file.")
        st.stop()

    # ------------------------------------------------------------ load
    frames = []
    for key in chosen:
        p = by_path[key]
        stt = p.stat()
        frames.append(load(key, stt.st_size, stt.st_mtime))
    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values("started_at", na_position="first").drop_duplicates("source_file", keep="last")
    df["run_date"] = pd.to_datetime(df["run_date"])
    df["artifact_name"] = df["artifact_name"].fillna(df["workflow"]).fillna("(unknown)")

    # ------------------------------------------------------------ filters
    lo, hi = df["run_date"].min().date(), df["run_date"].max().date()
    with row1[1]:
        dates = st.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi, key=f"dates_{lo}_{hi}")
    if len(dates) != 2:
        st.info("Pick an end date.")
        st.stop()

    in_dates = df[(df["run_date"] >= pd.Timestamp(dates[0])) & (df["run_date"] <= pd.Timestamp(dates[1]))]
    with row1[2]:
        test_pick = st.selectbox(
            "Test name", [ALL] + sorted(in_dates["test_name"].dropna().unique()),
            help="Show only the line items for one test.",
        )
    with row1[3]:
        artifacts = st.multiselect("Artifact name", sorted(in_dates["artifact_name"].unique()), placeholder="All")

    def multi(slot, label: str, col: str):
        with slot:
            return st.multiselect(label, sorted(df[col].dropna().unique()), placeholder="All")

    workflows = multi(row2[0], "Workflow", "workflow")
    brands = multi(row2[1], "Brand", "brand")
    platforms = multi(row2[2], "Platform", "platform")
    with row2[3]:
        statuses = st.multiselect("Status", list(STATUS_COLORS), placeholder="All")

if df["status"].eq("Unknown").all():
    st.warning(
        "These exports only list workflow and source file names (no results payload), so pass/fail "
        "is unknown. Pick a full export for pass rates and durations."
    )

f = df[(df["run_date"] >= pd.Timestamp(dates[0])) & (df["run_date"] <= pd.Timestamp(dates[1]))]
if test_pick != ALL:
    f = f[f["test_name"] == test_pick]
for col, picked in [("artifact_name", artifacts), ("workflow", workflows), ("brand", brands), ("platform", platforms), ("status", statuses)]:
    if picked:
        f = f[f[col].isin(picked)]

# ---------------------------------------------------------------- main
caption_slot.caption(f"{len(chosen)} file(s) · {dates[0]:%b %d, %Y} to {dates[1]:%b %d, %Y} · {len(f):,} of {len(df):,} runs")

if f.empty:
    st.info("No runs match these filters.")
    st.stop()

known = f[f["status"] != "Unknown"]
passed, failed = (known["status"] == "Passed").sum(), (known["status"] == "Failed").sum()
with st.container(horizontal=True):
    st.metric("Runs", f"{len(f):,}", border=True)
    st.metric("Pass rate", f"{passed / len(known):.1%}" if len(known) else "n/a", border=True)
    st.metric("Failed runs", f"{failed:,}", border=True)
    st.metric("Avg duration", f"{f['duration_s'].mean() / 60:.1f} min" if f["duration_s"].notna().any() else "n/a", border=True)
    st.metric("Failed validations", f"{int(f['validations_failed'].fillna(0).sum()):,}", border=True)

left, right = st.columns(2)
with left, st.container(border=True):
    st.subheader("Runs per day")
    daily = f.groupby([f["run_date"].dt.date, "status"]).size().unstack(fill_value=0)
    cols = [s for s in STATUS_COLORS if s in daily.columns]
    st.bar_chart(daily[cols], color=[STATUS_COLORS[c] for c in cols], alt="Stacked bar chart of runs per day by status")

with right, st.container(border=True):
    st.subheader("Pass rate by workflow")
    wf = known.groupby("workflow")["passed"].agg(rate=lambda s: s.astype(float).mean(), runs="size")
    if wf.empty:
        st.caption("No pass/fail data.")
    else:
        st.bar_chart(wf.sort_values("rate"), y="rate", horizontal=True, color="#2E9D5B",
                     alt="Bar chart of pass rate by workflow")

with st.container(border=True):
    st.subheader("Test names within each artifact")
    measure = st.segmented_control("Count", ["Runs", "Failed runs"], default="Runs", label_visibility="collapsed")
    base = f if measure == "Runs" else f[f["status"] == "Failed"]
    if base.empty:
        st.caption("Nothing to chart for this selection.")
    else:
        agg = base.groupby(["artifact_name", "test_name"]).size().rename("count").reset_index()
        order = agg.groupby("artifact_name")["count"].sum().sort_values(ascending=False).index.tolist()
        chart = (
            alt.Chart(agg, height=max(160, 24 * len(order)))
            .mark_bar(stroke="white", strokeWidth=1)
            .encode(
                y=alt.Y("artifact_name:N", sort=order, title=None, axis=alt.Axis(labelLimit=320)),
                x=alt.X("count:Q", title=measure, stack="zero"),
                color=alt.Color("test_name:N", legend=None, scale=alt.Scale(scheme="tableau20")),
                order=alt.Order("test_name:N"),
                tooltip=[alt.Tooltip("artifact_name:N", title="Artifact"), alt.Tooltip("test_name:N", title="Test name"),
                         alt.Tooltip("count:Q", title=measure)],
            )
        )
        st.altair_chart(chart, alt="Stacked bar chart: one bar per artifact name, split into segments by test name")
        st.caption("Each bar is an artifact name; each colored segment is one test name. Hover a segment to see which.")

tab_fail, tab_runs, tab_tests = st.tabs(["Failures", "All runs", "By test"])
cfg = {
    "run_date": st.column_config.DateColumn("Date"),
    "duration_s": st.column_config.NumberColumn("Duration (s)", format="%.0f"),
    "started_at": st.column_config.DatetimeColumn("Started", format="MMM D, h:mm a"),
}
show = ["run_date", "artifact_name", "workflow", "test_name", "brand", "platform", "browser", "status", "duration_s",
        "steps_failed", "validations_failed", "first_failure", "error", "source_file"]
with tab_fail:
    bad = f[f["status"] == "Failed"].sort_values("started_at", ascending=False)
    if bad.empty:
        st.success("No failed runs in this selection.")
    else:
        st.dataframe(bad[show], hide_index=True, column_config=cfg, alt="Failed runs")
with tab_runs:
    st.dataframe(f.sort_values("started_at", ascending=False)[show], hide_index=True, column_config=cfg, alt="All runs")
    st.download_button("Download filtered runs (CSV)", f[show].to_csv(index=False), "filtered_runs.csv", "text/csv")
with tab_tests:
    t = (
        known.groupby("test_name")
        .agg(runs=("status", "size"), failures=("status", lambda s: (s == "Failed").sum()), avg_duration_s=("duration_s", "mean"))
        .assign(pass_rate=lambda d: 1 - d["failures"] / d["runs"])
        .sort_values(["failures", "runs"], ascending=False)
    )
    st.dataframe(t, column_config={"pass_rate": st.column_config.ProgressColumn("Pass rate", min_value=0, max_value=1, format="percent"),
                                   "avg_duration_s": st.column_config.NumberColumn("Avg duration (s)", format="%.0f")},
                 alt="Pass rate per test")

st.caption("Created using Anthropic Claude. Keep on internal versions until a human has reviewed and verified it.")
