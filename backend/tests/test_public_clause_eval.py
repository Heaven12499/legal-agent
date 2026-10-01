from backend.scripts.eval_public_clauses import load, parse_risk_decision, summarize_risk


def test_parse_risk_decision_requires_protocol_first_line():
    assert parse_risk_decision("判定：需重点核查\n存在违约金风险。") is True
    assert parse_risk_decision("判定：无明显风险\n条款相对完整。") is False
    assert parse_risk_decision("该条款可能存在风险。") is None


def test_summarize_risk_reports_confusion_matrix_and_f1():
    rows = [
        {"gold_risk": True, "predicted_risk": True},
        {"gold_risk": True, "predicted_risk": False},
        {"gold_risk": False, "predicted_risk": True},
        {"gold_risk": False, "predicted_risk": False},
        {"gold_risk": False, "predicted_risk": None},
    ]

    summary = summarize_risk(rows)

    assert summary["evaluated"] == 5
    assert summary["decided"] == 4
    assert summary["unparseable"] == 1
    assert summary["protocol_compliance"] == 0.8
    assert summary["abstained_positive"] == 0
    assert summary["abstained_negative"] == 1
    assert (summary["tp"], summary["fp"], summary["fn"], summary["tn"]) == (1, 1, 1, 1)
    assert summary["precision"] == 0.5
    assert summary["recall"] == 0.5
    assert summary["specificity"] == 1 / 3
    assert summary["false_positive_rate"] == 1 / 3
    assert summary["f1"] == 0.5
    assert summary["accuracy"] == 0.4


def test_curated_benchmark_has_balanced_scored_labels_without_prompt_leakage():
    samples = load()
    positives = [row for row in samples if row["split"] == "positive"]
    negatives = [row for row in samples if row["split"] == "negative_curated"]
    excluded = [row for row in samples if row["split"] == "exclude"]

    assert (len(positives), len(negatives), len(excluded)) == (51, 24, 20)
    assert all(row["gold_articles"] for row in positives)
    assert all(not row["gold_articles"] for row in negatives)
    assert all("agent_prompt" not in row for row in positives + negatives)
