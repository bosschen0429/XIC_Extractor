from pathlib import Path

from scripts.check_validation_artifact_retention import (
    REQUIRED_INVENTORY_COLUMNS,
    check_validation_artifact_retention,
)


def test_current_validation_retention_inventory_accepts_worktree() -> None:
    result = check_validation_artifact_retention()

    assert result.problems == ()
    assert result.summary["inventory_rows"] == 312
    assert result.summary["present_validation_files"] == 259
    assert result.summary["externalized_count"] == 52
    assert result.summary["shrink_later_count"] == 0
    assert result.summary["shrink_later_tracked_count"] == 0
    assert result.summary["shrink_later_tracked_bytes"] == 0
    assert result.summary["shrink_later_files"] == []


def test_checker_rejects_missing_inventory_row(tmp_path: Path) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    _write_text(root / "docs/superpowers/validation/contract.tsv", "a\n1\n")
    _write_inventory(inventory, [])

    result = _check(
        root,
        inventory,
        policy,
        candidate_paths=["docs/superpowers/validation/contract.tsv"],
    )

    assert any("missing inventory rows" in problem for problem in result.problems)


def test_checker_rejects_externalized_png_still_present(tmp_path: Path) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    path = "docs/superpowers/validation/rendered/plot.png"
    _write_bytes(root / path, b"png")
    _write_inventory(
        inventory,
        [
            _row(
                path,
                category="rendered_plot",
                decision="externalize",
                replacement="local_validation_artifacts/rendered/plot.png",
            ),
        ],
    )

    result = _check(root, inventory, policy, candidate_paths=[path])

    assert any(
        "externalize artifact is still present" in problem
        for problem in result.problems
    )


def test_checker_rejects_externalized_html_still_present(tmp_path: Path) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    path = "docs/superpowers/validation/rendered/index.html"
    _write_text(root / path, "<html></html>")
    _write_inventory(
        inventory,
        [
            _row(
                path,
                category="rendered_html",
                decision="externalize",
                replacement="local_validation_artifacts/rendered/index.html",
            ),
        ],
    )

    result = _check(root, inventory, policy, candidate_paths=[path])

    assert any(
        "externalize artifact is still present" in problem
        for problem in result.problems
    )


def test_checker_accepts_contract_tsv_and_summary_markdown(tmp_path: Path) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    contract = "docs/superpowers/validation/contract.tsv"
    summary = "docs/superpowers/validation/README.md"
    _write_text(root / contract, "id\tvalue\nA\t1\n")
    _write_text(root / summary, "# Summary\n")
    _write_inventory(
        inventory,
        [
            _row(contract, category="tabular_contract", decision="keep_contract"),
            _row(summary, category="summary_or_policy", decision="keep_summary"),
        ],
    )

    result = _check(root, inventory, policy, candidate_paths=[contract, summary])

    assert result.problems == ()


def test_checker_warns_for_large_shrink_later_and_strict_fails(tmp_path: Path) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    path = "docs/superpowers/validation/big.tsv"
    _write_text(root / path, "x" * 20)
    _write_inventory(
        inventory,
        [
            _row(
                path,
                category="full_result_tsv",
                decision="shrink_later",
                generated_by="scripts/build_big.py",
                required_by="current checker",
                replacement="replace later with summary",
            ),
        ],
    )

    result = _check(
        root,
        inventory,
        policy,
        candidate_paths=[path],
        large_file_threshold_bytes=10,
    )
    strict_result = _check(
        root,
        inventory,
        policy,
        candidate_paths=[path],
        strict=True,
        large_file_threshold_bytes=10,
    )

    assert result.problems == ()
    assert result.summary["shrink_later_tracked_count"] == 1
    assert result.summary["shrink_later_tracked_bytes"] == 20
    assert result.summary["shrink_later_files"] == [
        {
            "path": path,
            "size_bytes": 20,
            "category": "full_result_tsv",
            "required_by": "current checker",
            "replacement_or_summary": "replace later with summary",
        },
    ]
    assert any("shrink_later remains tracked" in warning for warning in result.warnings)
    assert any(
        "shrink_later remains tracked" in problem
        for problem in strict_result.problems
    )


def test_checker_rejects_rendered_shrink_later_without_strict(
    tmp_path: Path,
) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    path = "docs/superpowers/validation/rendered/index.html"
    _write_text(root / path, "<html></html>")
    _write_inventory(
        inventory,
        [
            _row(
                path,
                category="rendered_html",
                decision="shrink_later",
                generated_by="scripts/build_gallery.py",
                required_by="manual review only",
                replacement="local_validation_artifacts/rendered/index.html",
            ),
        ],
    )

    result = _check(root, inventory, policy, candidate_paths=[path])

    assert any(
        "rendered validation artifact must be externalized" in problem
        for problem in result.problems
    )


def test_checker_rejects_stale_retained_file_metadata(tmp_path: Path) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    path = "docs/superpowers/validation/contract.tsv"
    _write_text(root / path, "id\tvalue\nA\t1\n")
    _write_inventory(
        inventory,
        [
            _row(
                path,
                category="tabular_contract",
                decision="keep_contract",
                size_bytes="1",
                line_count="99",
            ),
        ],
    )

    result = _check(root, inventory, policy, candidate_paths=[path])

    assert any("size_bytes is stale" in problem for problem in result.problems)
    assert any("line_count is stale" in problem for problem in result.problems)


def test_checker_uses_normalized_text_size_for_retained_metadata(
    tmp_path: Path,
) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    path = "docs/superpowers/validation/contract.tsv"
    _write_bytes(root / path, b"id\tvalue\r\nA\t1\r\n")
    _write_inventory(
        inventory,
        [
            _row(
                path,
                category="tabular_contract",
                decision="keep_contract",
                size_bytes=str(len("id\tvalue\nA\t1\n".encode("utf-8"))),
                line_count="2",
            ),
        ],
    )

    result = _check(root, inventory, policy, candidate_paths=[path])

    assert result.problems == ()


def test_checker_rejects_stale_rendered_reference_without_mapping(
    tmp_path: Path,
) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    summary = "docs/superpowers/validation/summary.md"
    rendered = "docs/superpowers/validation/rendered/index.html"
    _write_text(root / summary, f"Open {rendered}\n")
    _write_inventory(
        inventory,
        [_row(summary, category="summary_or_policy", decision="keep_summary")],
    )

    result = _check(root, inventory, policy, candidate_paths=[summary])

    assert any("lacks inventory mapping" in problem for problem in result.problems)


def test_checker_accepts_stale_rendered_reference_with_externalized_mapping(
    tmp_path: Path,
) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    summary = "docs/superpowers/validation/summary.md"
    rendered = "docs/superpowers/validation/rendered/index.html"
    _write_text(root / summary, f"Open {rendered}\n")
    _write_inventory(
        inventory,
        [
            _row(summary, category="summary_or_policy", decision="keep_summary"),
            _row(
                rendered,
                category="rendered_html",
                decision="externalize",
                replacement="local_validation_artifacts/rendered/index.html",
            ),
        ],
    )

    result = _check(root, inventory, policy, candidate_paths=[summary])

    assert result.problems == ()


def test_checker_can_require_externalized_local_copy(tmp_path: Path) -> None:
    root, inventory, policy = _fixture_root(tmp_path)
    summary = "docs/superpowers/validation/summary.md"
    rendered = "docs/superpowers/validation/rendered/index.html"
    replacement = "local_validation_artifacts/rendered/index.html"
    _write_text(root / summary, f"Open {rendered}\n")
    _write_inventory(
        inventory,
        [
            _row(summary, category="summary_or_policy", decision="keep_summary"),
            _row(
                rendered,
                category="rendered_html",
                decision="externalize",
                replacement=replacement,
            ),
        ],
    )

    missing = _check(
        root,
        inventory,
        policy,
        candidate_paths=[summary],
        require_externalized_local=True,
    )
    _write_text(root / replacement, "<html></html>")
    present = _check(
        root,
        inventory,
        policy,
        candidate_paths=[summary],
        require_externalized_local=True,
    )

    assert any(
        "externalized local replacement missing" in problem
        for problem in missing.problems
    )
    assert present.problems == ()


def _fixture_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path
    validation = root / "docs/superpowers/validation"
    validation.mkdir(parents=True)
    policy = validation / "RETENTION.md"
    inventory = validation / "ARTIFACT_INVENTORY.tsv"
    _write_text(
        policy,
        "\n".join(
            [
                "| Decision | Meaning |",
                "| --- | --- |",
                "| `keep_contract` | keep |",
                "| `keep_summary` | keep |",
                "| `keep_minimal_fixture` | keep |",
                "| `shrink_later` | keep temporarily |",
                "| `externalize` | move local |",
                "| `delete_generated` | remove |",
                "",
            ],
        ),
    )
    return root, inventory, policy


def _check(
    root: Path,
    inventory: Path,
    policy: Path,
    *,
    candidate_paths: list[str],
    deleted_paths: list[str] | None = None,
    strict: bool = False,
    require_externalized_local: bool = False,
    large_file_threshold_bytes: int = 500_000,
):
    return check_validation_artifact_retention(
        inventory_path=inventory,
        retention_policy_path=policy,
        validation_dir=root / "docs/superpowers/validation",
        repo_root=root,
        strict=strict,
        require_externalized_local=require_externalized_local,
        large_file_threshold_bytes=large_file_threshold_bytes,
        candidate_paths=candidate_paths,
        deleted_paths=deleted_paths or [],
    )


def _row(
    path: str,
    *,
    category: str,
    decision: str,
    size_bytes: str = "",
    line_count: str = "",
    generated_by: str = "",
    required_by: str = "",
    replacement: str = "",
) -> dict[str, str]:
    return {
        "path": path,
        "size_bytes": size_bytes,
        "line_count": line_count,
        "category": category,
        "retention_decision": decision,
        "keep_reason": "test reason",
        "generated_by": generated_by,
        "required_by": required_by,
        "replacement_or_summary": replacement,
    }


def _write_inventory(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = path.parent.parents[2]
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\t".join(REQUIRED_INVENTORY_COLUMNS) + "\n")
        for row in rows:
            file_path = root / row["path"]
            if file_path.exists():
                row = dict(row)
                if not row.get("size_bytes", ""):
                    try:
                        text = file_path.read_text(encoding="utf-8")
                        row["size_bytes"] = str(len(text.encode("utf-8")))
                    except UnicodeDecodeError:
                        row["size_bytes"] = str(file_path.stat().st_size)
                if not row.get("line_count", ""):
                    try:
                        row["line_count"] = str(
                            len(file_path.read_text(encoding="utf-8").splitlines()),
                        )
                    except UnicodeDecodeError:
                        pass
            handle.write(
                "\t".join(row.get(column, "") for column in REQUIRED_INVENTORY_COLUMNS)
                + "\n",
            )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
