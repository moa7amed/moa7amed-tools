"""Updates, health checks, backups and cost limits."""
import json
import os
import sqlite3
import subprocess
import sys
import unittest

from helpers import MQ, ROOT, QueueTestCase, runner_variants

from mqlib import __version__, engine, maintenance, store, util


def dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class Versions(unittest.TestCase):

    def test_versions_sort_the_way_releases_do(self):
        ordered = ["0.2.0", "0.3.0", "0.3.1-beta.1", "0.3.1-beta.2", "0.3.1-rc.1", "0.3.1",
                   "0.10.0", "1.0.0-beta.1", "1.0.0"]
        self.assertEqual(sorted(reversed(ordered), key=maintenance.parse_version), ordered)
        self.assertTrue(maintenance.is_newer("0.3.1", "0.3.1-beta.2"))
        self.assertFalse(maintenance.is_newer("0.3.1", "0.3.1"))
        self.assertFalse(maintenance.is_newer("not a version", "0.3.1"))
        self.assertEqual(maintenance.parse_version("v1.2.3"), maintenance.parse_version("1.2.3"))

    def test_channel_follows_the_version(self):
        self.assertEqual(maintenance.channel_of("1.2.3"), "stable")
        self.assertEqual(maintenance.channel_of("1.2.3-beta.4"), "beta")
        self.assertIsNone(maintenance.channel_of("latest"))

    def test_changelog_of_this_version_is_shipped_with_it(self):
        entries = maintenance.changelog_entries()
        self.assertEqual(entries[0][0], __version__)
        self.assertGreater(len(entries[0][1]), 200)
        versions = [version for version, _ in entries]
        self.assertEqual(versions, sorted(versions, key=maintenance.parse_version, reverse=True))
        self.assertEqual([version for version, _ in maintenance.changes_since("0.2.0")],
                         [version for version in versions if maintenance.is_newer(version, "0.2.0")])
        self.assertEqual(maintenance.changes_since(__version__), [])

    def test_release_declaration_matches_the_code(self):
        info = maintenance.release_info()
        self.assertEqual(info["version"], __version__)
        self.assertEqual(info["channel"], maintenance.channel_of(__version__))
        self.assertEqual(info["data_format"], store.SCHEMA_VERSION)
        self.assertIn(store.SCHEMA_VERSION, info["reads_data_formats"])
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["version"], __version__)


class Installed(QueueTestCase):
    """A fake Claude Code that reports this copy as installed from a marketplace."""

    CHANGES = "# Changelog\n\n## 9.9.0\n\n- a new thing\n\n## %s\n\n- this version\n" % __version__

    def install(self, installed=__version__, offered=None, auto_update=None, source="github",
                release="same", marketplace="moa7amed-tools", plugins=None):
        market = os.path.join(self.tmp, "market")
        folder = os.path.join(market, "plugins", "moa7amed-queue")
        os.makedirs(os.path.join(market, ".claude-plugin"), exist_ok=True)
        os.makedirs(os.path.join(folder, ".claude-plugin"), exist_ok=True)
        offered = offered or installed
        self.write(".claude-plugin/marketplace.json", json.dumps({
            "name": marketplace, "owner": {"name": "test"},
            "plugins": [{"name": "moa7amed-queue", "source": "./plugins/moa7amed-queue"}]}),
            root=market)
        self.write(".claude-plugin/plugin.json",
                   json.dumps({"name": "moa7amed-queue", "version": offered}), root=folder)
        self.write("CHANGELOG.md", self.CHANGES, root=folder)
        if release == "same":
            release = {"version": offered, "data_format": store.SCHEMA_VERSION,
                       "reads_data_formats": [store.SCHEMA_VERSION], "min_python": "3.8"}
        if release:
            self.write("release.json", json.dumps(release), root=folder)
        elif os.path.exists(os.path.join(folder, "release.json")):
            os.remove(os.path.join(folder, "release.json"))
        if plugins is None:
            plugins = [{"id": "moa7amed-queue@%s" % marketplace, "version": installed,
                        "scope": "user", "enabled": True, "installPath": ROOT,
                        "lastUpdated": "2026-11-01T10:00:00.000Z"}]
        state = {"plugins": plugins,
                 "marketplaces": [{"name": marketplace, "source": source,
                                   "repo": "moa7amed/moa7amed-tools", "installLocation": market}]}
        os.environ["FAKE_PLUGINS"] = self.write("plugins.json", json.dumps(state), root=self.tmp)
        known = {marketplace: {"source": {"source": source}, "installLocation": market,
                               "lastUpdated": "2026-11-19T08:00:00.000Z"}}
        if auto_update is not None:
            known[marketplace]["autoUpdate"] = auto_update
        self.write("plugins/known_marketplaces.json", json.dumps(known),
                   root=os.environ["CLAUDE_CONFIG_DIR"])
        return state

    def status(self, **kwargs):
        return maintenance.update_status(self.fake_claude, **kwargs)

    def plugin_commands(self):
        return [" ".join(event["argv"]) for event in self.events_of("plugin")]


class UpdateReport(Installed):

    def test_available_update_is_reported_with_its_changes_and_the_official_commands(self):
        self.install(offered="9.9.0")
        status = self.status()
        self.assertEqual(status["installed"]["version"], __version__)
        self.assertEqual((status["available_version"], status["update_available"]), ("9.9.0", True))
        self.assertEqual([entry["version"] for entry in status["whats_new"]], ["9.9.0"])
        self.assertIn("a new thing", status["whats_new"][0]["text"])
        self.assertEqual(status["compatibility"]["status"], "ok")
        self.assertEqual(maintenance.update_commands(status), [
            "moa7amed-queue backup",
            "claude plugin marketplace update moa7amed-tools",
            "claude plugin update moa7amed-queue@moa7amed-tools",
            "(restart Claude Code, or run /reload-plugins in a running session)",
            "moa7amed-queue doctor"])
        _, out, _ = self.mq("update")
        for expected in ("an update is available", "a new thing", "no runner is active",
                         "claude plugin update moa7amed-queue@moa7amed-tools",
                         "turn on auto-update"):
            self.assertIn(expected, out)

    def test_up_to_date_installation(self):
        self.install()
        status = self.status()
        self.assertEqual((status["available_version"], status["update_available"]),
                         (__version__, False))
        self.assertEqual(status["whats_new"], [])
        self.assertIn("up to date", self.mq("update")[1])

    def test_the_command_never_installs_or_updates_anything_itself(self):
        self.install(offered="9.9.0")
        self.mq("update")
        self.assertEqual(sorted(set(self.plugin_commands())),
                         ["plugin list --json", "plugin marketplace list --json"])
        self.mq("update", "--refresh")
        # Refreshing the listing is Claude Code's own command; nothing installs the plugin.
        self.assertIn("plugin marketplace update moa7amed-tools", self.plugin_commands())
        for command in self.plugin_commands():
            self.assertNotRegex(command, r"plugin (install|update|uninstall|enable|disable)\b")

    def test_refresh_is_reported_and_a_failed_refresh_is_not_hidden(self):
        state = self.install()
        self.assertTrue(self.status(refresh=True)["refreshed"])
        state["refresh_fails"] = True
        self.write("plugins.json", json.dumps(state), root=self.tmp)
        status = self.status(refresh=True)
        self.assertFalse(status["refreshed"])
        self.assertIn("refreshing the marketplace failed", " ".join(status["notes"]))

    def test_auto_update_setting_is_read_from_claude_code(self):
        self.install()
        self.assertIsNone(self.status()["auto_update"])       # third-party default: off
        self.assertIn("only after you turn that on", self.mq("update")[1])
        self.install(auto_update=True)
        self.assertIs(self.status()["auto_update"], True)
        self.assertNotIn("turn on auto-update", self.mq("update")[1])
        self.install(auto_update=False)
        self.assertIs(self.status()["auto_update"], False)
        # Declared in the user's settings instead of through the /plugin toggle.
        self.install()
        self.write("settings.json", json.dumps({"extraKnownMarketplaces": {
            "moa7amed-tools": {"source": {"source": "github"}, "autoUpdate": True}}}),
            root=os.environ["CLAUDE_CONFIG_DIR"])
        status = self.status()
        self.assertIs(status["auto_update"], True)
        self.assertTrue(status["auto_update_source"].endswith("settings.json"))
        # Claude Code reads the settings entry before the file the toggle writes.
        self.install(auto_update=False)
        self.assertIs(self.status()["auto_update"], True)

    def test_environment_that_switches_auto_update_off_is_reported(self):
        self.install(auto_update=True)
        self.assertEqual(self.status()["auto_update_switched_off_by"], [])
        os.environ["DISABLE_AUTOUPDATER"] = "1"
        self.assertEqual(self.status()["auto_update_switched_off_by"], ["DISABLE_AUTOUPDATER"])
        self.assertIn("switched off in this environment by DISABLE_AUTOUPDATER",
                      self.mq("update")[1])
        os.environ["FORCE_AUTOUPDATE_PLUGINS"] = "1"
        self.assertEqual(self.status()["auto_update_switched_off_by"], [])
        del os.environ["DISABLE_AUTOUPDATER"], os.environ["FORCE_AUTOUPDATE_PLUGINS"]
        os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "0"   # any value counts
        self.assertEqual(self.status()["auto_update_switched_off_by"],
                         ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"])

    def test_copy_that_was_not_installed_from_a_marketplace_says_so(self):
        self.install(plugins=[])
        status = self.status()
        self.assertIsNone(status["installed"])
        self.assertIsNone(status["update_available"])
        self.assertIn("not installed through a marketplace", " ".join(status["notes"]))
        self.assertNotIn("To update", self.mq("update")[1])

    def test_offer_that_cannot_read_the_data_fails_the_check(self):
        self.install(offered="9.9.0", release={"version": "9.9.0", "data_format": "3",
                                               "reads_data_formats": ["2", "3"]})
        check = self.status()["compatibility"]
        self.assertEqual(check["status"], "fail")
        self.assertIn("format", check["detail"])
        self.assertIn("[FAIL] compatibility", self.mq("update")[1])

    def test_offer_that_migrates_the_data_asks_for_a_backup(self):
        self.install(offered="9.9.0", release={"version": "9.9.0", "data_format": "2",
                                               "reads_data_formats": ["1", "2"]})
        check = self.status()["compatibility"]
        self.assertEqual(check["status"], "ok")
        self.assertIn("back up first", check["detail"])

    def test_offer_without_a_declaration_is_flagged_not_trusted(self):
        self.install(installed="9.9.0", offered="0.3.0", release=None)   # going back
        status = self.status()
        self.assertFalse(status["update_available"])
        self.assertEqual(status["compatibility"]["status"], "warn")
        self.assertIn("older than the installed version", self.mq("update")[1])

    def test_prerelease_is_offered_only_above_the_installed_release(self):
        self.install(offered=__version__ + "-beta.1")
        self.assertFalse(self.status()["update_available"])
        self.install(offered="9.9.0-beta.1")
        status = self.status()
        self.assertTrue(status["update_available"])
        self.assertEqual(status["available_channel"], "beta")

    def test_local_folder_is_not_presented_as_a_release_channel(self):
        self.install(source="directory")
        self.assertIn("local folder", " ".join(self.status()["notes"]))

    def test_plugin_still_installed_under_its_old_name_is_mentioned(self):
        state = self.install()
        state["plugins"].append({"id": "claude-task-queue@claude-task-queue", "version": "0.2.0",
                                 "scope": "user", "enabled": True, "installPath": self.tmp})
        self.write("plugins.json", json.dumps(state), root=self.tmp)
        self.assertEqual(self.status()["legacy_installed"], ["claude-task-queue@claude-task-queue"])
        self.assertIn("old name", self.mq("update")[1])

    def test_working_runner_fails_the_preflight(self):
        self.install(offered="9.9.0")
        self.add("some work")
        q = self.queue()
        engine.acquire(q, os.getpid(), None, kind="test")
        checks = {check["name"]: check for check in maintenance.preflight()}
        self.assertEqual(checks["no runner is active"]["status"], "fail")
        self.assertIn(self.project, checks["no runner is active"]["detail"])
        self.assertEqual(checks["queues are readable"]["status"], "ok")
        self.assertEqual(checks["data format"]["status"], "ok")


class Doctor(Installed):

    def doctor(self):
        code, out, _ = self.mq("doctor", "--json", check=False)
        data = json.loads(out)
        return code, data, {check["name"]: check for check in data["checks"]}

    def test_healthy_installation(self):
        self.add("some work")
        code, data, checks = self.doctor()
        self.assertEqual((code, data["healthy"]), (0, True))
        self.assertEqual(checks["queues"]["status"], "ok")
        self.assertIn("1 project queue(s), 1 task(s)", checks["queues"]["detail"])
        self.assertEqual(checks["manifest"]["status"], "ok")
        self.assertIn(__version__, checks["version"]["detail"])

    def test_damaged_queue_fails_the_check_and_names_the_project(self):
        self.add("some work")
        for q in self._queues:
            q.close()
        del self._queues[:]
        database = os.path.join(util.project_dir(self.project), "queue.db")
        with open(database, "wb") as handle:
            handle.write(b"this is not a database" * 200)
        code, data, checks = self.doctor()
        self.assertEqual((code, data["healthy"]), (1, False))
        self.assertEqual(checks["queues"]["status"], "fail")
        self.assertIn(self.project, checks["queues"]["detail"])
        self.assertEqual({check["name"]: check["status"] for check in maintenance.preflight()}
                         ["queues are readable"], "fail")

    def test_lock_of_a_runner_that_is_gone_is_a_note_not_a_failure(self):
        self.add("some work")
        engine.acquire(self.queue(), dead_pid(), None, kind="test")
        code, _, checks = self.doctor()
        self.assertEqual(code, 0)
        self.assertEqual(checks["runner locks"]["status"], "warn")
        self.assertIn("recover", checks["runner locks"]["detail"])

    def test_checking_a_machine_that_never_used_the_queue_creates_nothing(self):
        import shutil
        shutil.rmtree(self.home)
        code, data, checks = self.doctor()
        self.assertEqual((code, data["healthy"]), (0, True))
        self.assertIn("does not exist yet", checks["data directory"]["detail"])
        self.assertEqual(checks["Claude Code"]["status"], "ok")
        self.assertFalse(os.path.exists(self.home))

    def test_unknown_setting_is_pointed_out(self):
        self.set_config(no_such_setting=1)
        code, _, checks = self.doctor()
        self.assertEqual(code, 0)
        self.assertEqual(checks["settings"]["status"], "warn")
        self.assertIn("no_such_setting", checks["settings"]["detail"])

    def test_checking_changes_nothing_in_the_queue(self):
        self.add("some work")
        database = os.path.join(util.project_dir(self.project), "queue.db")
        with open(database, "rb") as handle:
            before = handle.read()
        self.doctor()
        self.mq("update")
        with open(database, "rb") as handle:
            self.assertEqual(handle.read(), before)


class Backups(QueueTestCase):

    PROMPT = u"أصلح الخطأ in the parser — café"

    def backup(self, *args):
        return self.mq_json("backup", *args)

    def test_backup_and_restore_bring_back_tasks_and_settings_exactly(self):
        first = self.add(self.PROMPT, acceptance="the parser test passes", priority=10)
        second = self.add("second task", after=[first])
        self.set_config(max_attempts=4)
        made = self.backup("--label", "before the change")
        self.assertTrue(os.path.isfile(os.path.join(made["path"], "manifest.json")))
        self.assertIn("before-the-change", made["name"])
        self.assertEqual(made["projects"][0]["tasks"], {"pending": 2})

        self.queue().cancel([first])
        self.add("a third task")
        self.set_config(max_attempts=9)
        self.assertEqual(len(self.queue().tasks()), 3)

        _, out, _ = self.mq("restore", made["name"])
        self.assertIn("Nothing was changed", out)
        self.assertEqual(len(self.queue().tasks()), 3)

        for q in self._queues:
            q.close()
        del self._queues[:]
        plan = self.mq_json("restore", made["name"], "--yes")
        self.assertTrue(plan["restored"])
        tasks = self.queue().tasks()
        self.assertEqual([(task["id"], task["status"]) for task in tasks],
                         [(first, "pending"), (second, "pending")])
        restored = self.task(first)
        self.assertEqual((restored["prompt"], restored["acceptance"], restored["priority"]),
                         (self.PROMPT, "the parser test passes", 10))
        self.assertEqual([row["id"] for row in self.queue().deps(second)], [first])
        self.assertEqual(util.effective_config()["max_attempts"], 4)
        # What was there before the restore is not lost either.
        safety = [item for item in maintenance.list_backups() if item["name"] == plan["safety_backup"]]
        self.assertEqual(safety[0]["projects"][0]["tasks"], {"cancelled": 1, "pending": 2})

    def test_restore_works_while_the_queue_is_open_elsewhere(self):
        self.add("the task that was there")
        made = self.backup()
        self.add("added after the backup")
        watcher = self.queue()            # another process looking at the queue
        self.assertEqual(len(watcher.tasks()), 2)
        plan = self.mq_json("restore", made["name"], "--yes")
        self.assertTrue(plan["restored"])
        self.assertEqual([task["prompt"] for task in watcher.tasks()], ["the task that was there"])
        leftovers = [name for name in os.listdir(util.project_dir(self.project))
                     if "restoring" in name]
        self.assertEqual(leftovers, [])

    def test_restore_also_takes_back_settings_that_did_not_exist_yet(self):
        self.add("some work")
        made = self.backup()
        self.mq("config", "set", "max_attempts", "9")            # creates the project's settings
        self.mq("config", "set", "timeout_minutes", "5", "--global")
        self.assertEqual(self.mq_json("config", "show")["max_attempts"], 9)
        for q in self._queues:
            q.close()
        del self._queues[:]
        self.mq("restore", made["name"], "--yes")
        settings = self.mq_json("config", "show")
        self.assertEqual((settings["max_attempts"], settings["timeout_minutes"]), (2, 60))

    def test_backup_and_health_check_work_in_a_folder_with_awkward_characters(self):
        os.environ["MOA7AMED_QUEUE_HOME"] = os.path.join(
            self.tmp, u"queue data #1 100% \u0642\u0627\u0626\u0645\u0629")
        self.mq("add", "a task in an oddly named place")
        code, out, _ = self.mq("doctor", check=False)
        self.assertEqual(code, 0, out)
        self.assertIn("1 project queue(s), 1 task(s)", out)
        made = self.backup()
        self.assertEqual(made["projects"][0]["tasks"], {"pending": 1})
        self.assertTrue(self.mq_json("restore", made["name"], "--yes")["restored"])

    def test_addresses_sqlite_is_given(self):
        for path, expected in ((r"C:\Users\you\queue.db", "file:///C:/Users/you/queue.db?mode=ro"),
                               ("/home/you/queue.db", "file:///home/you/queue.db?mode=ro"),
                               (r"\\server\share\q\queue.db",
                                "file:////server/share/q/queue.db?mode=ro"),
                               ("/data/a b/#1/100%/what?/queue.db",
                                "file:///data/a b/%231/100%25/what%3f/queue.db?mode=ro")):
            self.assertEqual(maintenance._sqlite_uri(path), expected)

    def test_restore_is_refused_while_a_runner_is_working(self):
        self.add("some work")
        made = self.backup()
        engine.acquire(self.queue(), os.getpid(), None, kind="test")
        code, _, err = self.mq("restore", made["name"], "--yes", check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("runner is active", err)

    def test_backup_of_a_queue_that_is_in_use_is_complete(self):
        for index in range(25):
            self.add("task %d" % index)
        self.queue()   # stays open, as a status window would
        made = self.backup()
        copy = sqlite3.connect(os.path.join(made["path"], "projects",
                                            made["projects"][0]["key"], "queue.db"))
        try:
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 25)
        finally:
            copy.close()

    def test_backups_are_listed_newest_first_and_unknown_names_are_refused(self):
        self.add("some work")
        self.assertIn("No backups yet", self.mq("backup", "--list")[1])
        one = self.backup("--label", "one")
        listed = self.mq_json("backup", "--list")
        self.assertEqual([item["name"] for item in listed], [one["name"]])
        code, _, err = self.mq("restore", "no-such-backup", "--yes", check=False)
        self.assertNotEqual(code, 0)
        self.assertIn("no backup matches", err)

    def test_backup_can_be_written_somewhere_else(self):
        self.add("some work")
        target = os.path.join(self.tmp, "elsewhere")
        made = self.backup("--output", target)
        self.assertEqual(os.path.dirname(made["path"]), target)
        self.assertTrue(self.mq_json("restore", made["path"])["projects"])


class BackupOnUpdate(QueueTestCase):

    def state(self):
        with open(os.path.join(self.home, "state.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def ran_before_as(self, version):
        with open(os.path.join(self.home, "state.json"), "w", encoding="utf-8") as handle:
            json.dump({"version": version, "since": "2026-10-01T00:00:00Z"}, handle)

    def labels(self):
        return [item.get("label") for item in maintenance.list_backups()]

    def test_first_run_after_an_update_backs_the_data_up_once(self):
        self.add("written by the previous version")
        self.ran_before_as("0.3.0")
        self.mq("status")
        self.assertEqual(self.labels(), ["auto-update-from-0.3.0"])
        state = self.state()
        self.assertEqual((state["version"], state["previous_version"]), (__version__, "0.3.0"))
        self.assertEqual(state["update_backup"], maintenance.list_backups()[0]["name"])
        self.assertEqual(state["history"][-1]["version"], "0.3.0")
        self.mq("status")
        self.mq("list")
        self.assertEqual(len(self.labels()), 1)
        self.assertIn("updated from 0.3.0", self.mq("doctor")[1])

    def test_data_of_a_version_that_kept_no_state_is_backed_up_before_it_is_touched(self):
        self.add("written by 0.3.0")
        for q in self._queues:
            q.close()
        del self._queues[:]
        database = os.path.join(util.project_dir(self.project), "queue.db")
        conn = sqlite3.connect(database)   # what a database of 0.3.0 looks like
        conn.execute("DROP TABLE process_tokens")
        conn.commit()
        conn.close()
        self.assertFalse(os.path.exists(os.path.join(self.home, "state.json")))
        self.mq("status")
        made = maintenance.list_backups()
        self.assertEqual([item["label"] for item in made], ["auto-update-from-earlier"])
        copy = sqlite3.connect(os.path.join(made[0]["path"], "projects",
                                            made[0]["projects"][0]["key"], "queue.db"))
        try:
            tables = {row[0] for row in copy.execute("SELECT name FROM sqlite_master")}
            self.assertNotIn("process_tokens", tables)    # copied as the old version left it
            self.assertEqual(copy.execute("SELECT prompt FROM tasks").fetchone()[0],
                             "written by 0.3.0")
        finally:
            copy.close()

    def test_steps_of_a_working_runner_do_not_record_a_version(self):
        # A runner that was started before an update goes on calling the copy it was
        # started from. Only its start, and commands a person runs, say which version
        # is in use here.
        self.add("some work")
        self.ran_before_as("0.3.0")
        runner = engine.acquire(self.queue(), os.getpid(), None, kind="test")
        self.mq("heartbeat", "--runner-id", runner["runner_id"])
        self.mq("claim", "--runner-id", runner["runner_id"])
        self.assertEqual(self.state()["version"], "0.3.0")
        self.assertEqual(self.labels(), [])
        self.mq("status")
        self.assertEqual(self.state()["version"], __version__)
        self.assertEqual(self.labels(), ["auto-update-from-0.3.0"])

    def test_starting_a_runner_after_an_update_backs_the_data_up_first(self):
        self.add("some work")
        self.ran_before_as("0.3.0")
        self.mq("runner-acquire", "--pid", str(os.getpid()), "--kind", "test")
        self.assertEqual(self.labels(), ["auto-update-from-0.3.0"])

    def test_backup_that_was_cut_short_is_made_on_the_next_run(self):
        self.add("some work")
        self.ran_before_as("0.3.0")
        real = maintenance.create_backup

        def cut_short(*_args, **_kwargs):
            raise KeyboardInterrupt()

        maintenance.create_backup = cut_short
        try:
            with self.assertRaises(KeyboardInterrupt):
                maintenance.note_version()
        finally:
            maintenance.create_backup = real
        self.assertEqual(self.state()["version"], "0.3.0")     # not yet recorded as updated
        self.mq("list")
        self.assertEqual(self.labels(), ["auto-update-from-0.3.0"])
        self.assertEqual(self.state()["version"], __version__)

    def test_two_copies_taking_turns_do_not_back_up_every_time(self):
        self.add("some work")
        self.ran_before_as("0.3.0")
        self.mq("status")
        for _ in range(4):
            self.ran_before_as("9.9.9")       # a copy of another version ran in between
            self.mq("status")
            self.ran_before_as("0.3.0")
            self.mq("status")
        self.assertEqual(sorted(self.labels()),
                         ["auto-update-from-0.3.0", "auto-update-from-9.9.9"])

    def test_damaged_state_file_does_not_stop_commands(self):
        self.add("some work")
        for content in ("[]", "null", '"text"', "{not json"):
            with open(os.path.join(self.home, "state.json"), "w") as handle:
                handle.write(content)
            self.mq("status")
            self.assertEqual(self.mq("doctor", check=False)[0], 0)
            self.assertEqual(self.state()["version"], __version__)

    def test_new_installation_makes_no_backup(self):
        self.mq("add", "the first task ever")
        self.mq("status")
        self.assertEqual(self.labels(), [])
        self.assertEqual(self.state()["version"], __version__)
        self.assertIsNone(self.state()["previous_version"])

    def test_it_can_be_turned_off(self):
        self.add("some work")
        self.ran_before_as("0.3.0")
        self.set_config(backup_on_update=False)
        self.mq("status")
        self.assertEqual(self.labels(), [])
        self.assertEqual(self.state()["previous_version"], "0.3.0")

    def test_captured_prompt_after_an_update_triggers_it_too(self):
        self.add("some work")
        self.ran_before_as("0.3.0")
        payload = json.dumps({"session_id": "s", "cwd": self.project,
                              "hook_event_name": "UserPromptSubmit",
                              "prompt": "queue: one more thing"}).encode("utf-8")
        result = subprocess.run([sys.executable, MQ, "hook-prompt"], input=payload,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.tmp)
        self.assertIn("Queued as task #2", result.stdout.decode("utf-8"))
        self.assertEqual(self.labels(), ["auto-update-from-0.3.0"])
        self.assertEqual(maintenance.list_backups()[0]["projects"][0]["tasks"], {"pending": 1})

    def test_only_the_newest_automatic_backups_are_kept_and_manual_ones_always(self):
        self.add("some work")
        maintenance.create_backup(label="mine")
        for index in range(maintenance.AUTOMATIC_BACKUPS_KEPT + 3):
            maintenance.create_backup(label="auto-update-from-0.0.%d" % index)
        removed = maintenance.prune_automatic_backups()
        self.assertEqual(len(removed), 3)
        labels = self.labels()
        self.assertIn("mine", labels)
        self.assertEqual(len([label for label in labels if label.startswith("auto-")]),
                         maintenance.AUTOMATIC_BACKUPS_KEPT)


class CostLimits(object):
    """Run once per runner. The fake CLI reports $0.0123 for every run."""

    def test_session_limit_stops_before_the_next_task_and_leaves_it_pending(self):
        for index in range(3):
            self.add("task %d" % index)
        self.set_config(cost_limit_run_usd=0.02)
        code, out = self.run_queue()
        self.assertEqual(code, engine.EXIT_COST_LIMIT, out)
        self.assertEqual(self.statuses(), {1: "done", 2: "done", 3: "pending"})
        self.assertIn("cost limit for one runner session has been reached", out)
        self.assertEqual(len(self.starts()), 2)
        # The limit belongs to one session: starting again continues with what is left.
        code, out = self.run_queue()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.statuses(), {1: "done", 2: "done", 3: "done"})
        self.assertEqual([task for task, _ in self.starts()], [1, 2, 3])

    def test_warnings_are_logged_once_per_task_and_once_per_session(self):
        for index in range(3):
            self.add("task %d" % index)
        self.set_config(cost_warn_task_usd=0.01, cost_warn_run_usd=0.02)
        code, out = self.run_queue()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("COST WARNING: task #"), 3)
        self.assertEqual(out.count("COST WARNING: this runner session"), 1)
        events = [event["detail"] for event in self.queue().events(limit=200)
                  if event["type"] == "cost-warning"]
        self.assertEqual(len(events), 4)
        self.assertEqual(self.statuses(), {1: "done", 2: "done", 3: "done"})


globals().update(runner_variants(CostLimits))


class CostAccounting(QueueTestCase):

    def run_python(self):
        code, out, err = self.mq("run", check=False)
        return code, out + err

    def test_no_limit_and_no_warning_unless_configured(self):
        self.add("one")
        self.add("two")
        code, out = self.run_python()
        self.assertEqual(code, 0, out)
        self.assertNotIn("COST WARNING", out)

    def test_review_calls_count_towards_the_session(self):
        for index in range(3):
            self.add("Add a small helper function number %d to utils" % index)
        # Each task: one review call and one run, $0.0123 each. Without the reviews the
        # session would be at $0.0246 after two tasks and a third would start.
        self.set_config(routing_classifier="always", cost_limit_run_usd=0.03)
        code, out = self.run_python()
        self.assertEqual(code, engine.EXIT_COST_LIMIT, out)
        self.assertEqual(len(self.events_of("review")), 2)
        self.assertEqual(self.statuses(), {1: "done", 2: "done", 3: "pending"})

    def test_run_without_a_reported_cost_adds_nothing_and_is_counted_as_such(self):
        self.add("one")
        self.add("two")
        self.script(1, "no-cost")
        self.set_config(cost_warn_task_usd=0.0001, cost_limit_run_usd=0.02)
        code, out = self.run_python()
        self.assertEqual(code, 0, out)                      # 0 + 0.0123 stays below the limit
        self.assertEqual(out.count("COST WARNING: task #"), 1)
        usage = self.mq_json("usage")
        self.assertEqual((usage["runs"], usage["runs_with_cost"], usage["runs_without_cost"]),
                         (2, 1, 1))
        self.assertAlmostEqual(usage["total_cost_usd"], 0.0123)

    def test_usage_shows_what_the_cli_reported_per_model_and_task(self):
        cheap = self.add("cheap", model="haiku")
        dear = self.add("dear", model="opus")
        self.script(dear, "cost 0.5")
        self.set_config(cost_warn_task_usd=0.4)
        code, out = self.run_python()
        self.assertEqual(code, 0, out)
        self.assertIn("COST WARNING: task #%d cost $0.5000" % dear, out)
        usage = self.mq_json("usage")
        self.assertAlmostEqual(usage["total_cost_usd"], 0.5123)
        self.assertEqual([task["task_id"] for task in usage["by_task"]], [dear, cheap])
        self.assertAlmostEqual(usage["by_model"]["claude-opus-test-1"]["cost_usd"], 0.5)
        self.assertEqual(usage["by_model"]["claude-haiku-test-1"]["runs"], 1)
        self.assertEqual(usage["by_model"]["claude-haiku-test-1"]["input_tokens"], 100)
        self.assertEqual(usage["limits"]["cost_warn_task_usd"], 0.4)
        _, text, _ = self.mq("usage")
        self.assertIn("as reported by Claude Code", text)
        self.assertIn("$0.5123", text)

    def test_zero_means_no_limit(self):
        self.add("one")
        self.add("two")
        self.set_config(cost_limit_run_usd=0, cost_warn_task_usd=0, cost_warn_run_usd=0)
        code, out = self.run_python()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.statuses(), {1: "done", 2: "done"})
        self.assertNotIn("COST WARNING", out)

    def test_session_warning_also_comes_when_a_review_call_crossed_the_level(self):
        for index in range(2):
            self.add("Add a small helper function number %d to utils" % index)
        # review + run + review = $0.0369: the level is crossed by the second review call.
        self.set_config(routing_classifier="always", cost_warn_run_usd=0.03)
        code, out = self.run_python()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("COST WARNING: this runner session"), 1)

    def test_damaged_routing_history_never_breaks_a_run(self):
        self.set_config(cost_limit_run_usd=5, cost_warn_run_usd=0.02, cost_warn_task_usd=0.01)
        damaged = ("null", '{"a": 1}', "[1, null]", '[{"type": "review", "cost_usd": "much"}]',
                   '[{"type": "review", "cost_usd": 1, "at": 5}]', "{not json")
        for index, value in enumerate(damaged, 1):
            self.mq("add", "task %d" % index)      # queued as a person would: it is routed
            q = self.queue()
            with q.tx():
                changed = q.conn.execute("UPDATE task_routing SET history=? WHERE task_id=?",
                                         (value, index)).rowcount
            self.assertEqual(changed, 1)
        code, out = self.run_python()
        self.assertEqual(code, 0, out)
        self.assertEqual(set(self.statuses().values()), {"done"})
        self.assertEqual(len(self.starts()), len(damaged))
        self.assertIn("as reported by Claude Code", self.mq("usage")[1])
        self.assertEqual(self.mq("route", "1", check=False)[0], 0)

    def test_usage_for_a_period_counts_only_the_reviews_of_that_period(self):
        self.mq("add", "an old task")
        q = self.queue()
        with q.tx():
            changed = q.conn.execute(
                "UPDATE task_routing SET history=? WHERE task_id=1", (json.dumps([
                    {"type": "review", "cost_usd": 0.5, "at": "2020-01-01T00:00:00Z"},
                    {"type": "review", "cost_usd": 0.25, "at": util.now_iso()}]),)).rowcount
        self.assertEqual(changed, 1)
        self.assertAlmostEqual(self.mq_json("usage")["review_cost_usd"], 0.75)
        self.assertAlmostEqual(self.mq_json("usage", "--days", "7")["review_cost_usd"], 0.25)

    def test_stop_for_cost_is_distinguishable_from_a_workspace_stop(self):
        self.add("one")
        self.write("untracked.txt", "user work in progress\n")
        code, out = self.run_python()
        self.assertEqual(code, engine.EXIT_WORKSPACE_STOP, out)
        self.assertNotEqual(engine.EXIT_COST_LIMIT, engine.EXIT_WORKSPACE_STOP)
        self.assertEqual(self.read("untracked.txt"), "user work in progress\n")


if __name__ == "__main__":
    unittest.main()
