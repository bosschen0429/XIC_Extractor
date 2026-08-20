from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    IdentityEvent,
    OwnerAssignment,
    SampleLocalMS1Owner,
)


def test_sample_local_owner_exposes_primary_and_supporting_events():
    primary = IdentityEvent(
        candidate_id="s1#6095",
        sample_stem="s1",
        raw_file="s1.raw",
        neutral_loss_tag="DNA_dR",
        precursor_mz=242.114,
        product_mz=126.066,
        observed_neutral_loss_da=116.048,
        seed_rt=12.5927,
        evidence_score=80,
        seed_event_count=3,
    )
    support = IdentityEvent(
        candidate_id="s1#6096",
        sample_stem="s1",
        raw_file="s1.raw",
        neutral_loss_tag="DNA_dR",
        precursor_mz=242.1141,
        product_mz=126.0661,
        observed_neutral_loss_da=116.048,
        seed_rt=12.594,
        evidence_score=70,
        seed_event_count=1,
    )

    owner = SampleLocalMS1Owner(
        owner_id="OWN-s1-000001",
        sample_stem="s1",
        raw_file="s1.raw",
        precursor_mz=242.114,
        owner_apex_rt=12.593,
        owner_peak_start_rt=12.55,
        owner_peak_end_rt=12.64,
        owner_area=12345.0,
        owner_height=1000.0,
        primary_identity_event=primary,
        supporting_events=(support,),
        identity_conflict=False,
        assignment_reason="same_apex_window",
    )

    assert owner.all_events == (primary, support)
    assert owner.neutral_loss_tag == "DNA_dR"
    assert owner.event_candidate_ids == ("s1#6095", "s1#6096")


def test_sample_local_owner_censors_endpoint_apex_even_without_explicit_flag():
    event = IdentityEvent(
        candidate_id="s1#6095",
        sample_stem="s1",
        raw_file="s1.raw",
        neutral_loss_tag="DNA_dR",
        precursor_mz=242.114,
        product_mz=126.066,
        observed_neutral_loss_da=116.048,
        seed_rt=12.55,
        evidence_score=80,
        seed_event_count=3,
    )

    owner = SampleLocalMS1Owner(
        owner_id="OWN-s1-000001",
        sample_stem="s1",
        raw_file="s1.raw",
        precursor_mz=242.114,
        owner_apex_rt=12.55,
        owner_peak_start_rt=12.55,
        owner_peak_end_rt=12.64,
        owner_area=12345.0,
        owner_height=1000.0,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="adapter_projection",
    )

    assert owner.quantification_eligible is False
    assert owner.quantification_exclusion_reason == (
        "owner_apex_on_integration_boundary"
    )


def test_owner_assignment_and_ambiguity_records_are_debug_contracts():
    assignment = OwnerAssignment(
        candidate_id="s1#7002",
        owner_id="OWN-s1-000003",
        assignment_status="supporting",
        reason="tail_assignment",
    )
    ambiguous = AmbiguousOwnerRecord(
        ambiguity_id="AMB-s1-000001",
        sample_stem="s1",
        candidate_ids=("s1#8001", "s1#8002"),
        reason="owner_multiplet_ambiguity",
    )

    assert assignment.assignment_status == "supporting"
    assert ambiguous.candidate_ids == ("s1#8001", "s1#8002")
