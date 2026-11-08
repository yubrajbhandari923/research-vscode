import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "packages", "core"))
sys.path.insert(0, os.path.join(ROOT, "packages", "cli"))


_GIT_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


@pytest.fixture(scope="session")
def _launcher_dir(tmp_path_factory):
    """A `research` launcher on PATH, as an installed CLI would provide (tests that spawn `research` got exit 127 without one)."""
    d = tmp_path_factory.mktemp("launcher")
    exe = d / "research"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m research_cli "$@"\n')
    exe.chmod(0o755)
    return str(d)


@pytest.fixture(scope="session", autouse=True)
def _reap_daemons(tmp_path_factory):
    """Tests that start a supervisor (plan_resume, Telegram /resume) leave a detached daemon behind; stop every one under the session tmp root."""
    yield
    import signal
    base = str(tmp_path_factory.getbasetemp())
    # Scan processes, not daemon.json: a test may delete its project (and the pid file) while the daemon lives on.
    for line in subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if "research.supervisord" in cmd and "--root " + base in cmd:
            root = cmd.split("--root ", 1)[1].split(" ")[0]
            try:
                os.makedirs(os.path.join(root, ".research", "supervisor"), exist_ok=True)
                open(os.path.join(root, ".research", "supervisor", "stop"), "w").write("stop\n")
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path_factory, _launcher_dir):
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "RESEARCH_AGENT", "RESEARCH_AGENT_MODEL", "RESEARCH_AUTHOR_TYPE",
              "RESEARCH_ROOT", "RESEARCH_RUN_DIR", "RESEARCH_RUN_ID", "RESEARCH_IN_JOB", "CODEX_SANDBOX",
              "CODEX_SANDBOX_NETWORK_DISABLED", "RESEARCH_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    # Point the machine-level config layer at an empty directory. Without this, the developer's own
    # ~/.config/research-os/config.yaml leaks into every test — a real cluster profile there makes
    # "no HPC configured" tests fail. A test that wants the layer sets XDG_CONFIG_HOME itself.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-empty")))
    env_pp = os.pathsep.join([os.path.join(ROOT, "packages", "core"), os.path.join(ROOT, "packages", "cli")])
    for k, v in _GIT_ID.items():   # CI has no git identity
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", _launcher_dir + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("PYTHONPATH", env_pp + os.pathsep + os.environ.get("PYTHONPATH", ""))


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    (d / "train.py").write_text("print('hi')\n")
    git(d, "add", ".")
    git(d, "commit", "-qm", "init")
    monkeypatch.chdir(d)
    return d


@pytest.fixture
def proj(repo):
    from research.store import Project
    p = Project.init(str(repo), name="demo", goal="Understand alpha")
    yield p
    p.close()


# --------------------------------------------------------------------------- shared project templates
#
# Building a realistic project costs seconds (git calls, SQLite writes, mirror rendering), and the big
# fixtures were being rebuilt for every test that used them: ~14s x 26 tests in test_fullreport alone.
# A template is built once per session and copied per test instead, which is a directory copy.

def clone_project(template: str, dest: str):
    """Copy a built project template to `dest` and open it. Each test gets its own mutable copy."""
    import shutil
    from research.store import Project
    shutil.copytree(template, dest, symlinks=True)
    # research.db records absolute paths in `mirrors`; rebuilding from the text files is the supported
    # way to relocate a project, and is far cheaper than recreating the records.
    p = Project(dest, sync=False)
    p.rebuild_index(backup=False)
    return p


@pytest.fixture(scope="session")
def _template_root(tmp_path_factory):
    return tmp_path_factory.mktemp("templates")


@pytest.fixture(scope="session")
def built_template(_template_root):
    """Build a named project template once per session.

    Usage in a test module:

        @pytest.fixture
        def rich(built_template, tmp_path):
            return clone_project(built_template("rich", _build_rich), str(tmp_path / "proj"))
    """
    import subprocess as _sp
    cache = {}

    def build(name, builder):
        if name in cache:
            return cache[name]
        d = os.path.join(str(_template_root), name)
        os.makedirs(d)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        _sp.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True, env=env)
        with open(os.path.join(d, "train.py"), "w") as f:
            f.write("print('hi')\n")
        _sp.run(["git", "add", "."], cwd=d, check=True, env=env)
        _sp.run(["git", "commit", "-qm", "init"], cwd=d, check=True, env=env)
        cwd = os.getcwd()
        os.chdir(d)
        try:
            from research.store import Project
            p = Project.init(d, name=name, goal=f"template: {name}")
            try:
                builder(p)
            finally:
                p.close()
        finally:
            os.chdir(cwd)
        cache[name] = d
        return d
    return build


# --------------------------------------------------------------------------- markers (assigned here, so test files stay untouched)
_DAEMON_FILES = ("test_cli_e2e", "test_supervisor", "test_autopromote")   # prefix match: also test_supervisor_fixes
_PTY_FILES = ("test_cli_pin_link_escalation", "test_cli_e2e")
_SLOW_FILES = ("test_acceptance", "test_guide_quarto", "test_fullreport")
# Tests over ~2 s in the serial `--durations=0` run, as "module::test name".
SLOW_TESTS = {
    "test_agent_features::test_cli_hooks_usage_pin_and_check",
    "test_agent_features::test_hook_timeout_failure_and_advisory",
    "test_agent_features::test_strategy_aware_agent_files",
    "test_bootstrap::test_apply_refuses_an_existing_project_and_invalid_files",
    "test_bootstrap::test_yaml_and_wizard_produce_the_same_project_and_export_round_trips",
    "test_cli_pin_link_escalation::test_a_wrong_result_is_not_an_incomplete_deliverable_and_escalation_is_bounded",
    "test_cli_pin_link_escalation::test_agent_shells_get_a_clear_message_and_are_never_locked_out",
    "test_cli_pin_link_escalation::test_first_protected_action_without_a_pin_creates_one_instead_of_silently_passing",
    "test_cli_pin_link_escalation::test_incomplete_deliverable_escalates_the_repair_attempt_to_the_stronger_model",
    "test_cli_pin_link_escalation::test_install_links_research_into_main_runs_from_there_and_stays_out_of_git",
    "test_cli_pin_link_escalation::test_link_never_replaces_real_files_and_unlink_removes_only_its_own_link",
    "test_cli_pin_link_escalation::test_mismatched_new_pin_creates_nothing_and_authorizes_nothing",
    "test_cli_pin_link_escalation::test_no_escalation_unless_configured_or_for_other_failures",
    "test_cli_pin_link_escalation::test_no_link_inside_experiment_worktrees_and_the_launcher_template_is_symlink_safe",
    "test_cli_pin_link_escalation::test_no_terminal_fails_closed_without_counting_a_wrong_attempt",
    "test_cli_pin_link_escalation::test_set_pin_prompts_twice_without_echo_then_every_protected_action_asks",
    "test_command_examples::test_example_parses",
    "test_config::test_cli_config_surface",
    "test_environment::test_configured_python_drives_the_shim_checks_and_agent_path",
    "test_environment::test_wizard_plans_and_creates_the_environment",
    "test_guide::test_a_human_owned_results_chapter_receives_proposals_not_edits",
    "test_guide::test_a_prepared_draft_runs_and_the_check_passes_within_the_limits",
    "test_guide::test_a_prepared_draft_that_overruns_is_caught_by_the_check",
    "test_guide::test_a_supported_review_logs_automatically_and_auto_log_can_be_turned_off",
    "test_guide::test_cite_add_list_and_check",
    "test_guide::test_cli_flow",
    "test_guide::test_draft_check_enforces_words_sections_figures_and_human_chapters",
    "test_guide::test_draft_done_validates_ids_and_clears_the_reminder",
    "test_guide::test_draft_prepares_a_bounded_brief_and_records_a_baseline",
    "test_guide::test_draft_with_nothing_to_draft_says_so",
    "test_guide::test_highlight_rules",
    "test_guide::test_log_adds_one_entry_per_result_without_identifiers_and_registers_provenance",
    "test_guide::test_log_copies_and_registers_the_result_plot_and_respects_max_figures",
    "test_guide::test_log_is_idempotent_never_rewrites_and_a_deleted_entry_stays_deleted",
    "test_guide::test_mcp_report_and_guide_tools",
    "test_guide::test_missing_citations_are_errors_unused_are_listed_and_crossrefs_are_not_citations",
    "test_guide::test_rpc_guide_methods_and_what_is_read_only",
    "test_guide::test_status_and_overview_describe_the_guide",
    "test_guide::test_triggers_shape_the_reminder_only",
    "test_guide_review::test_cannot_approve_over_provenance_errors_without_recorded_override",
    "test_guide_review::test_guide_draft_requires_the_briefing_skill",
    "test_guide_review::test_human_approval_is_separate_and_bound_to_the_text",
    "test_guide_review::test_proposals_for_human_owned_sections_are_listed_and_not_rendered",
    "test_guide_review::test_researcher_panels_show_review_and_approval_state_separately",
    "test_guide_review::test_review_changes_no_record_and_history_is_appended",
    "test_guide_review::test_review_goes_outdated_when_cited_evidence_changes",
    "test_guide_review::test_review_request_requires_and_injects_the_briefing_skill",
    "test_guide_review::test_review_validation",
    "test_guide_review::test_rpc_and_mcp_guide_views_are_read_only",
    "test_guide_review::test_unreviewed_by_default_and_review_is_not_approval",
    "test_guide_review::test_unsound_review_blocks_approval",
    "test_hpc::test_bundle_formats",
    "test_hpc::test_doctor_explains_an_unreachable_cluster_without_crashing",
    "test_hpc::test_doctor_failures_all_carry_an_actionable_fix",
    "test_hpc::test_failed_remote_job_is_recorded_as_failed",
    "test_hpc::test_identical_code_is_not_uploaded_twice_and_snapshots_stay_immutable",
    "test_hpc::test_project_directories_are_isolated",
    "test_hpc::test_remote_manifests_can_reconstruct_execution_state",
    "test_hpc::test_results_and_code_are_never_deleted",
    "test_hpc::test_submit_runs_and_records_the_job",
    "test_hpc::test_sync_handles_a_job_still_queued",
    "test_hpc::test_sync_is_idempotent",
    "test_hpc::test_sync_reconciles_and_collects",
    "test_hpc::test_sync_recovers_from_a_lost_submission_reply_without_resubmitting",
    "test_hpc::test_sync_survives_an_unreachable_cluster",
    "test_hpc::test_sync_waits_when_slurm_accounting_is_unavailable",
    "test_initproject::test_an_agent_running_the_wizard_cannot_approve_the_starter_hooks_and_nothing_fails",
    "test_initproject::test_guide_choices_from_the_wizard_are_saved_and_can_be_declined",
    "test_initproject::test_hpc_job_environment_is_saved_as_slurm_setup",
    "test_initproject::test_layout_one_repo_and_root_of_main",
    "test_initproject::test_new_experiments_use_configured_root_and_mcp_reachable_from_worktree",
    "test_initproject::test_new_projects_get_a_guide_on_main_with_a_publish_workflow",
    "test_initproject::test_new_projects_load_skills_hooks_and_mcp_by_default",
    "test_initproject::test_refuses_nonempty_targets_and_bad_input",
    "test_initproject::test_starter_hooks_and_skill_links_can_be_declined_and_added_later",
    "test_initproject::test_starter_hooks_really_run",
    "test_initproject::test_workspace_file_and_project_resolves_from_every_root",
    "test_intervention::test_a_halt_sends_one_message_with_the_debug_summary_and_options_then_reports_recovery",
    "test_intervention::test_a_plan_that_is_moving_is_not_reported_as_halted",
    "test_intervention::test_a_rejected_approval_changes_nothing",
    "test_intervention::test_a_restart_in_the_middle_of_implementing_applies_each_action_exactly_once",
    "test_intervention::test_a_role_contradiction_is_fixed_by_one_protected_approval_and_the_task_resumes",
    "test_intervention::test_a_second_open_intervention_for_the_same_task_is_refused_and_agents_cannot_instruct",
    "test_intervention::test_choose_turns_a_debug_option_into_an_intervention_and_plain_text_goes_to_the_only_blocked_task",
    "test_intervention::test_cli_intervention_approve_asks_for_the_pin_in_a_real_terminal",
    "test_intervention::test_cli_surfaces_exist_and_fail_clearly",
    "test_intervention::test_engineering_repair_grants_attempts_only_with_approval_and_guides_the_next_attempt",
    "test_intervention::test_ensure_running_starts_the_supervisor_for_telegram_or_a_running_plan_and_replaces_an_old_one",
    "test_intervention::test_frozen_test_changes_are_always_protected_and_never_edit_files",
    "test_intervention::test_halt_notice_without_the_debug_agent_and_no_telegram_means_no_network",
    "test_intervention::test_queued_instruction_waits_for_the_running_attempt_and_interrupt_stops_it_safely",
    "test_intervention::test_scientific_amendment_needs_one_pin_approval_then_resumes_and_keeps_the_evidence",
    "test_intervention::test_status_says_halted_when_nothing_moves_and_waiting_for_approval_when_only_approval_is_pending",
    "test_intervention::test_telegram_commands_report_status_and_route_instructions_but_never_approve_protected_changes",
    "test_intervention::test_unprotected_instructions_apply_without_a_pin_and_the_agent_sees_the_instruction",
    "test_intervention::test_unusable_planner_output_fails_closed_and_changes_nothing",
    "test_localbin::test_init_creates_runnable_gitignored_launcher",
    "test_localbin::test_run_exec_through_launcher_can_import_research",
    "test_orchestrate::test_setup_idempotent_preserves_user_files",
    "test_packs::test_advisory_hooks_do_not_count_as_a_verifier",
    "test_packs::test_cli_catalog_packs_install_enable_and_upgrade",
    "test_packs::test_wizard_installs_chosen_packs_hooks_and_links_then_a_small_workflow_runs",
    "test_plans_tasks_interfaces::test_rpc_plan_task_methods",
    "test_promotion::test_a_candidate_that_changes_after_approval_must_be_re_approved",
    "test_promotion::test_an_unsuccessful_experiment_stays_documented_when_its_candidate_is_abandoned",
    "test_promotion::test_cli_lists_and_agents_cannot_approve_through_it",
    "test_promotion::test_failing_tests_block_review_and_approval",
    "test_promotion::test_integration_is_refused_when_main_moved_or_is_dirty_and_never_forces",
    "test_promotion::test_prepare_by_paths_and_unsafe_selections",
    "test_promotion::test_prepare_takes_only_the_selected_commits_on_top_of_the_current_main",
    "test_promotion::test_scientific_evidence_gate_blocks_approval_until_a_finding_is_supported",
    "test_promotion::test_tests_only_count_for_the_exact_clean_revision_they_ran_against",
    "test_promotion::test_the_five_questions_are_reported_separately",
    "test_promotion::test_the_whole_path_to_main_needs_independent_review_and_a_human",
    "test_provenance::test_archived_figure_must_match_the_registered_artifact",
    "test_provenance::test_cli_claim_figure_validate_roundtrip",
    "test_provenance::test_failed_run_cannot_support",
    "test_provenance::test_unreviewed_evidence_is_a_warning_label_preliminary",
    "test_providers::test_cli_dispatch_really_launches_in_the_worktree",
    "test_providers::test_continue_with_another_provider_keeps_records_and_gates_still_apply",
    "test_providers::test_cross_provider_strategy_routes_levels_to_different_providers",
    "test_providers::test_failed_provider_is_failed_not_completed_and_prepared_is_single_use",
    "test_providers::test_headless_captures_logs_and_a_second_launch_of_the_same_task_is_refused",
    "test_providers::test_launch_runs_in_the_worktree_and_brief_is_readable",
    "test_providers::test_missing_executable_is_a_diagnosed_failure_and_dispatch_names_it",
    "test_providers::test_rpc_providers_and_prepare_never_runs",
    "test_providers::test_stop_marks_cancelled_not_failed",
    "test_release_1_0::test_killed_writer_recovers_to_one_coherent_state",
    "test_release_1_0::test_only_one_process_executes_a_task",
    "test_release_safety::test_corrupt_jsonl_cannot_silently_disappear",
    "test_release_safety::test_corrupt_ledger_never_resets_frozen_tests",
    "test_release_safety::test_hook_timeout_validation_before_mutation",
    "test_release_safety::test_rebuild_failure_preserves_index_and_files",
    "test_research_os::test_agents_cannot_self_promote_findings",
    "test_research_os::test_checks_gate_completion_for_agents_warn_humans",
    "test_research_os::test_claims_release_and_next",
    "test_research_os::test_cli_new_commands",
    "test_research_os::test_compare_runs",
    "test_research_os::test_mcp_protocol_and_tools",
    "test_research_os::test_reviews_survive_rebuild",
    "test_research_os::test_search_and_current_budget",
    "test_research_os::test_skills_from_git_url",
    "test_research_os::test_skills_import_link_match_remove",
    "test_research_os::test_stale_tasks_reach_home_and_current_md",
    "test_research_os::test_start_keeps_role_and_warns_when_not_ready",
    "test_research_os::test_sweep_budget_and_runs",
    "test_research_os::test_verify_moves_verify_tasks",
    "test_setup::test_new_experiment_full_flow_with_workspace_task_profile_and_prepared_agent",
    "test_slurm_rpc_cli::test_cli_budget_exit_code_for_agents",
    "test_slurm_rpc_cli::test_cli_full_flow",
    "test_strategy::test_cli_strategy",
    "test_strategy::test_direct_dispatch_matches_orchestrated_and_explicit_wins",
    "test_strategy::test_setup_agents_leaves_custom_agents_alone",
    "test_workspace::test_dispatch_runs_in_the_linked_workspace_and_says_so",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        mod = item.module.__name__
        if mod.startswith(_DAEMON_FILES):
            item.add_marker(pytest.mark.daemon)
        if mod in _PTY_FILES:
            item.add_marker(pytest.mark.pty)
        if mod in _SLOW_FILES or f"{mod}::{item.originalname}" in SLOW_TESTS:
            item.add_marker(pytest.mark.slow)
