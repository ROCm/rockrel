#!/usr/bin/env python3
"""
ROCm TheRock – Release Tag Automation Tool
---------------------------------------------

Automates tag and GitHub release creation for TheRock plus every tracked
submodule. It supports both token-backed HTTPS remotes (via `gh auth login
--with-token`) and SSH.

High-level workflow:
1. Reuse (or populate) a cached clone under a configurable directory
    (default: `/tmp/rock-tagging-cache`, overridable via `--cache-dir`),
    fetch the latest refs, and hard-reset to the user-specified commit.
2. Update submodules via `fetch_sources.py` when available (fallback to
    `git submodule update`) and build a plan by combining `.gitmodules`
    metadata with `git submodule status` output. Repos listed in
    `--exclude-list` and repos outside the ROCm GitHub org are skipped.
3. For each component (inside a single loop):
    a. Configure an authenticated `rocm-github` remote (token or SSH).
    b. Create an annotated tag (`therock-<version>`) at the recorded
       commit, skipping components where the tag already exists.
    c. For mono-repos (`rocm-libraries`, `rocm-systems`), generate
       tarballs for the `projects/` and `shared/` directories (tarballs
       are created even in dry-run mode).
    d. When not in dry-run mode, push the tag and invoke
       `gh release create` with the appropriate notes and tarball assets.

Use `--force-clone` to delete and reclone when the cache directory exists
but is not a valid git repo. All actions are logged for traceability, and
dry-run mode (the default) lets you preview the plan without touching
remotes.
"""

import argparse
import logging
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, List, Any


class RockTagging:
    """Automates tagging and release uploading for TheRock."""

    # Constants for mono-repos that need tarballs
    MONO_REPOS = frozenset({"rocm-libraries", "rocm-systems"})

    def __init__(self, cli_args: argparse.Namespace) -> None:
        # Collect CLI options
        self.release_branch: str | None = cli_args.branch_name
        self.api_token: str | None = cli_args.apitoken
        self.release_version: str | None = cli_args.release_version
        self.dry_run = cli_args.dry_run
        self.commitid: str | None = cli_args.commitid
        self.exclude_list: set[str] = set(cli_args.exclude_list or [])
        self.force_clone: bool = cli_args.force_clone
        self.cache_dir: Path | None = Path(cli_args.cache_dir) if cli_args.cache_dir else None
        self.rock_url: str = "https://github.com/ROCm/TheRock.git"
        self.cache_root: Path | None = None

        # Configure structured logging
        self._logger = logging.getLogger("rock_tagging")
        if not self._logger.handlers:
            logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

        mode = "API token mode" if self.api_token else "SSH mode"
        self.log(f"Authentication Mode: {mode}")
        self.log(f"Dry run mode = {self.dry_run}")
        if self.exclude_list:
            self.log(f"Exclude list: {self.exclude_list}")

    def log(self, msg: str) -> None:
        """Common method for logging info messages."""
        self._logger.info(msg)

    def exec(self, args: list[str | Path], cwd: Path, *, input: bytes | None = None, stream: bool = False) -> None:
        """
        Executes subprocess commands.
        If stream=True, prints stdout/stderr live (useful for git clone/submodule update).
        Otherwise, buffers output and logs when finished.
        """

        def mask(text: str | bytes) -> str:
            if isinstance(text, bytes):
                text = text.decode(errors="ignore")
            if not text or not self.api_token:
                return text or ""
            return text.replace(self.api_token, "***")

        cmd = args if isinstance(args, list) else [args]
        masked_cmd = mask(shlex.join(map(str, cmd)))
        self.log(f"++ Exec [{cwd}]$ {masked_cmd}")
        sys.stdout.flush()

        if stream:
            # STREAM OUTPUT LIVE
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            for line in process.stdout:
                self.log(mask(line.rstrip()))

            ret = process.wait()
            if ret != 0:
                raise subprocess.CalledProcessError(ret, cmd)

            return

        # NORMAL (buffered) MODE
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                shell=False,
                input=input,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                stdin=None if input else subprocess.DEVNULL,
                text=False,
            )

            if result.stdout:
                self.log(mask(result.stdout))
            if result.stderr:
                self.log(mask(result.stderr))

        except subprocess.CalledProcessError as exc:
            self.log(mask(exc.stdout or b""))
            self.log(mask(exc.stderr or b""))
            raise

    def run_command_output(self, args: list[str | Path], cwd: Path) -> str:
        """
        Run a command and return its stripped stdout as a string.
        Raises CalledProcessError on non-zero exit.
        """
        cmd = args if isinstance(args, list) else [args]
        masked_cmd = shlex.join(map(str, cmd))
        if self.api_token:
            masked_cmd = masked_cmd.replace(self.api_token, "***")
        self.log(f"++ Exec [{cwd}]$ {masked_cmd}")

        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
        )
        return result.stdout.strip()

    def convert_to_ssh(self, url: str) -> str:
        """Convert https://github.com/X/Y.git → git@github.com:X/Y.git"""
        if url.startswith("https://github.com/"):
            path = url.replace("https://github.com/", "")
            return f"git@github.com:{path}"
        return url

    def tokenize_url(self, url: str) -> str:
        """Return original HTTPS URL when token is available, else SSH URL."""
        if self.api_token:
            return url.replace("https://", f"https://{self.api_token}@")
        else:
            # SSH mode → convert URL to SSH
            return self.convert_to_ssh(url)

    def get_submodule_url_map(self, repo_dir: Path) -> Dict[str, str]:
        """Return mapping of submodule working-tree paths → remote URLs from .gitmodules."""
        gitmodules_path = repo_dir / ".gitmodules"
        if not gitmodules_path.exists():
            return {}

        try:
            path_entries = self.run_command_output(
                [
                    "git", "config", "--file", str(gitmodules_path),
                    "--get-regexp", r"submodule\..*\.path",
                ],
                cwd=repo_dir,
            )
        except subprocess.CalledProcessError:
            return {}

        url_map: Dict[str, str] = {}
        for line in path_entries.splitlines():
            # Each line is from `git config --get-regexp` and looks like:
            #   "submodule.external/hipcc.path external/hipcc"
            # We split into key="submodule.external/hipcc.path" and
            # path_value="external/hipcc", then strip the trailing ".path"
            # to get section="submodule.external/hipcc" for the URL lookup.
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            key, path_value = parts
            section = key.rsplit(".", 1)[0]
            try:
                url = self.run_command_output(
                    [
                        "git", "config", "--file", str(gitmodules_path),
                        "--get", f"{section}.url",
                    ],
                    cwd=repo_dir,
                )
            except subprocess.CalledProcessError:
                self.log(f"No URL entry for {section}; skipping")
                continue
            url_map[path_value.strip()] = url

        return url_map

    # ------------------------------------------------------
    # plan creation
    # ------------------------------------------------------

    def build_plan_from_rock_submodules(self) -> Dict[str, Dict[str, str]]:
        """
        Build the tagging execution plan.

        1. Clone (or reuse cached clone of) TheRock.
        2. Check out and hard-reset to ``self.commitid``.
        3. Populate submodules via ``fetch_sources.py`` (or ``git submodule update``).
        4. Read ``git submodule status`` and ``.gitmodules`` to collect each
           submodule's commit SHA, remote URL, and local path.
        5. Return a dict keyed by repo name, including TheRock itself.
        """
        cache_root = self.cache_dir or Path(tempfile.gettempdir()) / "rock-tagging-cache"
        cache_root.mkdir(parents=True, exist_ok=True)
        clone_dir = cache_root / "TheRock"
        self.cache_root = cache_root

        # Ensure cache directory contains a valid git repo; otherwise reclone
        needs_clone = not clone_dir.exists()
        if not needs_clone and not (clone_dir / ".git").exists():
            if not self.force_clone:
                raise RuntimeError(
                    f"Cache directory {clone_dir} exists but is not a git repo. "
                    "Use --force-clone to delete it and reclone."
                )
            self.log(f"Cache directory {clone_dir} is not a git repo; removing before reclone (--force-clone)")
            shutil.rmtree(clone_dir)
            needs_clone = True

        if needs_clone:
            self.log(f"Cloning TheRock repo from {self.rock_url} into {clone_dir}")
            self.exec(
                ["git", "clone", str(self.rock_url), str(clone_dir)],
                cwd=cache_root,
                stream=True,
            )
        else:
            # Repo exists – ensure it's a valid git repo and has the right remote
            self.log(f"Reusing existing TheRock repo at {clone_dir}")

            # Verify remote URL matches expected
            try:
                remote_url = self.run_command_output(
                    ["git", "remote", "get-url", "origin"],
                    cwd=clone_dir,
                )
                if "TheRock" not in remote_url:
                    raise RuntimeError(
                        f"Existing repo at {clone_dir} does not look like TheRock (origin={remote_url})"
                    )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"Failed to inspect existing repo at {clone_dir}: {exc}") from exc

            # Update refs from origin
            self.log("Fetching latest changes for existing TheRock clone...")
            self.exec(["git", "fetch", "origin", "--prune", "--recurse-submodules=on-demand"],
                      cwd=clone_dir, stream=True)

        fetch_script = clone_dir / "build_tools" / "fetch_sources.py"
        rock_commit = self.commitid

        # Hard-reset the working tree to the exact requested commit.
        self.log(f"Checking out TheRock at commit {rock_commit}")
        self.exec(["git", "checkout", rock_commit], cwd=clone_dir)
        self.exec(["git", "reset", "--hard", rock_commit], cwd=clone_dir)

        if fetch_script.exists():
            self.log("Updating submodules via fetch_sources.py (jobs=10, no patches)...")
            self.exec(
                ["python3", str(fetch_script), "--jobs", "10", "--no-apply-patches"],
                cwd=clone_dir,
                stream=True,
            )
        else:
            self.log("fetch_sources.py not found; falling back to git submodule update")
            self.exec(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=clone_dir,
                stream=True,
            )

        # Read submodule SHAs at this commit
        self.log("Reading submodule status...")
        try:
            status_output = self.run_command_output(
                ["git", "submodule", "status"],
                cwd=clone_dir,
            )
            lines = status_output.split("\n") if status_output else []
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to read submodule status: {exc}") from exc

        # Parse .gitmodules to map paths → URLs
        url_map = self.get_submodule_url_map(clone_dir)

        plan: Dict[str, Dict[str, str]] = {}

        # Each line from `git submodule status` looks like:
        #   " <sha> <path> (<describe>)"  or  "-<sha> <path>" (not initialized)
        # The leading character is ' ' (in-sync), '+' (changed), or '-' (not init).
        for line in lines:
            if not line:
                continue

            parts = line.split()
            if len(parts) < 2:
                continue
            sha = parts[0].lstrip("-+")
            path = parts[1]

            repo_name = Path(path).name
            repo_url = url_map.get(path)

            if not repo_url:
                self.log(f"No URL found for submodule {path} in .gitmodules")
                continue

            # Skip repos in the exclude list
            if repo_name in self.exclude_list:
                self.log(f"Skipping {repo_name} (in exclude list)")
                continue

            # Skip repos not under the ROCm organization
            url_lower = repo_url.lower()
            if "github.com/rocm/" not in url_lower and "github.com:rocm/" not in url_lower:
                self.log(f"Skipping {repo_name} (not a ROCm org repo: {repo_url})")
                continue

            plan[repo_name] = {
                "url": repo_url,
                "commit": sha,
                "path": str(clone_dir / path),
            }

        # Add TheRock itself to the plan
        plan["TheRock"] = {
            "url": self.rock_url,
            "commit": rock_commit,
            "path": str(clone_dir),
        }

        return plan


    # ------------------------------------------------------
    # Tarball creation
    # ------------------------------------------------------

    def create_tarballs(self, root_dir: Path, source_dir: Path, tarball_paths: List[Path], label: str) -> None:
        if not source_dir.is_dir():
            self.log(f"Source directory not found for {label}: {source_dir}")
            return

        self.log(f"Creating tarballs for {label}")
        for entry in sorted(source_dir.iterdir()):
            if entry.name.startswith(".") or not entry.is_dir():
                continue

            tarball_path = root_dir / f"{entry.name}.tar.gz"
            if tarball_path in tarball_paths:
                continue

            try:
                with tarfile.open(tarball_path, "w:gz") as tf:
                    tf.add(str(entry), arcname=entry.name)
                tarball_paths.append(tarball_path)
                self.log(f"Tarball created: {tarball_path}")
            except Exception as exc:
                self.log(f"Failed creating tarball for {entry.name}: {exc}")

    # ------------------------------------------------------
    # Tagging and release process
    # ------------------------------------------------------

    def execute_plan(self, plan: Dict[str, Dict[str, str]]) -> None:
        """
        Core workflow:
        - Configure remotes
        - Create tags
        - create GitHub releases and tarball artifacts
        """
        successful_components: Dict[str, Dict[str, str]] = {}
        failed_components: Dict[str, Dict[str, Any]] = {}
        work_dir = self.cache_root or Path(tempfile.gettempdir())
        work_dir.mkdir(parents=True, exist_ok=True)
        self.log(f"Working directory: {work_dir}")

        # -------- GH authentication only when token exists --------
        if self.api_token:
            try:
                self.exec(["gh", "--version"], cwd=work_dir)
                self.exec(
                    ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
                    cwd=work_dir,
                    input=self.api_token.encode(),
                )
            except subprocess.CalledProcessError as exc:
                self.log(f"GitHub CLI authentication failed: {exc}")
                self.log("Continuing with git operations (may fail if not authenticated via SSH)...")
        else:
            self.log("Skipping gh auth login (SSH mode). Assuming user already authenticated.")


        for comp, meta in plan.items():
            url = meta["url"]
            commit = meta["commit"]

            repo_dir = Path(meta["path"])

            # Setup authenticated remote (token or SSH)
            remote_url = self.tokenize_url(url)
            try:
                # Prefer updating existing remote to avoid remove/add races
                self.exec(
                    ["git", "remote", "set-url", "rocm-github", remote_url],
                    cwd=repo_dir,
                )
            except subprocess.CalledProcessError:
                try:
                    self.exec(
                        ["git", "remote", "add", "rocm-github", remote_url],
                        cwd=repo_dir,
                    )
                except subprocess.CalledProcessError as exc:
                    failed_components[comp] = {"error": f"Remote setup failed: {exc}"}
                    continue

            # Tag creation
            tag_name = (
                f"therock-{self.release_version}"
                if self.release_version
                else None
            )
            if tag_name:
                # Skip if tag already exists locally
                tag_exists = subprocess.run(
                    ["git", "rev-parse", "-q", "--verify", tag_name],
                    cwd=repo_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode == 0
                if tag_exists:
                    self.log(f"Tag {tag_name} already exists for {comp}; skipping creation")
                    successful_components[comp] = {
                        "url": url,
                        "commit": commit,
                        "tag": tag_name,
                        "skipped": "already exists",
                    }
                    continue
                try:
                    self.exec(
                        ["git", "tag", "-a", tag_name, commit, "-m", f"therock release v{self.release_version}"],
                        cwd=repo_dir,
                    )
                    if not self.dry_run:
                        self.exec(
                            ["git", "push", "rocm-github", f"{tag_name}:refs/tags/{tag_name}"],
                            cwd=repo_dir,
                        )
                    successful_components[comp] = {
                        "url": url,
                        "commit": commit,
                        "tag": tag_name,
                    }
                except subprocess.CalledProcessError as exc:
                    failed_components[comp] = {"error": f"Tag failed: {exc}"}
                    continue
            else:
                self.log(f" Skipping tag creation for {comp} (no tag_name specified)")

            # Tarballs only for mono repos
            tarballs = []
            if comp in self.MONO_REPOS:

                self.create_tarballs(repo_dir, repo_dir / "projects", tarballs, "projects")
                self.create_tarballs(repo_dir, repo_dir / "shared", tarballs, "shared")

            if self.dry_run:
                self.log(f"[DRY RUN] Would create release with: {tarballs}")
            elif tag_name:  # Only create release if tag was created
                try:
                    release_cmd = [
                        "gh", "release", "create", tag_name,
                        "--notes", f"therock release v{self.release_version}",
                        *[str(p) for p in tarballs]
                    ]
                    self.exec(release_cmd, cwd=repo_dir)
                except subprocess.CalledProcessError as exc:
                    failed_components[comp] = {"error": f"Release creation failed: {exc}"}
        from pprint import pformat as _pformat
        self.log(f"Summary: {len(successful_components)} succeeded, {len(failed_components)} failed out of {len(plan)} repos")
        if successful_components:
            self.log(f"Successful components: {_pformat(successful_components)}")
        if failed_components:
            self.log(f"Failed components: {_pformat(failed_components)}")

    # ------------------------------------------------------

    def main(self) -> None:
        plan = self.build_plan_from_rock_submodules()
        self.log(f"Execution plan: {plan}")
        self.execute_plan(plan)


def parse_args():
    parser = argparse.ArgumentParser(description="Rock Tagging Automation Tool")
    parser.add_argument("-B", "--branch_name", required=True)
    parser.add_argument("-V", "--release_version", required=True, help="Release version string")
    parser.add_argument("-C", "--commitid", required=True, help="Commit ID of TheRock")
    parser.add_argument("-A", "--apitoken", required=False, help="GitHub API token (optional)")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    parser.add_argument(
        "--exclude-list", nargs="*", default=[],
        help="List of submodule repo names to exclude from tagging"
    )
    parser.add_argument(
        "--force-clone", action="store_true", default=False,
        help="Delete and reclone if cache directory exists but is not a valid git repo"
    )
    parser.add_argument(
        "--cache-dir", default=None,
        help="Directory to cache the TheRock clone (default: /tmp/rock-tagging-cache)"
    )
    parser.set_defaults(dry_run=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    RockTagging(args).main()
