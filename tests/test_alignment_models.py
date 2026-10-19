from xic_extractor.alignment.models import AlignmentCluster


def test_alignment_cluster_fold_metadata_defaults_to_empty():
    cluster = AlignmentCluster(
        cluster_id="ALN000001",
        neutral_loss_tag="DNA_dR",
        cluster_center_mz=242.114,
        cluster_center_rt=12.5927,
        cluster_product_mz=126.066,
        cluster_observed_neutral_loss_da=116.048,
        has_anchor=False,
        members=(),
    )

    assert cluster.folded_cluster_ids == ()
    assert cluster.folded_member_count == 0
    assert cluster.folded_sample_fill_count == 0
    assert cluster.fold_evidence == ""
