"""Codex current-output pointer compatibility in lib/topics.py.

Synthetic fixtures in a temp dir only: no real jobs, no Sandy state.
Run: python3 -m unittest discover -s tests -v
"""
import json, os, sys, tempfile, time, unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import topics  # noqa: E402

OLD = time.time() - 30 * 86400          # far older than RECENT_S


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name))
        self.home, self.jobs = root / "home", root / "home/jobs"
        self.outside = root / "outside"
        self.jobs.mkdir(parents=True)
        self.outside.mkdir()
        self.saved = (topics.HOME, topics.JOBS, topics.PARENT_CACHE_FILE, dict(topics.PARENT_CACHE))
        topics.HOME, topics.JOBS = self.home, self.jobs
        topics.PARENT_CACHE_FILE = self.home / ".cache/agent-org-parents.json"
        topics.PARENT_CACHE.clear()

    def tearDown(self):
        topics.HOME, topics.JOBS, topics.PARENT_CACHE_FILE, cache = self.saved
        topics.PARENT_CACHE.clear()
        topics.PARENT_CACHE.update(cache)
        self.tmp.cleanup()

    def job(self, name, prompt=True):
        d = self.jobs / name
        d.mkdir()
        if prompt:
            (d / "PROMPT.md").write_text("# synthetic task\n")
        return d

    def attempt(self, job, recent="line\n", suffix="a1b2c3"):
        a = job / f".codex-attempt-{suffix}"
        a.mkdir()
        if recent is not None:
            (a / "recent.log").write_text(recent)
        return a

    def pointer(self, job, attempt=None, recent=None, **extra):
        doc = {"schema": 1, "attempt_dir": str(attempt), "recent_log": recent or str(Path(attempt) / "recent.log"),
               "index": str(Path(attempt) / "index.json"), "runner_pid": 4242, "started": "2026-10-10T00:00:00Z",
               "legacy_log_not_written_by_this_attempt": True}
        doc.update(extra)
        (job / topics.CODEX_POINTER).write_text(json.dumps(doc))

    def age(self, d):
        for p in [d, *d.rglob("*")]:
            os.utime(p, (OLD, OLD), follow_symlinks=False)


class PointerValidation(Fixture):
    def test_valid_absolute_and_relative(self):
        j = self.job("j-20260101")
        a = self.attempt(j)
        self.pointer(j, a)
        self.assertEqual(topics.codex_pointer(j), (str(a), str(a / "recent.log")))
        self.pointer(j, a.name, recent=f"{a.name}/recent.log")
        self.assertEqual(topics.codex_pointer(j), (str(a), str(a / "recent.log")))

    def test_refused_pointers(self):
        j = self.job("j-20260101")
        a = self.attempt(j)
        other = self.job("other-20260101")
        oa = self.attempt(other)
        (self.outside / ".codex-attempt-zz").mkdir()
        (self.outside / ".codex-attempt-zz/recent.log").write_text("SECRET OUTSIDE\n")
        bad = {
            "not json": "{nope",
            "list": json.dumps([1]),
            "schema 2": json.dumps({"schema": 2, "attempt_dir": str(a), "recent_log": str(a / "recent.log")}),
            "no schema": json.dumps({"attempt_dir": str(a), "recent_log": str(a / "recent.log")}),
            "non-str": json.dumps({"schema": 1, "attempt_dir": 5, "recent_log": str(a / "recent.log")}),
            "wrong prefix": json.dumps({"schema": 1, "attempt_dir": str(j), "recent_log": str(j / "recent.log")}),
            "other job": json.dumps({"schema": 1, "attempt_dir": str(oa), "recent_log": str(oa / "recent.log")}),
            "outside": json.dumps({"schema": 1, "attempt_dir": str(self.outside / ".codex-attempt-zz"),
                                   "recent_log": str(self.outside / ".codex-attempt-zz/recent.log")}),
            "dotdot attempt": json.dumps({"schema": 1, "attempt_dir": f"../{j.name}/{a.name}",
                                          "recent_log": f"../{j.name}/{a.name}/recent.log"}),
            "recent elsewhere": json.dumps({"schema": 1, "attempt_dir": str(a), "recent_log": str(a / "segments.log")}),
            "recent escapes": json.dumps({"schema": 1, "attempt_dir": str(a),
                                          "recent_log": str(a / "../../../outside/.codex-attempt-zz/recent.log")}),
            "nested attempt": json.dumps({"schema": 1, "attempt_dir": str(a / ".codex-attempt-x"),
                                          "recent_log": str(a / ".codex-attempt-x/recent.log")}),
            "too big": json.dumps({"schema": 1, "attempt_dir": str(a), "recent_log": str(a / "recent.log"),
                                   "pad": "x" * topics.CODEX_POINTER_MAX}),
        }
        for why, txt in bad.items():
            with self.subTest(why):
                (j / topics.CODEX_POINTER).write_text(txt)
                self.assertIsNone(topics.codex_pointer(j))

    def test_symlinks_refused(self):
        j = self.job("j-20260101")
        real = self.outside / ".codex-attempt-zz"
        real.mkdir()
        (real / "recent.log").write_text("SECRET OUTSIDE\n")
        # symlinked attempt dir inside the job pointing outside
        (j / ".codex-attempt-link").symlink_to(real)
        self.pointer(j, j / ".codex-attempt-link")
        self.assertIsNone(topics.codex_pointer(j))
        # symlinked pointer file
        (j / topics.CODEX_POINTER).unlink()
        a = self.attempt(j)
        good = self.outside / "pointer.json"
        good.write_text(json.dumps({"schema": 1, "attempt_dir": str(a), "recent_log": str(a / "recent.log")}))
        (j / topics.CODEX_POINTER).symlink_to(good)
        self.assertIsNone(topics.codex_pointer(j))
        # symlinked recent.log: pointer is fine, tail is refused
        (j / topics.CODEX_POINTER).unlink()
        (a / "recent.log").unlink()
        (a / "recent.log").symlink_to(real / "recent.log")
        self.pointer(j, a)
        self.assertIsNotNone(topics.codex_pointer(j))
        self.assertIsNone(topics.codex_current_tail(topics.codex_pointer(j)))

    def test_tail_missing_empty_oversized(self):
        j = self.job("j-20260101")
        a = self.attempt(j, recent=None)
        self.pointer(j, a)
        ptr = topics.codex_pointer(j)
        self.assertIsNotNone(ptr)
        self.assertIsNone(topics.codex_current_tail(ptr))           # missing
        (a / "recent.log").write_text("")
        self.assertEqual(topics.codex_current_tail(ptr)[1].st_size, 0)  # empty but ordinary
        with open(a / "recent.log", "wb") as fh:
            fh.truncate(topics.CODEX_RECENT_MAX + 1)
        self.assertIsNone(topics.codex_current_tail(ptr))           # over 2 MiB
        self.assertIsNone(topics.codex_current_tail(None))

    def test_null_and_bool_schema_refused(self):
        j = self.job("j-20260101")
        a = self.attempt(j)
        self.pointer(j, a, schema=True)
        self.assertIsNone(topics.codex_pointer(j))
        self.pointer(j, str(a) + "\0")
        self.assertIsNone(topics.codex_pointer(j))

    def test_attempt_replaced_before_tail_open(self):
        j = self.job("j-20260101")
        a = self.attempt(j)
        self.pointer(j, a)
        ptr = topics.codex_pointer(j)
        a.rename(j / "retained")
        a.symlink_to(self.outside, target_is_directory=True)
        (self.outside / "recent.log").write_text("outside")
        self.assertIsNone(topics.codex_current_tail(ptr, read=True))

    def test_tail_replaced_before_open(self):
        j = self.job("j-20260101")
        a = self.attempt(j)
        self.pointer(j, a)
        ptr = topics.codex_pointer(j)
        (a / "recent.log").unlink()
        (self.outside / "secret").write_text("outside")
        (a / "recent.log").symlink_to(self.outside / "secret")
        self.assertIsNone(topics.codex_current_tail(ptr, read=True))

    def test_replaced_tail_after_open_reads_pinned_inode(self):
        j = self.job("j-20260101")
        a = self.attempt(j, recent="original\n")
        self.pointer(j, a)
        ptr = topics.codex_pointer(j)
        real_open = topics.os.open
        def race(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            if path == "recent.log":
                (a / "recent.log").unlink()
                (self.outside / "secret").write_text("outside")
                (a / "recent.log").symlink_to(self.outside / "secret")
            return fd
        with patch.object(topics.os, "open", side_effect=race):
            self.assertEqual(topics.codex_current_tail(ptr, read=True)[2], b"original\n")


class ScanAndEngine(Fixture):
    def test_pointer_only_engine(self):
        j = self.job("p-20260101")
        self.pointer(j, self.attempt(j))
        fs = topics.scan_jobs(set())["p-20260101"]
        self.assertIn(topics.CODEX_POINTER, fs["files"])
        self.assertEqual(topics.engine_from_files("p-20260101", fs["files"]), "codex")

    def test_malformed_pointer_ignored(self):
        j = self.job("m-20260101")
        (j / topics.CODEX_POINTER).write_text("{broken")
        fs = topics.scan_jobs({"m-20260101"})["m-20260101"]
        self.assertNotIn(topics.CODEX_POINTER, fs["files"])
        self.assertIsNone(topics.engine_from_files("m-20260101", fs["files"]))

    def test_current_tail_keeps_job_active(self):
        j = self.job("old-20200101")
        a = self.attempt(j)
        self.pointer(j, a)
        self.age(j)
        self.assertNotIn("old-20200101", topics.scan_jobs(set()))   # everything old: stale
        now = time.time()
        os.utime(a / "recent.log", (now, now))                     # only the tail is fresh
        fs = topics.scan_jobs(set())["old-20200101"]
        self.assertGreaterEqual(fs["last_activity"], int(now) - 1)

    def test_escaped_tail_not_counted(self):
        j = self.job("esc-20200101")
        real = self.outside / ".codex-attempt-zz"
        real.mkdir()
        (real / "recent.log").write_text("x")
        self.pointer(j, real)
        self.age(j)
        self.assertNotIn("esc-20200101", topics.scan_jobs(set()))
        fs = topics.scan_jobs({"esc-20200101"})["esc-20200101"]
        self.assertLess(fs["last_activity"], time.time() - topics.RECENT_S)
        self.assertNotIn(topics.CODEX_POINTER, fs["files"])

    def test_missing_tail_falls_back(self):
        j = self.job("mt-20260101")
        self.pointer(j, self.attempt(j, recent=None))
        fs = topics.scan_jobs(set())["mt-20260101"]
        self.assertEqual(topics.engine_from_files("mt-20260101", fs["files"]), "codex")

    def test_legacy_log_unchanged(self):
        j = self.job("BookHost-20260101")
        (j / ".codex-attempt.log").write_text("legacy codex logger\n")
        fs = topics.scan_jobs(set())["BookHost-20260101"]
        self.assertIn(".codex-attempt.log", fs["files"])
        self.assertNotIn(topics.CODEX_POINTER, fs["files"])
        self.assertEqual(topics.engine_from_files("BookHost-20260101", fs["files"]), "codex")


class Parents(Fixture):
    def run_search(self, child):
        topics.search_parents({child: {"unit": None, "last_activity": int(time.time())}})
        return topics.PARENT_CACHE[child]

    def test_launch_line_in_current_tail(self):
        p = self.job("parent-20260101")
        a = self.attempt(p, recent="$ systemd-run --user --unit child-x -- run-codex jobs/child-x-20260101/ \n")
        self.pointer(p, a)
        time.sleep(0.05)
        self.job("child-x-20260101")
        got = self.run_search("child-x-20260101")
        self.assertEqual(got["job"], "parent-20260101")
        self.assertEqual(got["evidence"], f"systemd-run launch line in parent-20260101/{a.name}/recent.log")

    def test_prose_is_not_a_parent(self):
        p = self.job("parent-20260101")
        a = self.attempt(p, recent="Task: look at jobs/child-y-20260101 and summarise child-y output.\n")
        self.pointer(p, a)
        (p / ".codex-attempt.log").write_text("PROMPT mentions child-y-20260101 several times\n")
        self.job("child-y-20260101")
        self.assertNotEqual(self.run_search("child-y-20260101").get("kind"), "job")

    def test_escaped_tail_not_searched(self):
        p = self.job("parent-20260101")
        real = self.outside / ".codex-attempt-zz"
        real.mkdir()
        (real / "recent.log").write_text("systemd-run --user --unit child-z -- run-codex jobs/child-z-20260101/\n")
        self.pointer(p, real)
        self.job("child-z-20260101")
        self.assertNotEqual(self.run_search("child-z-20260101").get("kind"), "job")

    def test_legacy_launch_line_still_found(self):
        p = self.job("tao-trader-20260101")
        (p / ".codex-attempt.log").write_text("systemd-run --user --unit child-l -- run-codex jobs/child-l-20260101/\n")
        self.job("child-l-20260101")
        got = self.run_search("child-l-20260101")
        self.assertEqual(got["job"], "tao-trader-20260101")
        self.assertEqual(got["evidence"], "systemd-run launch line in tao-trader-20260101/.codex-attempt.log")


if __name__ == "__main__":
    unittest.main()
