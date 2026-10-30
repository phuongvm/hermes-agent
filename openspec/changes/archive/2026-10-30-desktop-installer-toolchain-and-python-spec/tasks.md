## 1. Spec Authoring
- [x] 1.1 Read both explorations, both APPROVED reviews and the canonical `desktop-release-publishing` spec
- [x] 1.2 Write `proposal.md`, `design.md` and delta `specs/desktop-release-publishing/spec.md` (2 ADDED requirements)

## 2. Validation and Sync
- [x] 2.1 `openspec validate desktop-installer-toolchain-and-python-spec --strict` passes with 0 warnings
- [x] 2.2 Sync the two ADDED requirements into `openspec/specs/desktop-release-publishing/spec.md` (9 requirements total)
- [x] 2.3 `openspec validate desktop-release-publishing --type spec --strict` passes with 0 warnings
- [x] 2.4 Seam A + Seam B proof: `python -m pytest .../services/tests/test_synology_release_publisher.py -q` passes (190 tests, rc=0)
- [x] 2.5 Scope check: zero diff under `apps/desktop/`, `build-desktop-installer.ps1`, `synology_release_publisher.py`, `hermes-env.cmd`

## 3. Review and Archive Gate
- [x] 3.1 Independent @reviewer verification of the spec-to-implementation mapping (Card 2) — APPROVED in `reviews/findings-spec-promotion-review-v2.md`; Crew proof PASS (rc=0)
- [x] 3.2 Archive change `desktop-installer-toolchain-and-python-spec` after Commander approval (`openspec archive desktop-installer-toolchain-and-python-spec --skip-specs -y`)
