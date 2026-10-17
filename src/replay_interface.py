"""Streamlit-native replay controls and nonblocking revision polling."""

import sqlite3

import streamlit as st

from src.database import connect
from src.simulator import HISTORICAL, SCENARIO, ReplaySession, historical_plan, scenario_plan

LABORATORY = "Laboratory Dataset"
MODES = [LABORATORY, HISTORICAL, SCENARIO]


def select_data_source(primary, *, target=None, controls=True):
    from src.workspace_interface import autosave_workspace
    ui = target or st.sidebar
    mode = ui.selectbox("Data Source", MODES, key="data_source") if controls else st.session_state.setdefault('data_source',LABORATORY)
    if not controls:
        st.session_state['data_source']=mode  # Detach the canonical value from widget cleanup.
    controllers = st.session_state.setdefault("replay_controllers", {})
    previous = st.session_state.get("active_data_source", LABORATORY)
    if previous != mode:
        for controller in controllers.values():
            controller.pause()
        # Canonical filters are saved separately per mode; widget keys remain temporary.
        from src.workspaces import FILTERS
        names = FILTERS
        saved = st.session_state.setdefault("source_filters", {})
        saved[previous] = {name: st.session_state[name] for name in names if name in st.session_state}
        for name in names:
            st.session_state.pop(name, None)
        st.session_state.update(saved.get(mode, {}))
    st.session_state["active_data_source"] = mode
    if mode == LABORATORY:
        autosave_workspace()
        return primary, None
    errors = st.session_state.get("replay_recovery_errors", {})
    if mode in errors:
        st.warning(errors[mode])
        if ui.button("Reset Replay", key=f"unavailable_reset_{mode}"):
            errors.pop(mode)
            st.session_state.get("unrecovered_replays", {}).pop(mode, None)
            st.rerun()
        return None, None
    if mode not in controllers:
        plan = scenario_plan() if mode == SCENARIO else historical_plan(primary)
        record = st.session_state.get("_workspace_record")
        controllers[mode] = ReplaySession(primary, plan, owner_key=record.identity if record else None)
    controller = controllers[mode]
    if controller.recovery_notice:
        st.info(controller.recovery_notice)
        controller.recovery_notice = None
    st.info(f"{mode} · Isolated session database. All counts, charts, signals and alerts below belong to {mode}.")
    if mode == SCENARIO and controls:
        st.caption(f"{SCENARIO}: deliberately constructed artificial observations, not an outbreak in the original dataset. "
                   "Batch 1: 100 baseline patients (10 R). Batch 2: 40 recent patients (4 R). "
                   "Batch 3: 60 additional recent patients (36 R). All 200 patient identifiers are distinct.")
    if controls:
        render_replay_controls(controller,mode,ui)
    autosave_workspace()
    return controller.settings, controller


def render_replay_controls(controller,mode,ui):
    from src.workspace_interface import autosave_workspace
    with ui.expander(f"{mode} controls", expanded=True):
        interval = st.selectbox("Playback Speed", [2, 1, 5, 10], format_func=lambda value: f"1 batch / {value} s", key=f"speed_{mode}")
        controller.set_speed(interval)
        start, pause = st.columns(2)
        if start.button("Start Replay", disabled=controller.complete, key=f"start_{mode}"):
            if autosave_workspace():
                controller.start()
                autosave_workspace()
        if pause.button("Pause Replay", disabled=not controller.running, key=f"pause_{mode}"):
            controller.pause()
            autosave_workspace()
            st.rerun()
        if st.button("Next Batch", disabled=controller.complete, key=f"next_{mode}"):
            if autosave_workspace():
                controller.step()
                autosave_workspace()
            st.rerun()
        if st.button("Reset Replay", key=f"reset_{mode}"):
            controller.reset()
            for name in ("analysis_date", "custom_period"):
                st.session_state.pop(name, None)
            st.session_state["follow_latest_date"] = True
            autosave_workspace()
            st.rerun()
        st.progress(controller.cursor / len(controller.plan.periods) if controller.plan.periods else 0)
        st.caption(f"{mode}: {controller.cursor}/{len(controller.plan.periods)} batches · "
                   f"{'Complete' if controller.complete else 'Running' if controller.running else 'Paused'}")
        if not controller.complete:
            start_date, end_date = controller.plan.periods[controller.cursor]
            st.caption(f"Next batch: {start_date} – {end_date}")


def show_update(settings, catalog):
    seen = st.session_state.setdefault("seen_revisions", {})
    identity = str(settings.database_path)
    old = seen.get(identity)
    if old is not None and old != catalog.version:
        st.success(f"Data updated · Version {old} → {catalog.version} · Latest Sample Date: {catalog.latest_sample} · "
                   f"Last Successful Import: {catalog.last_successful_import} UTC. Analytics recalculated for this revision.")
    seen[identity] = catalog.version


@st.fragment(run_every=1.0)
def refresh_watcher(settings, expected_version, expected_import, controller):
    """A timer checks once, then yields. No blocking polling loop or global state."""
    try:
        from src.workspace_interface import autosave_workspace
        if st.session_state.get("workspace_save_error"):
            if controller:
                controller.pause()
        result = controller.tick() if controller and not st.session_state.get("workspace_save_error") else None
        if result is not None:
            autosave_workspace()
        with connect(settings, read_only=True) as con:
            version = con.execute("SELECT version FROM data_version WHERE singleton_id=1").fetchone()[0]
            latest_import = con.execute("SELECT MAX(finished_at) FROM import_batches WHERE status='completed'").fetchone()[0]
        if result is not None or version != expected_version or latest_import != expected_import:
            st.rerun(scope="app")
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        if controller:
            controller.pause()
        st.error(f"Automatic refresh paused: {exc}. Retry the batch or Reset Replay.")
