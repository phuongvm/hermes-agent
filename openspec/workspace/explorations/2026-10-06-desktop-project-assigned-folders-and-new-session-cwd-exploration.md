# Exploration: Hermes Desktop Project Assigned Folders Visibility & New Session CWD Resolution

**Date:** 2026-10-06  
**Status:** Completed (Exploration Phase)  
**Author:** Commander & Hermes Agent  
**Topic:** Hermes Desktop Left Sidebar Project Hierarchy, Assigned Non-Git Folders Visibility, and Subfolder New Session CWD Binding  
**Target Change:** `desktop-project-folder-lanes-and-session-cwd`

---

## 1. Context & Problem Statement

In the Hermes Desktop application, **Projects** (stored in `$HERMES_HOME/projects.db`) serve as top-level workspaces grouping repositories, folder paths, lanes, and chat sessions. A project can be configured with a `primary_path` and a set of assigned folders (`project_folders`), which may be standalone Git repositories (Level 2) or nested non-git subdirectories (Level 3 folder lanes) within a parent repository.

### The Concrete Pain Points Reported by Commander:
1. **Assigned Folders Are Invisible in Project View**:
   - When a user selects a project in the left sidebar (e.g. `INSPIRE-ROBOTICS`, whose primary folder is `O:\workspaces\devops-support\inspire-robotics`), the project view **fails to display the assigned folder**.
   - Instead of displaying `devops-support/inspire-robotics`, it displays a synthetic fallback row: **`⌂ main >`** pointing to the parent repository root **`O:/workspaces`**.
   - The user has zero visual confirmation of which folders belong to the project.
2. **Inability to Create New Sessions with the Correct Working Directory**:
   - When clicking the `+` button inside the project view (adjacent to the `main` row), the desktop creates a session pinned to the parent repository root (`O:\workspaces`).
   - Consequently, the chat session operates in `O:\workspaces`, and the right sidebar (Files Panel) browses `O:\workspaces` instead of the project's intended workspace (`O:\workspaces\devops-support\inspire-robotics`).

---

## 2. Root Cause Analysis (Code Autopsy)

Deep inspection of both backend (`tui_gateway/project_tree.py`) and frontend (`apps/desktop/src/app/chat/sidebar/projects/`) reveals four coupled flaws causing this behavior:

### Flaw 1: `_seed_folder_repos` Collapses Non-Git Project Folders to Parent Git Root
- **File**: `oss/hermes-agent/tui_gateway/project_tree.py:320-340`
- **Mechanism**:
  ```python
  def _seed_folder_repos(repos: list[dict], folders: list[dict], resolve: Optional[Resolve]) -> list[dict]:
      seen = {_path_key(v) for repo in repos for v in (repo.get("id"), repo.get("path")) if v}
      seeded = list(repos)
      for folder in folders or []:
          raw = _field(folder, "path")
          if not raw:
              continue
          info = resolve(raw) if resolve else None
          root = (info or {}).get("repo_root") or raw.rstrip("/\\")
          root_key = _path_key(root)
          if not root_key or root_key in seen:
              continue
          seeded.append(_repo_node(root, base_name(root) or root))
          seen.add(root_key)
  ```
- **The Defect**:
  When `raw = "O:/workspaces/devops-support/inspire-robotics"`:
  1. `resolve(raw)` runs Git inspection on the path.
  2. Because `devops-support/inspire-robotics` is inside the `workspaces` Git repository, `resolve()` returns `repo_root = "O:/workspaces"`.
  3. `root` becomes `"O:/workspaces"`, discarding the folder path `devops-support/inspire-robotics` entirely!
  4. It seeds a repository node for `O:/workspaces` with empty groups (`groups = []`).
  5. In the UI, a repo with empty groups falls back to synthesizing the trunk branch `main` at `O:/workspaces`.

---

### Flaw 2: `_subfolder_under` Truncates Deep Nested Paths to the First Direct Child
- **File**: `oss/hermes-agent/tui_gateway/project_tree.py:188-201`
- **Mechanism**:
  ```python
  def _subfolder_under(cwd: str, root: str) -> Optional[tuple[str, str]]:
      cwd_segs = _comparison_segments(cwd)
      root_segs = _comparison_segments(root)
      if len(cwd_segs) > len(root_segs) and cwd_segs[:len(root_segs)] == root_segs:
          raw_segs = _segments(cwd)
          subfolder = raw_segs[len(root_segs)]  # <-- Only takes 1st segment after root!
          if subfolder not in (".git", ".worktrees"):
              folder_path = (cwd.rstrip("/\\") if len(raw_segs) == len(root_segs) + 1
                             else "/".join(raw_segs[:len(root_segs) + 1]))
              return folder_path, subfolder
      return None
  ```
- **The Defect**:
  When a session has `cwd = "O:/workspaces/devops-support/inspire-robotics"` and `root = "O:/workspaces"`:
  - `root_segs` = `['o', 'workspaces']` (len = 2)
  - `raw_segs[len(root_segs)]` = `raw_segs[2]` = `'devops-support'`
  - It constructs `folder_path = "O:/workspaces/devops-support"`, completely lopping off `/inspire-robotics`!
  - Multiple distinct sub-projects (`devops-support/inspire-robotics`, `devops-support/optimality`) are erroneously collapsed into a single lane named `folder: devops-support`.

---

### Flaw 3: Frontend `EnteredProjectContent` Lacks an Explicit Assigned Folders Section
- **File**: `apps/desktop/src/app/chat/sidebar/projects/entered-content.tsx:33-79`
- **Mechanism**:
  - `EnteredProjectContent` only iterates over `project.repos`.
  - For each repo, it renders `RepoFlatSection` which iterates over `repo.groups` (Git branches / worktrees / synthesized folder lanes).
  - There is no direct rendering of `project.folders` or indication of what non-git directories are explicitly attached to the project. If the backend fails to emit a lane for an assigned non-git folder, that folder vanishes from the interface.

---

### Flaw 4: `onNewSession` Binds CWD to `group.path` Without Honoring Project Folder Target
- **File**: `apps/desktop/src/app/chat/sidebar/projects/entered-content.tsx:191-202`
- **Mechanism**:
  ```tsx
  <SidebarWorkspaceGroup
    group={group}
    key={group.id}
    onNewSession={group.isKanban ? undefined : onNewSession}
    ...
  />
  ```
- **The Defect**:
  The `+` button passes `group.path` to `onNewSession`. Because Flaw 1 collapsed the repo into trunk `main`, `group.path` is `"O:/workspaces"`. Clicking `+` creates a new session in `"O:/workspaces"` instead of the project's `primary_path`.

---

## 3. Architecture Diagrams

### A. Current Flawed Pipeline
```
[projects.db]
  Project: Inspire-Robotics
  primary_path: O:\workspaces\devops-support\inspire-robotics
  folders: [ O:\workspaces\devops-support\inspire-robotics ]
       │
       ▼
[Backend: _seed_folder_repos()]
  resolve("O:\workspaces\devops-support\inspire-robotics")
       │
       ▼ Git probe sees O:\workspaces is the parent repo!
  root = "O:\workspaces"  (FLAW: discards subfolder path!)
  repo_node: "workspaces", groups: []
       │
       ▼
[Desktop Frontend: EnteredProjectContent]
  Renders single repo "workspaces" with empty groups
  Synthesizes default trunk lane:
    ⌂ main >  [+]  (Path: O:/workspaces)  <-- WRONG!
       │
       ▼ User clicks [+]
  New Chat launched with CWD: O:/workspaces  <-- INCORRECT WORKSPACE!
```

---

### B. Proposed Unified Architecture
```
[projects.db]
  Project: Inspire-Robotics
  primary_path: O:\workspaces\devops-support\inspire-robotics
  folders: [ O:\workspaces\devops-support\inspire-robotics ]
       │
       ▼
[Backend: Enhanced _seed_folder_repos() & _subfolder_under()]
  1. Recognizes "devops-support/inspire-robotics" as a declared subfolder lane.
  2. Emits a dedicated Level 3 Folder Lane even with 0 sessions:
     - id: "O:/workspaces::folder::devops-support/inspire-robotics"
     - label: "folder: devops-support/inspire-robotics"
     - path: "O:/workspaces/devops-support/inspire-robotics"
     - isGit: false, isMain: false
       │
       ▼
[Desktop Frontend: EnteredProjectContent]
  Renders:
  📁 devops-support/inspire-robotics  [+]  (Path: O:/workspaces/devops-support/inspire-robotics)
       │
       ▼ User clicks [+]
  New Chat launched with CWD: O:/workspaces/devops-support/inspire-robotics
       │
       ▼
  Right Sidebar (Files Panel) automatically navigates to:
  O:\workspaces\devops-support\inspire-robotics  <-- CORRECT FILE BROWSING!
```

---

## 4. Remediation Plan

### Phase 1: Backend Fixes (`tui_gateway/project_tree.py`)

1. **Retain Full Relative Path in `_subfolder_under(cwd, root)`**:
   - Rather than truncating at `len(root_segs) + 1`, preserve the full subfolder relative path (e.g. `devops-support/inspire-robotics`), or match against declared project folders in `folder_index`.
   - Update `_folder_placement()` to generate clean, legible labels: `folder: <subfolder_relpath>`.

2. **Seed Explicit Non-Git Folders as Dedicated Folder Lanes in `_seed_folder_repos()`**:
   - When iterating over declared `project.folders`:
     - If the folder is inside a parent Git repository (`info.get("repo_root")` matches parent repo), **do not discard the subfolder**.
     - Instead, attach a synthetic folder group (`::folder::<relative_path>`) directly into that repository node's `groups` array.
     - Set `isGit=False`, `isMain=False`, `path=folder_path`.
   - This ensures that even before the user starts their first chat, the declared folder appears immediately in the left sidebar as an active, clickable lane with its own `+` button.

### Phase 2: Frontend Fixes (`apps/desktop/src/...`)

1. **Project-Scoped New Session CWD Precedence**:
   - In `resolveNewSessionCwd()` (`store/projects.ts`), when a project is selected in the sidebar, ensure the default CWD is always `project.primary_path`, not falling back to the parent repository root.

2. **Folder Lane UI Styling & Header**:
   - In `SidebarWorkspaceGroup` / `entered-content.tsx`, render non-git folder lanes (`group.isGit === false`) with folder iconography (`Codicon: folder` or `folder-opened`) rather than Git branch icons.
   - Tooltip must explicitly show the folder's absolute path.
   - The `+` button on a folder lane must pass the exact folder path to `newChat({ cwd: group.path })`.

---

## 5. Verification & Test Plan

1. **Backend Unit Tests** (`tests/tui_gateway/test_projects_rpc.py`):
   - Add test case: Project with non-git declared folder inside a parent git repo.
   - Assert `projects.tree` returns a repo node containing a group with `lane_key` matching `::folder::<subfolder>` and `path` matching the declared subfolder path.
   - Assert deep nested paths (`a/b/c`) are preserved in `groups`, not truncated to `a`.
2. **End-to-End Desktop Verification**:
   - Launch Hermes Desktop.
   - Navigate to `INSPIRE-ROBOTICS`.
   - Verify that `folder: devops-support/inspire-robotics` is visible in the sidebar.
   - Click `+` on the folder row.
   - Verify that the newly created session has CWD `O:\workspaces\devops-support\inspire-robotics`.
   - Verify that the right sidebar File Browser lists files inside `O:\workspaces\devops-support\inspire-robotics`.
