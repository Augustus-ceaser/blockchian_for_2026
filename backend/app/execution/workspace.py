from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from uuid import UUID


class WorkspaceSecurityError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionWorkspace:
    root: Path
    input: Path
    work: Path
    output: Path
    logs: Path
    manifests: Path


class ExecutionWorkspaceManager:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def create(self, run_id: UUID) -> ExecutionWorkspace:
        run_root = self._safe_child(str(run_id))
        run_root.mkdir(mode=0o700, parents=False, exist_ok=False)
        paths = [run_root / name for name in ("input", "work", "output", "logs", "manifests")]
        for path in paths:
            path.mkdir(mode=0o700)
        try:
            os.chmod(paths[0], 0o500)
        except OSError:
            pass
        return ExecutionWorkspace(run_root, *paths)

    def _safe_child(self, name: str) -> Path:
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise WorkspaceSecurityError("workspace child name is invalid")
        candidate = self._root / name
        if candidate.is_symlink() or candidate.resolve(strict=False).parent != self._root:
            raise WorkspaceSecurityError("workspace path escapes its root")
        return candidate

    def resolve_member(self, workspace: ExecutionWorkspace, area: str, name: str) -> Path:
        base = getattr(workspace, area, None)
        if not isinstance(base, Path) or not name or Path(name).is_absolute():
            raise WorkspaceSecurityError("workspace member is invalid")
        candidate = base / name
        if any(part == ".." for part in Path(name).parts):
            raise WorkspaceSecurityError("path traversal is forbidden")
        if candidate.is_symlink() or base.is_symlink():
            raise WorkspaceSecurityError("symbolic links are forbidden")
        resolved = candidate.resolve(strict=False)
        if resolved.parent != base.resolve():
            raise WorkspaceSecurityError("workspace member escapes its area")
        return candidate

    def cleanup(self, workspace: ExecutionWorkspace) -> None:
        if workspace.root.parent.resolve() != self._root or workspace.root.is_symlink():
            raise WorkspaceSecurityError("refusing unsafe workspace cleanup")
        for path in (workspace.input, workspace.work, workspace.output, workspace.logs, workspace.manifests):
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        shutil.rmtree(workspace.root)
