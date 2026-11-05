"""Helpers shared by test_alignment_drift_evidence and other test modules."""

from pathlib import Path

from openpyxl import Workbook


def _known_reference_workbook(path: Path) -> Workbook:
    workbook = Workbook()
    targets = workbook.active
    targets.title = "Targets"
    targets.append(
        [
            "Label",
            "Role",
            "m/z",
            "RT min",
            "RT max",
            "ppm tol",
            "NL (Da)",
            "NL ppm warn",
            "NL ppm max",
        ]
    )
    targets.append([" known ", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50])
    results = workbook.create_sheet("XIC Results")
    results.append(["SampleName", "Target", "Role", "RT", "Area"])
    results.append([" early ", "analyte", "Analyte", 8, 1])
    results.append([None, "known", "ISTD", 8, "not evidence"])
    results.append(["late", "analyte", "Analyte", 9, 1])
    results.append(["", "known", "ISTD", 9, -999])
    results.append(["missing", "analyte", "Analyte", "ND", 1])
    results.append([None, "known", "ISTD", "ND", 100])
    workbook.save(path)
    return workbook
