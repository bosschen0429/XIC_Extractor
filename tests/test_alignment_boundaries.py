def test_alignment_exports_plan2_public_api_names():
    import xic_extractor.alignment as alignment

    assert alignment.__all__ == ("AlignmentConfig",)
    assert {name for name in dir(alignment) if not name.startswith("_")} == set(
        alignment.__all__
    )
