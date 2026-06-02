from pathlib import Path

from ft_diag_agent.classifier import WorkOrderClassifier
from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.models import CoverageStatus, DiagnosisMode, WorkOrder
from ft_diag_agent.rag import DocumentRag
from ft_diag_agent.settings import Settings
from ft_diag_agent.work_orders import parse_pasted_work_order_text, parse_work_order_files
from ft_diag_agent.workflow import DiagnosticEngine

ROOT = Path(__file__).resolve().parents[1]
TTL = ROOT / "corrected_fault_tree_instances.ttl"
RAW_DOCS = ROOT / "data/raw_docs"

FREE_TEXT_NON_TREE_ORDER = """## 工单 01｜MOCK-NFT-01-01｜车辆加速无力/动力受限

### 基本信息

- 工单编号：`MOCK-NFT-01-01`
- VIN：`LXMOCKPWR001001`
- 创建时间：2026-05-10 09:20:00
- 车型/工厂：X 平台 / 常州工厂
- 业务域：动力系统
- 故障标签：动力受限
- 期望路由：`NON_TREE_DIAGNOSIS`
- 期望故障树：`NONE`
- 证据充分性：`SUFFICIENT`

### 用户/现场描述

仪表提示“动力受限，请安全停车”，SOC 62%，无黑屏，无车门闭合抱怨。

### 初步检查记录

- 读取 VCU/BMS/DCDC DTC
- 检查高压互锁状态
- 复核加速踏板开度与电机扭矩请求一致性

### 现场发现

P1A0B-VCU 扭矩降额；BMS 单体压差瞬时升高
"""


def test_parse_mock_work_orders() -> None:
    orders = parse_work_order_files(RAW_DOCS)

    assert len(orders) == 20
    assert orders[0].order_id.startswith("WO-")
    assert orders[0].failure_phenomenon
    assert orders[0].expected_leaf_symptom_id
    assert orders[0].executed_checks


def test_parse_free_text_work_order_without_strict_heading() -> None:
    order = parse_pasted_work_order_text(FREE_TEXT_NON_TREE_ORDER, Settings(llm_enable=False))

    assert order
    assert order.order_id == "MOCK-NFT-01-01"
    assert order.failure_phenomenon == "动力受限"
    assert order.vin == "LXMOCKPWR001001"
    assert order.vehicle_project == "X 平台"
    assert order.station_or_scene == "常州工厂"
    assert order.business_domain == "动力系统"
    assert order.expected_route == "NON_TREE_DIAGNOSIS"
    assert order.expected_fault_tree is None
    assert order.extraction_method == "RULE_FALLBACK"
    assert any("VCU/BMS/DCDC" in check for check in order.executed_checks)
    assert any("P1A0B-VCU" in check for check in order.executed_checks)


def test_mock_work_order_classification_rules() -> None:
    repo = RdfFaultTreeRepository(TTL)
    rag = DocumentRag(RAW_DOCS, ROOT / "data/chroma")
    classifier = WorkOrderClassifier(repo, rag, Settings(llm_enable=False))

    orders = parse_work_order_files(RAW_DOCS)
    results = [classifier.classify(order) for order in orders]

    assert all(result.coverage_status == CoverageStatus.COVERED for result in results)
    for order, result in zip(orders, results, strict=True):
        expected_tree = "FT_001" if order.expected_leaf_symptom_id.startswith("S0") else "FT_002"
        assert result.tree_id == expected_tree
        assert result.confidence >= 0.55


def test_unsupported_modes() -> None:
    repo = RdfFaultTreeRepository(TTL)
    rag = DocumentRag(RAW_DOCS, ROOT / "data/chroma")
    classifier = WorkOrderClassifier(repo, rag, Settings(llm_enable=False))
    order = WorkOrder(
        order_id="WO-UN-260520-001",
        failure_phenomenon="空调制冷不足",
        description="压缩机工作但出风温度偏高。",
    )

    prod = classifier.classify(order, DiagnosisMode.PRODUCTION)
    dev = classifier.classify(order, DiagnosisMode.DEVELOPMENT)

    assert prod.coverage_status == CoverageStatus.UNSUPPORTED
    assert prod.diagnosis_mode == DiagnosisMode.PRODUCTION
    assert dev.coverage_status == CoverageStatus.UNSUPPORTED
    assert dev.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY


def test_free_text_non_tree_order_routes_by_coverage(tmp_path: Path) -> None:
    repo = RdfFaultTreeRepository(TTL)
    settings = Settings(
        raw_docs_dir=RAW_DOCS,
        chroma_dir=tmp_path / "chroma",
        runs_dir=tmp_path / "runs",
        datasets_dir=tmp_path / "datasets",
        llm_enable=False,
    )
    engine = DiagnosticEngine(repo, DocumentRag(RAW_DOCS, tmp_path / "chroma"), settings)
    order = parse_pasted_work_order_text(FREE_TEXT_NON_TREE_ORDER, settings)

    assert order
    prod = engine.start_work_order(order, DiagnosisMode.PRODUCTION)
    dev = engine.start_work_order(order, DiagnosisMode.DEVELOPMENT)

    assert prod.coverage_decision
    assert prod.coverage_decision.status == CoverageStatus.UNSUPPORTED
    assert prod.gate_result
    assert prod.gate_result.status == "FAIL"
    assert dev.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY
    assert dev.planned_actions
    assert dev.evidence_chain
    assert dev.case_only_plan
    assert dev.case_only_hypotheses
    assert any("BMS" in h.system_area or "VCU" in (h.component or "") for h in dev.case_only_hypotheses)
    assert any("BMS" in action.reason or "扭矩" in action.reason for action in dev.planned_actions)
    assert dev.planned_actions[0].evidence_ids


def test_all_mock_work_orders_reach_expected_leaf(tmp_path: Path) -> None:
    repo = RdfFaultTreeRepository(TTL)
    settings = Settings(
        raw_docs_dir=RAW_DOCS,
        chroma_dir=tmp_path / "chroma",
        runs_dir=tmp_path / "runs",
        datasets_dir=tmp_path / "datasets",
        llm_enable=False,
    )
    engine = DiagnosticEngine(repo, DocumentRag(RAW_DOCS, tmp_path / "chroma"), settings)

    for order in parse_work_order_files(RAW_DOCS):
        state = engine.start_work_order(order, DiagnosisMode.PRODUCTION)
        expected_tree = "FT_001" if order.expected_leaf_symptom_id.startswith("S0") else "FT_002"

        assert state.active_tree_id == expected_tree
        assert state.active_node_id == order.expected_leaf_symptom_id
        assert all(action.tool_name == "human_input" for action in state.planned_actions)
