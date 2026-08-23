from finance_query.critic_calibration_v2 import human_reviewed_audit_metrics,score_stratum
POL={"minimum_sample_size":2,"minimum_source_coordinate_agreement":.99,"minimum_unit_period_agreement":.99,"minimum_accept_precision":.99,"maximum_false_acceptance_rate":.01,"minimum_abstention_correctness":.95,"minimum_deterministic_replay_agreement":1.,"maximum_unsupported_evidence_rate":0.}
def test_independent_labels_can_promote_only_when_every_metric_passes():
 good=[{"label_provenance":"human_verified","critic_status":"accept","independent_status":"accept","source_coordinate_agree":True,"unit_period_agree":True,"deterministic_replay_agree":True,"unsupported_evidence":False},{"label_provenance":"human_verified","critic_status":"abstain","independent_status":"abstain","source_coordinate_agree":True,"unit_period_agree":True,"deterministic_replay_agree":True,"unsupported_evidence":False}]
 assert score_stratum(good,POL)["promotion_recommendation"]=="promote"
 bad=[{**good[0],"unsupported_evidence":True}]*2
 assert score_stratum(bad,POL)["promotion_recommendation"]=="needs_human"


def test_training_metric_excludes_independent_ai_reviews():
 rows=[
  {"label_provenance":"human_verified","critic_status":"accept","independent_status":"accept"},
  {"label_provenance":"independent_ai_source_review","critic_status":"accept","independent_status":"reject"},
 ]
 metrics=human_reviewed_audit_metrics(rows)
 assert metrics["independent_audit_sample_size"]==1
 assert metrics["independent_audit_accept_count"]==1
 assert metrics["independent_audit_precision"]==1.0
