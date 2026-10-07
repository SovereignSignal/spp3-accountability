"""publish() against a real git origin that other writers push to.

Since 2026-10-03 master has three writers: the VM cron, the hourly Grails
job and the daily ledger job. The old publish() pushed without pulling, so
every VM push after the first bot commit was rejected and stayed local
while the job reported success. These tests drive publish() against a
temporary bare origin that has moved ahead, the exact production state.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import stream_monitor as M


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args), check=True,
                          capture_output=True, text=True).stdout.strip()


def clone(origin, dest):
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True,
                   capture_output=True)
    git(dest, "config", "user.name", "test")
    git(dest, "config", "user.email", "test@example.invalid")
    return dest


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class PublishTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.origin = base / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/master")
        seed = base / "seed"
        seed.mkdir()
        git(seed, "init", "-q")
        git(seed, "config", "user.name", "test")
        git(seed, "config", "user.email", "test@example.invalid")
        git(seed, "checkout", "-q", "-b", "master")
        write(seed / "data" / "status.json", "{}\n")
        write(seed / "data" / "grails.json", "{}\n")
        git(seed, "add", ".")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "remote", "add", "origin", str(self.origin))
        git(seed, "push", "-q", "origin", "master")
        self.vm = clone(self.origin, base / "vm")
        self.bot = clone(self.origin, base / "bot")
        notify = mock.patch("notify.send", return_value=True)
        self.sent = notify.start()
        self.addCleanup(notify.stop)

    def bot_pushes(self, rel, text, msg="bot"):
        write(self.bot / rel, text)
        git(self.bot, "commit", "-q", "-am", msg)
        git(self.bot, "push", "-q", "origin", "master")

    def origin_log(self):
        return git(self.origin, "log", "--format=%s", "master").splitlines()

    def test_rebases_onto_moved_origin_and_pushes(self):
        self.bot_pushes("data/grails.json", '{"n": 1}\n', "bot refresh")
        status = self.vm / "data" / "status.json"
        write(status, '{"block": 1}\n')
        result = M.publish(status, "status healthy", root=self.vm)
        self.assertEqual(result, M.PUSHED)
        self.assertEqual(self.origin_log(), ["status healthy", "bot refresh", "seed"])
        self.assertEqual(git(self.vm, "rev-list", "--count", "origin/master..HEAD"), "0")
        self.sent.assert_not_called()

    def test_conflict_aborts_rebase_keeps_commit_local_and_alerts(self):
        self.bot_pushes("data/status.json", '{"block": "bot"}\n')
        status = self.vm / "data" / "status.json"
        write(status, '{"block": "vm"}\n')
        result = M.publish(status, "status healthy", root=self.vm)
        self.assertEqual(result, M.LOCAL)
        self.assertNotIn("status healthy", self.origin_log())
        self.assertFalse((self.vm / ".git" / "rebase-merge").exists())
        self.assertFalse((self.vm / ".git" / "rebase-apply").exists())
        self.assertEqual(git(self.vm, "log", "-1", "--format=%s"), "status healthy")
        self.sent.assert_called_once()
        self.assertIn("not pushed", self.sent.call_args[0][0])

    def test_nothing_to_commit_is_unchanged(self):
        status = self.vm / "data" / "status.json"
        self.assertEqual(M.publish(status, "noop", root=self.vm), M.UNCHANGED)
        self.assertEqual(self.origin_log(), ["seed"])

    def test_commit_is_scoped_to_its_own_path(self):
        write(self.vm / "data" / "grails.json", '{"operator": "edit"}\n')
        git(self.vm, "add", "data/grails.json")
        status = self.vm / "data" / "status.json"
        write(status, '{"block": 2}\n')
        self.assertEqual(M.publish(status, "status", root=self.vm), M.PUSHED)
        changed = git(self.vm, "show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertEqual(changed, ["data/status.json"])
        # The operator's edit survives the autostash, uncommitted.
        self.assertEqual(git(self.vm, "diff", "HEAD", "--name-only"), "data/grails.json")
        self.assertEqual((self.vm / "data" / "grails.json").read_text(), '{"operator": "edit"}\n')

    def test_detached_head_never_pushes(self):
        git(self.vm, "checkout", "-q", "--detach")
        status = self.vm / "data" / "status.json"
        write(status, '{"block": 3}\n')
        self.assertEqual(M.publish(status, "status", root=self.vm), M.LOCAL)
        self.assertEqual(self.origin_log(), ["seed"])
        self.sent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
