"""Pin and export the complete implementation without changing the user's index."""
import os
from pathlib import Path
import subprocess
import tempfile


def git(repo, *args, env=None):
    result = subprocess.run(["git", "-C", str(repo), *args], env=env,
                            capture_output=True, check=True)
    return result.stdout


def capture(repo):
    with tempfile.TemporaryDirectory(prefix="cross-review-index-") as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))
        staged_tree = git(repo, "write-tree").decode().strip()
        git(repo, "read-tree", staged_tree, env=env)
        git(repo, "add", "-A", "--", ".", env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()
    return {"head": git(repo, "rev-parse", "HEAD").decode().strip(), "tree": tree}


def begin(repo):
    repo = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Pre-existing dirty state is unsupported; preserve it and start from a clean worktree.")
    state = capture(repo)
    return {"repo": str(repo), "baseline": state["head"], "initial": state}


def snapshot(session, destination):
    repo = session["repo"]
    state = capture(repo)
    diff = git(repo, "diff", "--binary", session["baseline"], state["tree"]).decode("utf-8", "replace")
    if not diff:
        raise ValueError("The implementation diff is empty.")
    source = destination / "source"
    source.mkdir()
    for entry in git(repo, "ls-tree", "-rz", state["tree"]).split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        if kind != "blob":
            raise ValueError("Submodules are unsupported; review their changes separately.")
        path = source / os.fsdecode(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Symlinks are represented by their stored target text, never followed.
        path.write_bytes(git(repo, "cat-file", "blob", oid))
        path.chmod(0o444)
    (destination / "implementation.patch").write_text(diff)
    return dict(state, baseline=session["baseline"], diff=diff,
                source="source", symlinks="Stored target text; not followed")


def unchanged(session, reviewed):
    current = capture(session["repo"])
    return all(current[key] == reviewed[key] for key in ("head", "tree"))
