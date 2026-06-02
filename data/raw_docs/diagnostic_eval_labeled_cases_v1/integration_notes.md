# Streamlit / CLI 接入建议

## JSONL读取示例

```python
import json
from pathlib import Path

cases = [json.loads(line) for line in Path("datasets/eval_cases/diagnostic_eval_cases_v1.jsonl").read_text(encoding="utf-8").splitlines()]
```

## 转成 EvalCase 的建议映射

```python
EvalCase(
    case_id=row["case_id"],
    input_text=row["case_description"] + "
" + row["observed_evidence"],
    expected_route=row["expected_route"],
    expected_tree_id=row["expected_tree_id"],
    expected_leaf_id=row["expected_final_leaf_id"],
    expected_gate=row["expected_gate"],
    expected_root_cause=row["expected_final_root_cause"],
    expected_next_actions=row["expected_next_action_hit"].split("; "),
)
```

## 建议目录

```text
datasets/
  eval_cases/
    diagnostic_eval_cases_v1.jsonl
    diagnostic_eval_cases_v1.csv
  eval_results/
```
