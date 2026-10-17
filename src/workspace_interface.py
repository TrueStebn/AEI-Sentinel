"""Compact workspace controls and allowlisted autosave before/after user actions."""

import json
import sqlite3
from datetime import date

import streamlit as st

from src.replay_files import cleanup_replays
from src.simulator import ReplaySession
from src.workspaces import FILTERS, PREFERENCES, WorkspaceError, WorkspaceStore

SAVE_ERRORS = (sqlite3.Error, OSError, RuntimeError, ValueError, TypeError)


def _encode(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def capture_state():
    state = {"format": 1, "preferences": {name: _encode(st.session_state[name]) for name in PREFERENCES if name in st.session_state},
             "source_filters": _encode(st.session_state.get("source_filters", {})),
             "replays": dict(st.session_state.get("unrecovered_replays", {})),
             "laboratory_version": st.session_state.get("_laboratory_version", 0)}
    for mode, controller in st.session_state.get("replay_controllers", {}).items():
        state["replays"][mode] = controller.descriptor()
    return state


def _decode_preferences(values):
    restored = dict(values)
    if restored.get("analysis_date"):
        restored["analysis_date"] = date.fromisoformat(restored["analysis_date"])
    if "custom_period" in restored:
        restored["custom_period"] = tuple(date.fromisoformat(day) for day in restored["custom_period"])
    return restored


def apply_workspace(primary, record, state):
    from src.runtime_policy import public_demo
    if public_demo():
        raise RuntimeError('Persistent workspace restoration is disabled in Public Demo Mode.')
    for name in list(st.session_state):
        if name.startswith('_smart_'):
            st.session_state.pop(name, None)
    for controller in st.session_state.get("replay_controllers", {}).values():
        controller.pause()
        controller.lease.close()
    for name in (*PREFERENCES, *("_" + name for name in PREFERENCES)):
        st.session_state.pop(name, None)
    st.session_state.update(_decode_preferences(state.get("preferences", {})))
    st.session_state["active_data_source"] = state.get("preferences", {}).get("data_source", "Laboratory Dataset")
    st.session_state["source_filters"] = {mode: _decode_preferences(values) for mode, values in state.get("source_filters", {}).items()}
    st.session_state["replay_controllers"] = {}
    st.session_state["unrecovered_replays"] = {}
    st.session_state["replay_recovery_errors"] = {}
    for mode, descriptor in state.get("replays", {}).items():
        try:
            controller = ReplaySession.recover(primary, descriptor, record.identity)
            st.session_state["replay_controllers"][mode] = controller
            st.session_state[f"speed_{mode}"] = int(controller.interval)
        except SAVE_ERRORS as exc:
            st.session_state["unrecovered_replays"][mode] = descriptor
            st.session_state["replay_recovery_errors"][mode] = f"Replay recovery unavailable: {exc}"
    st.session_state["_workspace_record"] = record
    st.session_state.pop("workspace_save_error", None)
    st.session_state["workspace_restore_notice"] = "Workspace restored. Analytics are recalculated from current data; recovered Replay is paused."


def _queue_restore(token):
    st.session_state["_pending_restore_token"] = token
    st.rerun()


def bootstrap_workspace(primary):
    """Run before creating filter/navigation widgets so restoration can set values."""
    from src.runtime_policy import public_demo
    if public_demo():
        from src.public_demo_interface import bootstrap_public_session
        return bootstrap_public_session()
    try:
        store = st.session_state.get("_workspace_store")
        if store is None:
            store = WorkspaceStore(primary)
            st.session_state["_workspace_store"] = store
        pending = st.session_state.pop("_pending_restore_token", None)
        tokens = st.query_params.get_all("workspace")
        if len(tokens) > 1:
            raise WorkspaceError("Provide exactly one restore token in the URL.")
        token = pending or (tokens[0] if tokens else st.session_state.get("_workspace_token"))
        if st.session_state.pop("_new_workspace", False):
            token = None
        changed = bool(pending) or token != st.session_state.get("_workspace_token")
        created = token is None
        if token is None:
            token, record = store.create()
            changed = True
        elif changed or "_workspace_record" not in st.session_state:
            record = store.load(token)
        else:
            record = st.session_state["_workspace_record"]
        if changed or "_workspace_record" not in st.session_state:
            apply_workspace(primary, record, record.state)
            st.session_state["_workspace_token"] = token
            if created:
                st.session_state.pop('workspace_restore_notice',None)
        snapshot = st.session_state.pop("_pending_workspace_state", None)
        if snapshot is not None:
            apply_workspace(primary, record, snapshot)
        st.query_params["workspace"] = token
        return True
    except SAVE_ERRORS as exc:
        st.error(f"Unable to restore workspace. {exc}")
        with st.sidebar.expander("Restore Workspace", expanded=True):
            supplied = st.text_input("Restore token", type="password", key="recovery_token")
            if st.button("Restore Workspace", key="recover_failed"):
                _queue_restore(supplied.strip())
            if st.button("Start New Workspace", key="new_after_failure"):
                st.session_state["_new_workspace"] = True
                st.rerun()
        return False


def autosave_workspace(status=None):
    from src.runtime_policy import public_demo
    if public_demo():
        return True
    message, saved_stamp = status if isinstance(status, tuple) else (status, None)
    store = st.session_state.get("_workspace_store")
    if store is None:
        return True  # Standalone engine and fragment tests need no workspace runtime.
    try:
        state = capture_state()
        record = st.session_state["_workspace_record"]
        if state != record.state:
            if message is not None:
                message.caption("Saving…")
            st.session_state["_workspace_record"] = store.save(st.session_state["_workspace_token"], state, record.revision)
        st.session_state.pop("workspace_save_error", None)
        if message is not None:
            message.caption("All changes saved.")
        if saved_stamp is not None:
            saved_stamp.caption(f"Last Saved: {st.session_state['_workspace_record'].last_saved_at} UTC")
        return True
    except SAVE_ERRORS as exc:
        for controller in st.session_state.get("replay_controllers", {}).values():
            controller.pause()
        st.session_state["workspace_save_error"] = str(exc)
        if message is not None:
            message.warning("Unable to save — retry.")
        return False


def render_workspace(primary, *, target=None, controls=True):
    from src.runtime_policy import public_demo
    if public_demo():
        from src.public_demo_interface import public_workspace_notice
        return public_workspace_notice(target, controls)
    store = st.session_state["_workspace_store"]
    token = st.session_state["_workspace_token"]
    ui = target or st.sidebar
    status = ui.empty()
    if controls or st.session_state.get('workspace_save_error'):
        status.caption("Unable to save — retry." if st.session_state.get("workspace_save_error") else "All changes saved.")
    if not controls:
        if st.session_state.get('workspace_save_error') and ui.button('Retry autosave'):
            autosave_workspace(status)
            st.rerun()
        return (status if st.session_state.get('workspace_save_error') else None), None
    with ui.expander("Workspace", expanded=False):
        if st.session_state.get("workspace_save_error"):
            st.warning(st.session_state["workspace_save_error"])
            if st.button("Retry autosave"):
                autosave_workspace(status)
                st.rerun()
        saved_stamp = st.empty()
        saved_stamp.caption(f"Last Saved: {st.session_state['_workspace_record'].last_saved_at} UTC")
        st.caption("Keep the private workspace URL or copy the restore token. Anyone holding it can open this demo workspace. This is not authentication.")
        st.text_input("Your restore token (copy and keep private)", value=token, type="password", key=f"token_display_{st.session_state['_workspace_record'].identity}")
        if st.button("Start New Workspace", key="new_workspace"):
            st.session_state["_new_workspace"] = True
            st.rerun()
        with st.form("restore_identity"):
            supplied = st.text_input("Restore token", type="password", key="manual_restore_token")
            restore = st.form_submit_button("Restore Workspace")
        if restore:
            try:
                store.load(supplied.strip())
                _queue_restore(supplied.strip())
            except SAVE_ERRORS as exc:
                st.warning(str(exc))
        with st.form("save_workspace"):
            title = st.text_input("Workspace title", max_chars=80, key="snapshot_title")
            save = st.form_submit_button("Save Workspace")
        if save:
            try:
                if autosave_workspace(status):
                    store.save_snapshot(token, title, capture_state())
                    st.success("Workspace snapshot saved.")
            except SAVE_ERRORS as exc:
                st.warning(str(exc))
        st.markdown("**List Saved Workspaces**")
        try:
            snapshots = store.list_snapshots(token)
            if snapshots:
                labels = {row["workspace_id"]: f"{row['title']} · {row['updated_at']}" for row in snapshots}
                chosen = st.selectbox("Saved Workspace", list(labels), format_func=labels.get, key="selected_snapshot")
                selected = next(row for row in snapshots if row["workspace_id"] == chosen)
                st.caption(f"Created: {selected['created_at']} UTC · Updated: {selected['updated_at']} UTC")
                if st.button("Restore Saved Workspace"):
                    st.session_state["_pending_workspace_state"] = store.snapshot(token, chosen)
                    st.rerun()
                with st.form(f"rename_{chosen}"):
                    renamed = st.text_input("New workspace title", value=selected["title"], max_chars=80)
                    if st.form_submit_button("Rename Workspace"):
                        store.rename_snapshot(token, chosen, renamed)
                        st.rerun()
                confirm = st.checkbox("Confirm deletion of selected saved workspace", key=f"delete_confirm_{chosen}")
                if st.button("Delete Workspace", disabled=not confirm):
                    store.delete_snapshot(token, chosen, confirmed=confirm)
                    st.rerun()
            else:
                st.caption("No saved workspace snapshots.")
            if st.button("Preview abandoned Replay cleanup"):
                preview = cleanup_replays(store, token)
                st.session_state["cleanup_preview"] = preview
            if "cleanup_preview" in st.session_state:
                preview = st.session_state["cleanup_preview"]
                st.caption(f"{len(preview['files'])} owned, unreferenced, inactive databases eligible; "
                           f"{sum(item['bytes'] for item in preview['files']):,} bytes. Legacy or unknown ownership is retained.")
                confirmed = st.checkbox("Confirm deletion of abandoned Replay data", key="cleanup_confirm")
                if st.button("Clean Up Abandoned Replay Data", disabled=not confirmed):
                    result = cleanup_replays(store, token, confirmed=confirmed)
                    st.success(f"Reclaimed {result['reclaimed_bytes']:,} bytes from {len(result['files'])} databases.")
                    st.session_state.pop("cleanup_preview", None)
        except SAVE_ERRORS as exc:
            st.warning(f"Workspace operation unavailable: {exc}")
    return status, saved_stamp
