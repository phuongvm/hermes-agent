"""Synthetic catalog resources for the sanitized Designer proposal assignment."""
from hashlib import sha256

# Source: Nerve tests/fixtures/designer_prerequisite_incident.json (66e5f45).
CHANGE = "example-topic"
GOAL = (
    "Execute /openspec-propose to create a complete, minimal, validated OpenSpec proposal package for "
    "example-topic from the approved exploration. Produce real proposal.md, design.md, delta specs, "
    "unchecked tasks, exploration linkage/snapshot and validation evidence. Documentation only; "
    "return paths/hash/validation for independent proposal review."
)
CONTEXT = (
    "Change slug example-topic. Sole write ownership under new change directory. Schema tool fields: "
    "openspec_change_create requires change,title,summary,workdir. Record change metadata."
)
GOVERNANCE_PATHS = (
    "AGENTS.md", "shared/startup-rules.md", "shared/safety.md", "skills/task-verification-mandate.md",
    "shared/rules-index.md", "user/communication-language.md", "shared/lessons-digest.md",
    "skills/anti-deception-instruction.md", "skills/compliance-enforcer.md",
)
SKILL_NAMES = (
    "verification-before-claim", "openspec-propose", "openspec-workflow-governance", "codebase-design",
    "deep-reference-protocol", "ai-agent-collaboration", "hermes-agent",
)
BASELINE_NAMES = ("nerve-crew-dod-and-role-handoff", "nerve-reflex-resilience")
PROJECT_PATHS = {
    "project/agent-share": "openspec/workspace/sessions/agent_share.md",
    "project/exploration": "openspec/workspace/explorations/approved.md",
    "project/receipt": "openspec/workspace/explorations/receipt.md",
}
REFERENCE = "reference/verification-before-claim/references/completion-gate.md"


def proposal_entry(root):
    relative = {
        **PROJECT_PATHS,
        **{f"baseline/{name}": f"openspec/specs/{name}/spec.md" for name in BASELINE_NAMES},
        "snapshot/status": f"openspec/workspace/prerequisites/{CHANGE}/status.json",
        **{f"snapshot/instructions/{name}": f"openspec/workspace/prerequisites/{CHANGE}/instructions/{name}.json"
           for name in ("proposal", "design", "specs", "tasks")},
    }
    absolute = {
        **{f"governance/{name}": root / "rules" / name for name in GOVERNANCE_PATHS},
        **{f"skill/{name}": root / "skills" / name / "SKILL.md" for name in SKILL_NAMES},
        REFERENCE: root / "skills/verification-before-claim/references/completion-gate.md",
    }
    rows = {}
    for key, path in {**absolute, **{k: root / v for k, v in relative.items()}}.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        data = f"Fixture for {key}\n".encode()
        path.write_bytes(data)
        info = path.stat()
        rows[key] = {"path": relative.get(key, str(path)), "device": info.st_dev, "inode": info.st_ino,
                     "size": len(data), "max_file_bytes": 512, "sha256": sha256(data).hexdigest()}
    rows.update({"project/requirements": None, "project/architecture": None})
    return {"planning_home": str(root), "operations": 64, "seconds": 600, "resources": rows}


def profile_config(entry):
    return {"prerequisite_catalog": {"openspec-propose": {CHANGE: entry}}}
