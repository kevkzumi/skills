"""Pin implementation ownership without changing the user's index or worktree."""
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


def git(repo, *args, env=None):
    result = subprocess.run(["git", "-C", str(repo), *args], env=env,
                            capture_output=True, check=True)
    return result.stdout


def entries(repo, tree):
    result = {}
    for entry in git(repo, "ls-tree", "-rz", tree).split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        if kind != "blob":
            raise ValueError("Submodules are unsupported; review their changes separately.")
        result[os.fsdecode(path)] = (mode, oid)
    return result


def changed(left, right):
    return {path for path in left.keys() | right.keys() if left.get(path) != right.get(path)}


def capture(repo):
    flags = git(repo, "ls-files", "-v", "-z").split(b"\0")
    if any(item and (item[:1].islower() or item.startswith(b"S ")) for item in flags):
        raise ValueError("Hidden assume-unchanged or skip-worktree entries prevent reliable scope capture.")
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    with tempfile.TemporaryDirectory(prefix="cross-review-index-") as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))
        staged_tree = git(repo, "write-tree").decode().strip()
        git(repo, "read-tree", staged_tree, env=env)
        git(repo, "add", "-A", "--", ".", env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()
    entries(repo, tree)
    return {"head": head, "tree": tree, "index": staged_tree}


def owned(path, paths):
    return paths is None or any(path == root or path.startswith(root + "/") for root in paths)


def begin(repo, baseline=None, paths=None):
    repo = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if paths is not None:
        paths = list(dict.fromkeys(paths))
        if not paths or any(not path or path == "." or str(PurePosixPath(path)) != path or
                            PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or
                            ".git" in PurePosixPath(path).parts for path in paths):
            raise ValueError("Ownership paths must be explicit repository-relative files or directories without traversal.")
    state = capture(repo)
    head = entries(repo, state["head"])
    dirty = changed(head, entries(repo, state["tree"])) | changed(head, entries(repo, state["index"]))
    if dirty and paths is None:
        raise ValueError("Dirty starts require explicit --path ownership; preserve existing changes.")
    overlaps = sorted(path for path in dirty if owned(path, paths) or
                      any(root.startswith(path + "/") for root in (paths or [])))
    if overlaps:
        raise ValueError("Ambiguous ownership overlaps pre-existing changes: " + ", ".join(overlaps))
    baseline = git(repo, "rev-parse", "--verify", (baseline or state["head"]) + "^{commit}").decode().strip()
    git(repo, "merge-base", "--is-ancestor", baseline, state["head"])
    entries(repo, baseline)
    return {"repo": str(repo), "baseline": baseline, "initial": state,
            "paths": paths, "preserved_paths": sorted(dirty)}


def scoped_state(session):
    repo = session["repo"]
    state = capture(repo)
    git(repo, "merge-base", "--is-ancestor", session["baseline"], state["head"])
    initial = session["initial"]
    for key in ("head", "tree", "index"):
        before = entries(repo, initial[key])
        now = entries(repo, state[key])
        if any(before.get(path) != now.get(path) for path in session.get("preserved_paths", [])):
            raise ValueError("Pre-existing work changed since baseline; ownership is ambiguous. Preserve work and resolve scope.")
    baseline = entries(repo, session["baseline"])
    current = entries(repo, state["tree"])
    paths = sorted(path for path in changed(baseline, current) if owned(path, session.get("paths")))
    with tempfile.TemporaryDirectory(prefix="cross-review-scope-") as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))
        git(repo, "read-tree", session["baseline"], env=env)
        for path in paths:
            git(repo, "update-index", "--force-remove", "--", path, env=env)
        for path in paths:
            if path in current:
                mode, oid = current[path]
                git(repo, "update-index", "--add", "--cacheinfo", mode, oid, path, env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()
    head = entries(repo, state["head"])
    commit_paths = sorted(path for path in changed(head, current) if owned(path, session.get("paths")))
    return dict(state, tree=tree, paths=paths, commit_paths=commit_paths)


def snapshot(session, destination):
    repo = session["repo"]
    state = scoped_state(session)
    diff = git(repo, "diff", "--binary", session["baseline"], state["tree"]).decode("utf-8", "replace")
    if not diff:
        raise ValueError("The implementation diff is empty.")
    source = destination / "source"
    source.mkdir()
    for name, (mode, oid) in entries(repo, state["tree"]).items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        # Symlinks are represented by their stored target text, never followed.
        path.write_bytes(git(repo, "cat-file", "blob", oid))
        path.chmod(0o444)
    (destination / "implementation.patch").write_text(diff)
    return dict(state, baseline=session["baseline"], diff=diff,
                source="source", symlinks="Stored target text; not followed")


def unchanged(session, reviewed):
    try:
        current = scoped_state(session)
    except (ValueError, subprocess.CalledProcessError):
        return False
    return all(current[key] == reviewed[key] for key in ("head", "tree"))
