"""The private invitation target is package data and cannot publish a release."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/ci.yml").read_text()
SPEC = (ROOT / "webjam.spec").read_text()


def test_target_input_is_allowlisted_and_passed_as_data():
    input_block = WORKFLOW.split("      web_join_target:\n", 1)[1].split("\n# Default:", 1)[0]
    assert "type: choice" in input_block and "default: public" in input_block
    assert "          - public\n          - private-beta\n" in input_block
    build = WORKFLOW.split("      - name: Build desktop artifact\n", 1)[1].split("\n      - name:", 1)[0]
    assert "WEBJAM_WEB_JOIN_TARGET: ${{ inputs.web_join_target || 'public' }}" in build
    script = build.split("        run: |\n", 1)[1]
    assert "${{ inputs.web_join_target" not in script
    # Explicit Bash retains Actions' existing -e/pipefail contract. Do not
    # change unset-variable behavior across the rest of this large build step.
    assert build.startswith("        shell: bash\n")
    assert "continue-on-error:" not in build
    assert "set +e" not in script.split("python -m tests.support.verify_packaged_web_join_target", 1)[0]


def test_every_desktop_target_verifies_its_actual_bundled_manifest():
    build = WORKFLOW.split("      - name: Build desktop artifact\n", 1)[1].split("\n      - name:", 1)[0]
    assert "metadata_root=dist/WebJam/_internal" in build
    assert "metadata_root=dist/WebJam.app/Contents/Resources" in build
    assert "python -m tests.support.verify_packaged_web_join_target" in build
    assert '--manifest "$metadata_root/webjam-web-join-target.json"' in build
    assert '--build-id "$(git rev-parse HEAD)"' in build
    assert '--target "$WEBJAM_WEB_JOIN_TARGET"' in build
    assert '(str(_web_join_target_path), "."),' in SPEC
    assert 'git_ref=os.environ.get("GITHUB_REF", "")' in SPEC


def test_private_target_cannot_create_a_tag_release_draft():
    release = WORKFLOW.split("  release:\n", 1)[1].split("    runs-on:", 1)[0]
    assert "startsWith(github.ref, 'refs/tags/v')" in release
    assert "inputs.final_build &&" in release
    assert "(inputs.web_join_target == '' || inputs.web_join_target == 'public')" in release
