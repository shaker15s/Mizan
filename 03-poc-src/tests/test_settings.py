"""Settings store: the contract behind "the settings panel controls everything".

The cockpit renders its whole settings UI from ``SettingsStore.snapshot()``, so these
tests pin the invariants that UI depends on: every spec is reachable and typed, an
invalid value is refused with a per-key error instead of silently applied, secrets
are masked in every read path and never touch the disk, and ``reset`` returns to the
*startup* baseline (env/deploy value first, spec default second) rather than clobbering
a server owner's launch configuration.
"""

from __future__ import annotations

import json

import pytest

from poc.settings import (
    SPEC_BY_KEY,
    SettingsStore,
    all_specs,
    baseline_value,
    group_labels,
    mask_secret,
)


@pytest.fixture()
def store(tmp_path):
    return SettingsStore(path=tmp_path / "settings.json")


# --------------------------------------------------------------------- manifest


def test_manifest_is_complete_and_ui_ready():
    specs = all_specs()
    assert specs, "the settings manifest must never be empty"
    assert len({spec.key for spec in specs}) == len(specs), "duplicate setting keys"
    for spec in specs:
        assert spec.label_ar, f"{spec.key} needs an Arabic label (the UI shows it verbatim)"
        assert spec.key in SPEC_BY_KEY
        assert spec.type in {"str", "int", "float", "bool", "choice", "secret", "dict"}, spec.key
        if spec.type == "choice":
            assert spec.default in spec.choices, f"{spec.key} default must be one of its choices"
        if spec.min_value is not None:
            assert spec.min_value <= spec.default, spec.key
        if spec.max_value is not None:
            assert spec.default <= spec.max_value, spec.key
        assert spec.ui_control in {"toggle", "number", "select", "text", "password", "json"}, spec.key


def test_groups_are_labelled_and_cover_every_spec(store):
    snapshot = store.snapshot()
    grouped = {item["key"] for group in snapshot["groups"] for item in group["items"]}
    assert grouped == set(SPEC_BY_KEY), "a setting invisible in the UI is a setting nobody can fix"
    labels = group_labels()
    for group in snapshot["groups"]:
        assert group["label"] and group["icon"]
        assert group["id"] in labels


# ------------------------------------------------------------------------ reading


def test_defaults_are_typed(store):
    assert store.bool_of("agent.narrative_composer") is True
    assert store.int_of("model.max_tokens") == 2048
    assert store.float_of("model.temperature") == pytest.approx(0.2)
    assert store.get("agent.dialect") == "ar-EG"
    assert store.get("nope.not_a_setting", "fallback") == "fallback"
    assert store.int_of("nope.not_a_setting") == 0


def test_snapshot_reports_untouched_keys_as_default(store):
    item = next(row for group in store.snapshot()["groups"] for row in group["items"] if row["key"] == "model.temperature")
    assert item["is_default"] is True
    assert item["value"] == 0.2


# ------------------------------------------------------------------------ writing


def test_update_coerces_scalars(store):
    changed, errors = store.update(
        {
            "agent.narrative_composer": "false",
            "model.max_tokens": "1024",
            "model.temperature": "0.5",
        }
    )
    assert errors == {}
    assert set(changed) == {"agent.narrative_composer", "model.max_tokens", "model.temperature"}
    assert store.bool_of("agent.narrative_composer") is False
    assert store.int_of("model.max_tokens") == 1024
    assert store.float_of("model.temperature") == pytest.approx(0.5)


def test_choice_rejects_unknown_value_and_changes_nothing(store):
    before = store.get("agent.dialect")
    changed, errors = store.update({"agent.dialect": "fr-FR"})
    assert changed == {}
    assert "agent.dialect" in errors
    assert store.get("agent.dialect") == before
    assert store.version == 0, "a fully rejected update must not bump the version"


@pytest.mark.parametrize("value", [10, 99999, "abc"])
def test_number_bounds_are_enforced(store, value):
    _changed, errors = store.update({"model.max_tokens": value})
    assert "model.max_tokens" in errors


def test_unknown_key_is_reported_not_silently_dropped(store):
    changed, errors = store.update({"policy.allow_all": True})
    assert changed == {}
    assert errors["policy.allow_all"] == "إعداد غير معروف"


def test_partial_update_applies_valid_and_reports_invalid(store):
    changed, errors = store.update({"agent.memory_turns": 4, "agent.response_style": "shakespeare"})
    assert store.int_of("agent.memory_turns") == 4
    assert "agent.response_style" in errors
    assert changed == {"agent.memory_turns": 4}


def test_noop_update_does_not_bump_version_or_history(store):
    changed, errors = store.update({"agent.dialect": "ar-EG"})
    assert (changed, errors) == ({}, {})
    assert store.version == 0
    assert store.snapshot()["history"] == [], "a save that changed nothing must not look like an edit"


def test_history_records_the_actor_and_the_keys(store):
    store.update({"agent.memory_turns": 2}, actor="finance-lead")
    row = store.snapshot()["history"][0]
    assert row["actor"] == "finance-lead"
    assert "agent.memory_turns" in row["changed"]
    assert row["version"] == store.version


def test_dict_setting_roundtrips(store):
    config = {"slack": {"enabled": True, "url": "https://hooks.example/x"}}
    changed, errors = store.update({"integrations.config": config})
    assert errors == {}
    assert store.get("integrations.config")["slack"]["url"].endswith("/x")
    assert "integrations.config" in changed


# ------------------------------------------------------------------- reset & env


def test_reset_restores_spec_default(store):
    store.update({"model.temperature": 0.9})
    restored = store.reset(["model.temperature"])
    assert restored == {"model.temperature": 0.2}
    assert store.float_of("model.temperature") == pytest.approx(0.2)


def test_reset_honours_the_deploy_baseline(store, monkeypatch):
    """A server owner's env choice is not the operator's edit to undo."""
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    fresh = SettingsStore(path=store.path)
    assert fresh.get("model.provider") == "openai_compatible"
    item = next(row for group in fresh.snapshot()["groups"] for row in group["items"] if row["key"] == "model.provider")
    assert item["is_default"] is True, "the env baseline is the default for this process"
    assert SPEC_BY_KEY["model.provider"].default == "anthropic"

    fresh.update({"model.provider": "anthropic"})
    assert fresh.get("model.provider") == "anthropic"
    fresh.reset(["model.provider"])
    assert fresh.get("model.provider") == "openai_compatible"
    assert baseline_value(SPEC_BY_KEY["model.provider"]) == "openai_compatible"


def test_reset_all_keys_when_none_given(store):
    store.update({"agent.memory_turns": 1, "model.retries": 5})
    restored = store.reset()
    assert {"agent.memory_turns", "model.retries"} <= set(restored)
    assert store.int_of("agent.memory_turns") == SPEC_BY_KEY["agent.memory_turns"].default


# ----------------------------------------------------------------------- secrets


def test_secrets_are_masked_in_reads_and_never_persisted(store):
    _changed, errors = store.update({"model.api_key": "sk-super-secret"})
    assert errors == {}
    assert store.secret("model.api_key") == "sk-super-secret"
    for payload in (store.snapshot(), store.describe(), store.export()):
        assert "sk-super-secret" not in json.dumps(payload, ensure_ascii=False)
    masked = next(row for group in store.snapshot()["groups"] for row in group["items"] if row["key"] == "model.api_key")
    assert masked["value"] == mask_secret("sk-super-secret")
    assert masked["configured"] is True
    assert masked["secret"] is True
    assert "sk-super-secret" not in store.path.read_text(encoding="utf-8")


def test_clearing_a_secret_is_a_noop_and_reset_forgets_it(store):
    store.update({"model.api_key": "sk-keepme"})
    store.update({"model.api_key": ""})
    assert store.secret("model.api_key") == "sk-keepme", "an empty submit must not wipe the key"
    store.reset(["model.api_key"])
    assert store.secret("model.api_key") == ""


def test_persist_secrets_is_opt_in(tmp_path):
    """Secrets stay in memory unless the operator explicitly opts in — the flag
    exists for packaged deployments, and its default is what protects the repo."""
    guarded = SettingsStore(path=tmp_path / "guarded.json")
    guarded.update({"model.api_key": "sk-abc"})
    assert "sk-abc" not in guarded.path.read_text(encoding="utf-8")

    opted = SettingsStore(path=tmp_path / "opted.json", persist_secrets=True)
    opted.update({"model.api_key": "sk-abc"})
    assert "sk-abc" in opted.path.read_text(encoding="utf-8")
    assert SettingsStore(path=tmp_path / "opted.json", persist_secrets=True).secret("model.api_key") == "sk-abc"
    # ...but a reader that did not opt in never resurrects it from disk
    assert SettingsStore(path=tmp_path / "opted.json").secret("model.api_key") == ""


# --------------------------------------------------------------- persistence file


def test_overlay_survives_a_restart_and_rejects_garbage(store, tmp_path):
    store.update({"agent.memory_turns": 3, "model.api_key": "sk-x"})
    assert store.path.exists()
    reloaded = SettingsStore(path=store.path)
    assert reloaded.int_of("agent.memory_turns") == 3
    assert reloaded.secret("model.api_key") == "", "secrets are memory-only by default"
    assert reloaded.get("agent.dialect") == "ar-EG"

    store.path.write_text("{ this is not json", encoding="utf-8")
    resilient = SettingsStore(path=store.path)
    assert resilient.int_of("agent.memory_turns") == SPEC_BY_KEY["agent.memory_turns"].default


def test_stale_overlay_key_that_became_invalid_is_recorded_not_fatal(store):
    store.update({"model.max_tokens": 4096})
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["values"]["model.max_tokens"] = 7  # below min_value
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    reloaded = SettingsStore(path=store.path)
    assert reloaded.int_of("model.max_tokens") == SPEC_BY_KEY["model.max_tokens"].default
    assert any(row.get("action") == "overlay_rejected" for row in reloaded.snapshot()["history"])


# ------------------------------------------------------------------- listeners


def test_listener_sees_changed_keys_and_cannot_break_the_write(store):
    seen: list[tuple[str, ...]] = []
    store.subscribe(lambda _store, keys: seen.append(keys))
    store.subscribe(lambda _store, _keys: (_ for _ in ()).throw(RuntimeError("listener exploded")))
    changed, errors = store.update({"agent.slash_commands": False})
    assert errors == {} and changed == {"agent.slash_commands": False}
    assert seen == [("agent.slash_commands",)]
    assert store.bool_of("agent.slash_commands") is False
