from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

import streamlit as st

from ft_diag_agent.dynamic_tree import merge_dynamic_tree_clusters
from ft_diag_agent.eval import (
    EvalCaseResult,
    default_eval_cases,
    load_labeled_eval_cases_v1,
    load_replay_records,
    run_eval_cases,
    write_eval_outputs,
)
from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.models import (
    CoverageStatus,
    DiagnosisMode,
    DiagnosticAction,
    DiagnosticState,
    IntakeRequest,
    TreeGenerationHitlDecision,
    WorkOrder,
)
from ft_diag_agent.rag import DocumentRag
from ft_diag_agent.settings import Settings
from ft_diag_agent.tree_generation import (
    SUPPORTED_TREE_SOURCE_SUFFIXES,
    BatchTreeGenerationService,
    generation_hitl_items,
    render_tree_generation_mermaid,
)
from ft_diag_agent.tree_proposal_eval import run_tree_proposal_eval
from ft_diag_agent.tree_proposals import TreeProposalStore
from ft_diag_agent.work_orders import parse_pasted_work_order_text, parse_work_order_files
from ft_diag_agent.workflow import DiagnosticEngine

st.set_page_config(page_title="故障树诊断 Agent", layout="wide")


@st.cache_resource(show_spinner="解析故障树 TTL...")
def get_repository(ttl_path: str) -> RdfFaultTreeRepository:
    return RdfFaultTreeRepository(ttl_path)


@st.cache_resource(show_spinner="初始化文档检索...")
def get_rag(raw_docs_dir: str, chroma_dir: str, collection: str, chunk_size: int, overlap: int) -> DocumentRag:
    return DocumentRag(raw_docs_dir, chroma_dir, collection, chunk_size, overlap)


@st.cache_resource(show_spinner="初始化诊断引擎...")
def get_engine(
    ttl_path: str,
    raw_docs_dir: str,
    chroma_dir: str,
    collection: str,
    chunk_size: int,
    overlap: int,
    runs_dir: str,
    datasets_dir: str,
    openai_model: str,
    enable_llm: bool,
    llm_provider: str,
    diagnosis_mode: str,
) -> DiagnosticEngine:
    settings = Settings(
        fault_tree_ttl_path=Path(ttl_path),
        raw_docs_dir=Path(raw_docs_dir),
        chroma_dir=Path(chroma_dir),
        runs_dir=Path(runs_dir),
        datasets_dir=Path(datasets_dir),
        rag_collection_name=collection,
        rag_chunk_size=chunk_size,
        rag_chunk_overlap=overlap,
        openai_model=openai_model,
        openai_enable_llm=enable_llm,
        llm_provider=llm_provider,
        llm_enable=enable_llm,
        diagnosis_mode=diagnosis_mode,
    )
    repository = get_repository(ttl_path)
    rag = get_rag(raw_docs_dir, chroma_dir, collection, chunk_size, overlap)
    return DiagnosticEngine(repository, rag, settings)


@st.cache_resource(show_spinner=False)
def get_tree_generation_service(
    tree_generation_dir: str,
    tree_proposals_dir: str,
    enable_llm: bool,
    llm_provider: str,
    openai_model: str,
) -> BatchTreeGenerationService:
    settings = Settings(
        tree_generation_dir=Path(tree_generation_dir),
        tree_proposals_dir=Path(tree_proposals_dir),
        llm_enable=enable_llm,
        llm_provider=llm_provider,
        openai_model=openai_model,
    )
    return BatchTreeGenerationService(settings)


@st.cache_data(show_spinner=False)
def scan_doc_count(raw_docs_dir: str, chroma_dir: str, collection: str, chunk_size: int, overlap: int) -> int:
    rag = DocumentRag(raw_docs_dir, chroma_dir, collection, chunk_size, overlap)
    return len(rag.scan())


def reset_case() -> None:
    st.session_state.pop("diag_state", None)
    st.session_state.pop("last_start_message", None)


def state() -> DiagnosticState | None:
    return st.session_state.get("diag_state")


@st.cache_data(show_spinner=False)
def load_mock_work_orders(raw_docs_dir: str) -> list[dict]:
    return [order.model_dump(mode="json") for order in parse_work_order_files(raw_docs_dir)]


def select_action(actions: list[DiagnosticAction]) -> DiagnosticAction:
    labels = [
        f"{idx + 1}. P{action.priority} · {action.test_id or action.action_id} · {action.reason[:40]}"
        for idx, action in enumerate(actions)
    ]
    selected = st.radio("选择要执行/录入的检测", options=list(range(len(actions))), format_func=lambda i: labels[i])
    return actions[selected]


def node_label(engine: DiagnosticEngine, node_id: str | None) -> str:
    if not node_id:
        return "N/A"
    node = engine.repository.get_symptom(node_id)
    return f"{node.symptom_id} · {node.name}" if node else node_id


def fmt_metric(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.2%}"
    return str(value)


def enum_value(value: object) -> object:
    return getattr(value, "value", value)


def start_message(engine: DiagnosticEngine, new_state: DiagnosticState, coverage_label: str) -> str:
    return (
        f"已开始诊断 {new_state.case_id}：{coverage_label}，"
        f"当前节点={node_label(engine, new_state.active_node_id)}。"
    )


def render_active_node(engine: DiagnosticEngine, current: DiagnosticState) -> None:
    if current.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY:
        st.warning("当前为非故障树覆盖的开发态探索，不展示故障树节点/分支；下方为探索计划和疑似假设。")
        if current.case_only_plan:
            st.markdown(f"**探索目标**：{current.case_only_plan.objective}")
            st.write(current.case_only_plan.summary)
            st.caption(
                f"Planner 来源：{current.case_only_plan.planner_source} · "
                f"引用证据：{', '.join(current.case_only_plan.evidence_ids[:4]) or '暂无'}"
            )
        if current.case_only_hypotheses:
            rows = [
                {
                    "假设ID": item.hypothesis_id,
                    "系统": item.system_area,
                    "部件": item.component or "-",
                    "失效模式": item.failure_mode,
                    "置信度": item.confidence,
                    "状态": item.status,
                    "依据": item.rationale,
                }
                for item in current.case_only_hypotheses
            ]
            st.dataframe(rows, width="stretch", hide_index=True)
        if current.case_only_findings:
            st.caption("已记录探索发现")
            st.dataframe(
                [finding.model_dump(mode="json") for finding in current.case_only_findings],
                width="stretch",
                hide_index=True,
            )
        return
    node = engine.repository.get_symptom(current.active_node_id) if current.active_node_id else None
    if not node:
        st.info("当前没有活动故障树节点。")
        return
    st.markdown(f"**{node.symptom_id} · {node.name}**")
    st.caption(f"层级：{node.level or 'unknown'}")
    if node.description:
        st.write(node.description)
    outgoing = engine.repository.outgoing_transitions(node.symptom_id, current.active_tree_id)
    if outgoing:
        rows = []
        for transition in outgoing:
            test = engine.repository.get_test(transition.test_id)
            target = engine.repository.get_symptom(transition.target_id)
            rows.append(
                {
                    "test_id": transition.test_id,
                    "检测项": test.display_name if test else transition.test_id,
                    "条件": transition.condition or "缺失",
                    "目标节点": f"{transition.target_id} · {target.name if target else ''}",
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.success("当前节点没有后续分支，通常表示已到达叶子/候选根因。")


def render_action(engine: DiagnosticEngine, current: DiagnosticState, action: DiagnosticAction) -> None:
    test = engine.repository.get_test(action.test_id) if action.test_id else None
    is_case_only = action.action_type == "CASE_ONLY_HITL"
    is_rework_counter = action.action_type == "REWORK_COUNTER_CHECK"
    is_confirmation = action.action_type == "CONFIRMATION_CHECK"
    with st.expander("动作详情", expanded=True):
        st.write(action.reason)
        st.caption(
            f"Planner 来源：{action.planner_source} · 置信度：{action.confidence:.2f}"
            + (f" · 证据引用：{', '.join(action.evidence_ids[:3])}" if action.evidence_ids else "")
        )
        if action.risk_notes:
            st.warning("；".join(action.risk_notes))
        if is_case_only:
            st.caption("这是开发态探索动作，不对应故障树 test；提交结果只用于沉淀证据和 replay，不能生产放行。")
            if action.target_cause_id:
                linked = next(
                    (
                        hypothesis
                        for hypothesis in current.case_only_hypotheses
                        if hypothesis.hypothesis_id == action.target_cause_id
                    ),
                    None,
                )
                if linked:
                    st.write(f"**关联假设**：{linked.system_area} / {linked.failure_mode}")
        elif is_rework_counter:
            st.caption(
                "这是返修/误判反证检查，不对应故障树固定 test；"
                "提交结果用于确认是否存在前次处置无效或相邻根因风险。"
            )
        elif is_confirmation:
            st.caption("这是疑似根因的发布前补证动作，用于降低误判和返修风险；不改变已确认的故障树路径。")
        else:
            st.caption("当前阶段所有故障树检测项均按人工 HITL 处理；后续由故障树生成 Agent 标注自动/模型检测。")
        if test and not is_case_only and not is_rework_counter and not is_confirmation:
            st.write(f"**检测项**：{test.display_name}")
            st.write(f"**目标**：{test.target or '未知'}")
            st.write(f"**规则**：{test.rule or '缺失'}")
            st.write(f"**条件范围**：{test.lolim or '-'} ~ {test.hilim or '-'} {test.unit or ''}")
        if action.source_refs:
            st.write("**参考来源**")
            for ref in action.source_refs:
                st.caption(ref)

    with st.form(f"hitl_form_{action.action_id}", clear_on_submit=True):
        result = st.text_input("人工检测结论", value="")
        value = st.text_input("检测值/读数", value="")
        if is_case_only:
            passed_label = "该信息是否支持当前探索判断"
        elif is_rework_counter:
            passed_label = "该信息是否确认返修/误判风险"
        elif is_confirmation:
            passed_label = "该补证是否支持当前疑似根因"
        else:
            passed_label = "该检测是否支持进入目标分支"
        passed = st.selectbox(passed_label, ["未知", "是", "否"], index=0)
        strength = st.slider("证据强度", min_value=0.0, max_value=1.0, value=0.7, step=0.05)
        notes = st.text_area("备注", value="", height=80)
        accepted = st.checkbox("采纳 Planner 建议", value=True)
        submitted = st.form_submit_button("提交检测结果", type="primary")
        if submitted:
            payload = {
                "action_id": action.action_id,
                "test_id": action.test_id,
                "result": result or "已完成检测",
                "value": value or None,
                "passed": None if passed == "未知" else passed == "是",
                "notes": notes or None,
                "supports_cause_id": action.target_cause_id,
                "supports_node_id": action.target_node_id,
                "strength": strength,
            }
            st.session_state["diag_state"] = engine.apply_human_test(current, payload, accepted=accepted)
            st.rerun()


def render_eval_tab(engine: DiagnosticEngine, raw_docs_dir: str, datasets_dir: str) -> None:
    st.subheader("诊断评测")
    st.caption(
        "评测只在点击按钮时运行；普通页面交互不会触发批量诊断。"
        "labeled v1 会按无标签泄漏方式读取 38 条标注工单。"
    )
    suite = st.selectbox(
        "评测集",
        ["default_mock_21", "labeled_v1_38"],
        format_func=lambda item: "默认 mock 21 条" if item == "default_mock_21" else "标注评测 v1 38 条",
    )
    col_run, col_save = st.columns([1, 1])
    if col_run.button("运行评测集", type="primary", width="stretch"):
        cases = default_eval_cases(raw_docs_dir) if suite == "default_mock_21" else load_labeled_eval_cases_v1()
        st.session_state["eval_summary"] = run_eval_cases(engine, cases)
        st.session_state["eval_suite"] = suite
    summary = st.session_state.get("eval_summary")
    if not summary:
        st.info("点击“运行评测集”后查看诊断评测指标。")
        return

    st.caption(f"当前结果：{st.session_state.get('eval_suite', 'unknown')}")
    metrics = st.columns(7)
    metrics[0].metric("用例数", summary.cases)
    metrics[1].metric("路由", fmt_metric(summary.route_accuracy))
    metrics[2].metric("覆盖判断", fmt_metric(summary.coverage_accuracy))
    metrics[3].metric("树选择", fmt_metric(summary.tree_selection_accuracy))
    metrics[4].metric("最终叶子", fmt_metric(summary.final_leaf_accuracy))
    metrics[5].metric("Gate", fmt_metric(summary.gate_accuracy))
    metrics[6].metric("误放行", summary.gate_mispass_count)
    extra = st.columns(6)
    extra[0].metric("Case-only 假设命中", fmt_metric(summary.case_only_hypothesis_hit_rate))
    extra[1].metric("下一动作命中", fmt_metric(summary.next_action_hit_rate))
    extra[2].metric("生产 Gate 安全", fmt_metric(summary.production_gate_safety_rate))
    extra[3].metric("返修/误判识别", fmt_metric(summary.rework_or_misdiagnosis_identification_rate))
    extra[4].metric("错树误诊", summary.wrong_tree_misdiagnosis_count)
    extra[5].metric("Guardrail 误路由", summary.guardrail_misroute_count)
    if summary.group_metrics:
        st.caption("分组指标")
        st.dataframe(
            [{"group": group, **metrics} for group, metrics in summary.group_metrics.items()],
            width="stretch",
            hide_index=True,
        )
    rows = [
        {
            "case_id": item.case_id,
            "group": item.eval_group,
            "mode": enum_value(item.diagnosis_mode),
            "expected_route": item.expected_route,
            "predicted_route": item.predicted_route,
            "coverage": enum_value(item.coverage_status),
            "tree": item.tree_id,
            "active_node": item.active_node_id,
            "gate": enum_value(item.gate_status),
            "route_ok": item.route_correct,
            "coverage_ok": item.coverage_correct,
            "tree_ok": item.tree_correct,
            "leaf_ok": item.leaf_correct,
            "gate_ok": item.gate_correct,
            "hypothesis_hit": item.hypothesis_hit,
            "action_hit": item.next_action_hit,
            "rework_hit": item.rework_or_misdiagnosis_identified,
            "gate_safe": item.production_gate_safe,
            "gate_mispass": item.gate_mispass,
            "guardrail_misroute": item.guardrail_misroute,
        }
        for item in summary.results
    ]
    st.dataframe(rows, width="stretch", hide_index=True)
    render_eval_drilldown(summary.results)
    if col_save.button("写入 datasets/eval_results", width="stretch"):
        paths = write_eval_outputs(summary, Path(datasets_dir) / "eval_results")
        st.success(f"已写入：{paths['summary']}，{paths['results']}，{paths['details']}")


def render_eval_drilldown(results: list[EvalCaseResult]) -> None:
    st.subheader("失败案例 Drill-down")
    failed = [item for item in results if item.failure_tags]
    if not failed:
        st.success("当前评测没有失败标签。")
        return

    left, right = st.columns([1, 1])
    group_options = ["全部", *sorted({item.eval_group or "UNKNOWN" for item in failed})]
    selected_group = left.selectbox("失败分组", group_options, key="eval_failure_group")
    tag_options = ["全部", *sorted({tag for item in failed for tag in item.failure_tags})]
    selected_tag = right.selectbox("失败标签", tag_options, key="eval_failure_tag")
    filtered = [
        item
        for item in failed
        if (selected_group == "全部" or (item.eval_group or "UNKNOWN") == selected_group)
        and (selected_tag == "全部" or selected_tag in item.failure_tags)
    ]
    st.dataframe(
        [
            {
                "case_id": item.case_id,
                "group": item.eval_group,
                "failure_tags": "；".join(item.failure_tags),
                "reason": item.short_error_reason,
                "expected_route": item.expected_route,
                "predicted_route": item.predicted_route,
                "expected_tree": item.expected_tree_id,
                "predicted_tree": item.tree_id,
                "expected_leaf": item.expected_leaf_symptom_id,
                "predicted_leaf": item.active_node_id,
                "expected_gate": enum_value(item.expected_gate_status),
                "predicted_gate": enum_value(item.gate_status),
                "expected_next_action": item.expected_next_action_hit_text,
            }
            for item in filtered
        ],
        width="stretch",
        hide_index=True,
    )
    if not filtered:
        st.info("当前筛选条件下没有失败案例。")
        return

    selected_case_id = st.selectbox(
        "选择失败案例",
        [item.case_id for item in filtered],
        format_func=lambda case_id: _eval_case_label(next(item for item in filtered if item.case_id == case_id)),
        key="eval_failure_case",
    )
    selected = next(item for item in filtered if item.case_id == selected_case_id)
    render_eval_case_detail(selected)


def render_eval_case_detail(item: EvalCaseResult) -> None:
    st.markdown(f"**{item.case_id} · {item.eval_group or 'UNKNOWN'}**")
    st.caption(item.short_error_reason or "无失败原因")
    left, right = st.columns([1, 1])
    with left:
        st.caption("Expected")
        st.json(
            {
                "route": item.expected_route,
                "tree_id": item.expected_tree_id,
                "leaf_symptom_id": item.expected_leaf_symptom_id,
                "gate_status": enum_value(item.expected_gate_status),
                "next_action": item.expected_next_action_hit_text,
                "action_keywords": item.expected_action_keywords,
            }
        )
    with right:
        st.caption("Predicted")
        st.json(
            {
                "route": item.predicted_route,
                "tree_id": item.tree_id,
                "active_node_id": item.active_node_id,
                "gate_status": enum_value(item.gate_status),
                "failure_tags": item.failure_tags,
            }
        )

    detail_tabs = st.tabs(["Planner 动作", "已执行检测", "证据摘要"])
    with detail_tabs[0]:
        if item.planned_actions:
            st.dataframe(item.planned_actions, width="stretch", hide_index=True)
        else:
            st.caption("无 Planner 动作。")
    with detail_tabs[1]:
        if item.executed_tests:
            st.dataframe(item.executed_tests, width="stretch", hide_index=True)
        else:
            st.caption("无已执行检测。")
    with detail_tabs[2]:
        if item.evidence_summary:
            st.dataframe(item.evidence_summary, width="stretch", hide_index=True)
        else:
            st.caption("无证据摘要。")


def _eval_case_label(item: EvalCaseResult) -> str:
    return f"{item.case_id} · {item.short_error_reason or '失败'}"


def render_dynamic_tree_request(current: DiagnosticState) -> None:
    request = current.fault_tree_generation_request
    if not request:
        return
    with st.expander("动态故障树候选请求", expanded=True):
        st.warning("这是开发态候选请求，不是已审核故障树；不会写入生产 TTL，也不能让 Gate PASS。")
        cols = st.columns(4)
        cols[0].metric("Request", request.request_id)
        cols[1].metric("审核状态", request.review_status)
        cols[2].metric("候选根因", len(request.candidate_root_hypotheses))
        cols[3].metric("建议检查", len(request.candidate_tests))
        st.markdown(f"**候选入口现象**：{request.candidate_start_symptom}")
        if request.candidate_failure_domain:
            st.caption(f"候选故障域：{request.candidate_failure_domain}")
        if request.candidate_root_hypotheses:
            st.caption("候选根因假设")
            st.write(request.candidate_root_hypotheses)
        if request.candidate_tests:
            st.caption("建议检查项")
            st.write(request.candidate_tests)
        if current.fault_tree_request_cluster:
            cluster = current.fault_tree_request_cluster
            st.divider()
            st.markdown("**候选请求聚类 / 审核流转**")
            cluster_cols = st.columns(4)
            cluster_cols[0].metric("Cluster", cluster.cluster_id)
            cluster_cols[1].metric("支持案例", f"{cluster.support_count}/{cluster.min_support_for_review}")
            cluster_cols[2].metric("状态", cluster.review_status)
            cluster_cols[3].metric("可流转", len(cluster.allowed_next_statuses))
            st.caption(cluster.recommended_next_step)
            if cluster.allowed_next_statuses:
                st.write("允许下一状态：", [str(item) for item in cluster.allowed_next_statuses])
            if cluster.supporting_case_ids:
                st.caption("支持案例/证据来源")
                st.write(cluster.supporting_case_ids)
            with st.expander("聚类 JSON", expanded=False):
                st.json(cluster.model_dump(mode="json"))
        with st.expander("本体建模约束 / tree_gen_agent.md 对齐", expanded=False):
            st.write(request.ontology_build_constraints)
            st.write(request.required_validation_steps)
        with st.expander("JSON 请求", expanded=False):
            st.json(request.model_dump(mode="json"))


def render_dynamic_tree_cluster_history(runs_dir: str) -> None:
    st.subheader("跨 runs 动态故障树聚类")
    st.caption("只在点击按钮时扫描 replay；聚类结果用于开发态审核，不会写入生产 TTL。")
    if st.button("扫描 runs 聚类", width="stretch"):
        records = load_replay_records(runs_dir)
        st.session_state["dynamic_tree_clusters"] = merge_dynamic_tree_clusters(records)
    clusters = st.session_state.get("dynamic_tree_clusters")
    if not clusters:
        st.info("点击“扫描 runs 聚类”后查看历史 case-only 候选树聚类。")
        return
    st.dataframe(
        [
            {
                "cluster_id": item.cluster_id,
                "status": item.review_status,
                "support": f"{item.support_count}/{item.min_support_for_review}",
                "start": item.representative_start_symptom,
                "domain": item.candidate_failure_domain,
                "requests": len(item.request_ids),
                "roots": len(item.merged_root_hypotheses),
                "tests": len(item.merged_tests),
                "next": item.recommended_next_step,
            }
            for item in clusters
        ],
        width="stretch",
        hide_index=True,
    )
    selected_cluster_id = st.selectbox(
        "选择聚类",
        [item.cluster_id for item in clusters],
        key="dynamic_tree_cluster_select",
    )
    selected = next(item for item in clusters if item.cluster_id == selected_cluster_id)
    st.json(selected.model_dump(mode="json"))


@st.cache_data(show_spinner=False)
def list_tree_generation_source_docs(raw_docs_dir: str) -> list[str]:
    root = Path(raw_docs_dir)
    if not root.exists():
        return []
    return [
        str(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUPPORTED_TREE_SOURCE_SUFFIXES and not path.name.startswith(".")
    ]


def render_tree_generation_entry(
    raw_docs_dir: str,
    chroma_dir: str,
    collection: str,
    chunk_size: int,
    overlap: int,
    tree_generation_dir: str,
    tree_proposals_dir: str,
    enable_llm: bool,
    llm_provider: str,
    openai_model: str,
) -> None:
    service = get_tree_generation_service(
        tree_generation_dir,
        tree_proposals_dir,
        enable_llm,
        llm_provider,
        openai_model,
    )
    st.subheader("批量文档预生成候选树")
    st.caption(
        "严格按 tree_ontology_schema.md 和 tree_gen_agent.md 的边界执行：LLM 优先多轮抽取本体草案和 transition，"
        "校验后做确定性 FaultTree 预览。结果只进入 DRAFT_TREE / TreeProposal，不写生产 TTL。"
    )
    with st.form("batch_tree_generation_form", clear_on_submit=False):
        title = st.text_input("生成任务标题", value="新故障类型候选树")
        description = st.text_area(
            "补充说明",
            value="请从质量报告/8D/维修资料中抽取入口现象、根因族、检查项、处置措施和诊断转移。",
            height=90,
        )
        source_docs = list_tree_generation_source_docs(raw_docs_dir)
        selected_docs = st.multiselect(
            "从 data/raw_docs 选择资料",
            source_docs,
            format_func=lambda item: str(Path(item).relative_to(Path(raw_docs_dir)))
            if Path(item).is_relative_to(Path(raw_docs_dir))
            else item,
        )
        uploaded = st.file_uploader(
            "或上传质量报告 / 8D / SOP / FMEA / 维修资料",
            type=[suffix.removeprefix(".") for suffix in sorted(SUPPORTED_TREE_SOURCE_SUFFIXES)],
            accept_multiple_files=True,
        )
        use_llm = st.checkbox("使用 LLM-first 多轮本体抽取/修复", value=enable_llm)
        st.caption(
            "本次 Tree Generation 配置："
            f"LLM={'启用' if service.settings.llm_enable and use_llm else '关闭'}；"
            f"provider={service.settings.llm_provider}；"
            f"fast={service.settings.deepseek_model_fast}；"
            f"pro={service.settings.deepseek_model_pro}"
        )
        if not use_llm:
            st.warning("未启用 LLM 时只会生成 LOW_CONF_DEBUG_DRAFT，用于调试流程，不应作为可用候选树。")
        elif not enable_llm:
            st.warning("侧边栏未启用 LLM 增强；即使勾选本项，本次服务配置仍会跳过 LLM。")
        submitted = st.form_submit_button("生成 DRAFT_TREE / TreeProposal", type="primary")
        if submitted:
            pending_dir = service.uploads_dir / "streamlit_pending"
            pending_dir.mkdir(parents=True, exist_ok=True)
            uploaded_paths = []
            for file in uploaded:
                target = pending_dir / file.name
                target.write_bytes(file.getbuffer())
                uploaded_paths.append(target)
            source_paths = [*selected_docs, *uploaded_paths]
            if not source_paths:
                st.error("请至少选择或上传一份资料。")
            else:
                progress_status = st.status("准备生成候选树...", expanded=True)
                progress_rows: list[dict] = []
                progress_table = st.empty()

                def on_progress(record: dict) -> None:
                    progress_rows.append(record)
                    label = record.get("label") or record.get("stage_id") or "生成阶段"
                    status = record.get("status")
                    duration = record.get("duration_ms")
                    suffix = f" · {duration} ms" if duration is not None else ""
                    progress_status.update(label=f"{label} · {status}{suffix}")
                    progress_status.write(record)
                    completed = [row for row in progress_rows if row.get("duration_ms") is not None]
                    if completed:
                        progress_table.dataframe(completed, width="stretch", hide_index=True)

                job = service.run_batch_job(
                    title=title,
                    description=description,
                    source_paths=source_paths,
                    use_llm=use_llm,
                    progress_callback=on_progress,
                )
                progress_status.update(
                    label="候选树生成完成" if job.status == "COMPLETED" else "候选树生成失败",
                    state="complete" if job.status == "COMPLETED" else "error",
                )
                st.session_state["last_tree_generation_job_id"] = job.job_id
                st.session_state["tree_generation_job_select"] = job.job_id
                if job.status == "COMPLETED":
                    st.success(f"已生成 {job.job_id}，并写入 TreeProposal。")
                else:
                    st.error(f"生成失败：{job.error}")

    jobs = service.load_jobs()
    if not jobs:
        st.info("暂无树生成任务。")
        return
    job_ids = [job.job_id for job in jobs]
    last_job_id = st.session_state.get("last_tree_generation_job_id")
    default_index = job_ids.index(last_job_id) if last_job_id in job_ids else 0
    if st.session_state.get("tree_generation_job_select") not in (None, *job_ids):
        st.session_state.pop("tree_generation_job_select", None)
    selected_job_id = st.selectbox(
        "查看生成任务",
        job_ids,
        index=default_index,
        key="tree_generation_job_select",
        format_func=lambda job_id: _tree_generation_job_label(next(job for job in jobs if job.job_id == job_id)),
    )
    selected_job = next(job for job in jobs if job.job_id == selected_job_id)
    render_tree_generation_job(
        service,
        selected_job,
        raw_docs_dir,
        chroma_dir,
        collection,
        chunk_size,
        overlap,
    )


def _tree_generation_job_label(job) -> str:
    updated_at = (job.updated_at or job.created_at or "").replace("T", " ")[:19]
    return f"{updated_at} · {job.job_id} · {job.status} · {job.title}"


def render_tree_generation_job(
    service: BatchTreeGenerationService,
    job,
    raw_docs_dir: str,
    chroma_dir: str,
    collection: str,
    chunk_size: int,
    overlap: int,
) -> None:
    cols = st.columns(6)
    cols[0].metric("Job", job.job_id)
    cols[1].metric("状态", job.status)
    cols[2].metric("输入文档", len(job.input_documents))
    cols[3].metric("Proposal", job.proposal.proposal_id if job.proposal else "N/A")
    cols[4].metric("树状态", job.proposal.status if job.proposal else "N/A")
    cols[5].metric("抽取质量", job.artifact.extraction_quality if job.artifact else "N/A")
    if job.error:
        st.error(job.error)
    if job.input_documents:
        st.caption("输入资料")
        st.dataframe([item.model_dump(mode="json") for item in job.input_documents], width="stretch", hide_index=True)
    if not job.artifact:
        return
    if job.artifact.extraction_quality == "LOW_CONF_DEBUG_DRAFT":
        st.warning("当前是规则低置信 debug 草案：只能用于检查流程，不应作为高质量候选树。")
    elif job.artifact.extraction_quality == "NEEDS_REPAIR_LLM_DRAFT":
        st.warning("当前 LLM 草案仍存在结构问题，需要继续修复或补充资料。")
    else:
        st.success("当前为 LLM-first 本体抽取草案，仍需 eval 和人工审核后才能晋升。")
    failed_llm_passes = [
        item.summary
        for item in job.artifact.extraction_passes
        if item.pass_type in {"RULE_LOW_CONF_DEBUG_FALLBACK", "LLM_VALIDATE_AND_REPAIR_FAILED"}
    ]
    if failed_llm_passes:
        with st.expander("LLM 抽取/修复诊断信息", expanded=True):
            for summary in failed_llm_passes:
                st.warning(summary)
    validation = job.artifact.validation_report
    if validation:
        if validation.is_valid:
            st.success("结构校验通过：可作为 DRAFT_TREE 候选草案进入人工审核前置流程。")
        else:
            st.warning("结构校验未通过：需要补充资料或人工修订。")
        metric_cols = st.columns(5)
        metric_cols[0].metric("start", validation.start_symptom_count)
        metric_cols[1].metric("root", validation.root_symptom_count)
        metric_cols[2].metric("test", validation.test_count)
        metric_cols[3].metric("transition", validation.transition_count)
        metric_cols[4].metric("errors", len(validation.errors))
        if validation.errors:
            st.error("；".join(validation.errors))
        if validation.warnings:
            st.warning("；".join(validation.warnings))
    detail_tabs = st.tabs(
        [
            "阶段耗时",
            "树结构图",
            "抽取轮次",
            "校验问题",
            "HITL 补全候选",
            "症状节点",
            "检查项",
            "处置措施",
            "诊断转移",
            "重建预览",
            "Proposal JSON",
        ]
    )
    with detail_tabs[0]:
        if job.artifact.stage_timings:
            st.dataframe(job.artifact.stage_timings, width="stretch", hide_index=True)
        else:
            st.info("暂无阶段耗时记录；请使用新版生成流程重新运行。")
    with detail_tabs[1]:
        mermaid = render_tree_generation_mermaid(job.artifact)
        st.markdown(mermaid)
        with st.expander("Mermaid 源码", expanded=False):
            st.code(mermaid, language="markdown")
    with detail_tabs[2]:
        st.json(job.artifact.extraction_plan.model_dump(mode="json"))
        if job.artifact.extraction_passes:
            st.dataframe(
                [item.model_dump(mode="json") for item in job.artifact.extraction_passes],
                width="stretch",
                hide_index=True,
            )
            for item in job.artifact.extraction_passes:
                with st.expander(f"{item.pass_id} · {item.pass_type}", expanded=False):
                    st.write(item.summary)
                    if item.output_counts:
                        st.caption("抽取计数")
                        st.json(item.output_counts)
                    if item.output_preview:
                        st.caption("抽取结果预览")
                        st.json(item.output_preview)
                    if item.raw_output:
                        st.caption("LLM 原始返回 JSON")
                        st.json(item.raw_output)
                    if item.raw_text:
                        st.caption("LLM 原始响应文本")
                        st.code(item.raw_text, language="json")
                    if item.issues_before:
                        st.caption("修复前问题")
                        st.dataframe(
                            [issue.model_dump(mode="json") for issue in item.issues_before],
                            width="stretch",
                            hide_index=True,
                        )
                    if item.issues_after:
                        st.caption("修复后问题")
                        st.dataframe(
                            [issue.model_dump(mode="json") for issue in item.issues_after],
                            width="stretch",
                            hide_index=True,
                        )
    with detail_tabs[3]:
        issues = validation.issues if validation else []
        if issues:
            st.dataframe([item.model_dump(mode="json") for item in issues], width="stretch", hide_index=True)
        else:
            st.success("暂无结构校验问题。")
    with detail_tabs[4]:
        hitl_items = generation_hitl_items(job.artifact)
        if hitl_items:
            st.info("以下字段来自 MISSING 或 EXTRACTED_INFERRED，应进入树生成阶段 HITL 补全/确认。")
            st.dataframe(hitl_items, width="stretch", hide_index=True)
            if st.button("生成专家建议选项", key=f"gen_hitl_suggestions_{job.job_id}"):
                rag = get_rag(raw_docs_dir, chroma_dir, collection, int(chunk_size), int(overlap))
                updated = service.generate_hitl_suggestions(job.job_id, rag=rag, use_llm=service.settings.llm_enable)
                if updated and updated.artifact:
                    st.session_state["last_tree_generation_job_id"] = updated.job_id
                    st.session_state["tree_generation_job_select"] = updated.job_id
                    st.success(f"已生成 {len(updated.artifact.hitl_suggestions)} 条专家建议。")
                    st.rerun()
            if job.artifact.hitl_suggestions:
                render_generation_hitl_suggestions(service, job)
        else:
            st.success("暂无必须补全的字段；仍可进入人工审核。")
    with detail_tabs[5]:
        st.dataframe([item.model_dump(mode="json") for item in job.artifact.symptoms], width="stretch", hide_index=True)
    with detail_tabs[6]:
        st.dataframe([item.model_dump(mode="json") for item in job.artifact.tests], width="stretch", hide_index=True)
    with detail_tabs[7]:
        st.dataframe([item.model_dump(mode="json") for item in job.artifact.measures], width="stretch", hide_index=True)
    with detail_tabs[8]:
        st.dataframe(
            [item.model_dump(mode="json") for item in job.artifact.transitions],
            width="stretch",
            hide_index=True,
        )
    with detail_tabs[9]:
        st.json(job.artifact.rebuilt_fault_tree)
    with detail_tabs[10]:
        st.json(job.proposal.model_dump(mode="json") if job.proposal else {})


def render_generation_hitl_suggestions(service: BatchTreeGenerationService, job) -> None:
    st.caption("专家建议仅作为候选选项；确认后才写回草稿树并重跑结构校验。")
    for suggestion in job.artifact.hitl_suggestions:
        current = suggestion.current_value if suggestion.current_value not in (None, "") else "缺失"
        label = (
            f"{suggestion.object_type} · {suggestion.object_id} · {suggestion.field} · "
            f"{suggestion.current_status}"
        )
        with st.expander(label, expanded=False):
            st.write(f"当前值：{current}")
            st.write(suggestion.generation_summary)
            if suggestion.source_refs:
                st.caption(f"原文引用：{', '.join(suggestion.source_refs)}")
            if suggestion.rag_refs:
                st.caption(f"RAG 引用：{', '.join(suggestion.rag_refs)}")
            option_labels = {
                option.option_id: (
                    f"{_format_hitl_value(option.value)} · {option.status} · "
                    f"{option.confidence:.0%}"
                )
                for option in suggestion.options
            }
            action_options = [*option_labels.keys(), "__KEEP__", "__MANUAL__", "__MORE__", "__REJECT__"]
            with st.form(f"hitl_decision_{job.job_id}_{suggestion.suggestion_id}"):
                selected = st.radio(
                    "确认动作",
                    action_options,
                    format_func=lambda value, labels=option_labels: labels.get(
                        value,
                        {
                            "__KEEP__": "确认保留当前值",
                            "__MANUAL__": "手动输入修订值",
                            "__MORE__": "需要补充资料",
                            "__REJECT__": "拒绝这些建议",
                        }.get(value, value),
                    ),
                )
                manual_value = st.text_area("手动修订值", value="", height=70)
                rationale = st.text_input("确认说明", value="")
                submitted = st.form_submit_button("确认写回")
            if submitted:
                decision = _build_hitl_decision(suggestion, selected, manual_value, rationale)
                updated = service.apply_hitl_decision(job.job_id, decision)
                if updated:
                    st.session_state["last_tree_generation_job_id"] = updated.job_id
                    st.session_state["tree_generation_job_select"] = updated.job_id
                    st.success("已写回草稿并重跑校验。")
                    st.rerun()
            for option in suggestion.options:
                st.markdown(f"**建议：{_format_hitl_value(option.value)}**")
                st.caption(f"{option.status} · 置信度 {option.confidence:.0%}")
                st.write(option.rationale)
                if option.risk_notes:
                    st.warning("；".join(option.risk_notes))


def _build_hitl_decision(suggestion, selected: str, manual_value: str, rationale: str) -> TreeGenerationHitlDecision:
    if selected == "__KEEP__":
        action = "KEEP_CURRENT"
        selected_option_id = None
        value = suggestion.current_value
    elif selected == "__MANUAL__":
        action = "MANUAL_VALUE"
        selected_option_id = None
        value = manual_value
    elif selected == "__MORE__":
        action = "NEEDS_MORE_EVIDENCE"
        selected_option_id = None
        value = None
    elif selected == "__REJECT__":
        action = "REJECT"
        selected_option_id = None
        value = None
    else:
        action = "ACCEPT_OPTION"
        selected_option_id = selected
        value = None
    return TreeGenerationHitlDecision(
        suggestion_id=suggestion.suggestion_id,
        object_type=suggestion.object_type,
        object_id=suggestion.object_id,
        field=suggestion.field,
        action=action,
        selected_option_id=selected_option_id,
        value=value,
        rationale=rationale or None,
    )


def _format_hitl_value(value: object) -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value)
    if value in (None, ""):
        return "无可靠补全值"
    return str(value)


def render_tree_proposal_review(tree_proposals_dir: str) -> None:
    store = TreeProposalStore(tree_proposals_dir)
    proposals = store.load_proposals()
    st.subheader("TreeProposal 审核")
    st.caption("审核动作只更新 proposal store 和 review log；不会写正式 TTL，也不能让 Gate PASS。")
    if not proposals:
        st.info("暂无 TreeProposal。请先从批量文档 Tree Generation 生成 DRAFT_TREE。")
        return
    status_options = ["ALL", *sorted({proposal.status.value for proposal in proposals})]
    selected_status = st.selectbox("状态筛选", status_options, key="proposal_status_filter")
    visible = proposals if selected_status == "ALL" else [p for p in proposals if p.status.value == selected_status]
    if not visible:
        st.info("当前筛选条件下没有 proposal。")
        return
    proposal_id = st.selectbox(
        "选择 proposal",
        [proposal.proposal_id for proposal in visible],
        format_func=lambda item: _proposal_label(
            next(proposal for proposal in visible if proposal.proposal_id == item)
        ),
        key="proposal_review_select",
    )
    proposal = next(item for item in visible if item.proposal_id == proposal_id)
    cols = st.columns(5)
    cols[0].metric("Proposal", proposal.proposal_id)
    cols[1].metric("状态", proposal.status)
    cols[2].metric("roots", len(proposal.root_cause_families))
    cols[3].metric("tests", len(proposal.candidate_tests))
    cols[4].metric("来源 job", proposal.source_job_id or "N/A")
    st.write(proposal.confidence_summary)
    if proposal.risk_notes:
        st.warning("；".join(proposal.risk_notes))

    review_logs = store.load_review_logs(proposal.proposal_id)
    case_links = store.load_case_links(proposal.proposal_id)
    eval_results = store.load_eval_results(proposal.proposal_id)
    tabs = st.tabs(["概览", "审核动作", "审核日志", "关联案例", "Eval", "Artifact"])
    with tabs[0]:
        st.json(proposal.model_dump(mode="json"))
    with tabs[1]:
        with st.form(f"proposal_review_{proposal.proposal_id}"):
            decision = st.radio(
                "审核结论",
                ["APPROVE", "REQUEST_CHANGES", "REJECT"],
                format_func=lambda value: {
                    "APPROVE": "批准进入下一状态",
                    "REQUEST_CHANGES": "请求修改，保持当前状态",
                    "REJECT": "拒绝",
                }[value],
            )
            reviewer = st.text_input("审核人", value="")
            rationale = st.text_area("审核理由", value="", height=90)
            required_changes_text = st.text_area("需要修改/补充项（每行一条）", value="", height=90)
            submitted = st.form_submit_button("写入审核日志")
        if submitted:
            if not rationale.strip():
                st.error("请填写审核理由。")
            else:
                required_changes = [line.strip() for line in required_changes_text.splitlines() if line.strip()]
                log = store.review_proposal(
                    proposal.proposal_id,
                    decision=decision,
                    reviewer=reviewer.strip() or None,
                    rationale=rationale.strip(),
                    required_changes=required_changes,
                )
                if log:
                    st.success(f"已写入审核日志：{log.from_status} -> {log.to_status}")
                    st.rerun()
                else:
                    st.error("未找到 proposal，审核写入失败。")
    with tabs[2]:
        if review_logs:
            st.dataframe([item.model_dump(mode="json") for item in review_logs], width="stretch", hide_index=True)
        else:
            st.info("暂无审核日志。")
    with tabs[3]:
        if case_links:
            st.dataframe([item.model_dump(mode="json") for item in case_links], width="stretch", hide_index=True)
        else:
            st.info("暂无关联 case link。")
    with tabs[4]:
        if st.button("运行 Tree Proposal Eval", key=f"run_tree_proposal_eval_{proposal.proposal_id}"):
            result = run_tree_proposal_eval(store, proposal.proposal_id)
            if result:
                st.success("Tree Proposal Eval 已完成并写入 eval_results.jsonl。")
                st.rerun()
            else:
                st.error("未找到 proposal，评测失败。")
        if eval_results:
            latest_eval = eval_results[-1]
            metrics = latest_eval.metrics
            metric_cols = st.columns(5)
            metric_cols[0].metric("schema", "PASS" if metrics.get("schema_valid") else "FAIL")
            metric_cols[1].metric("candidate", "READY" if metrics.get("candidate_ready") else "BLOCKED")
            metric_cols[2].metric("errors", metrics.get("validation_error_count", 0))
            metric_cols[3].metric("HITL pending", metrics.get("hitl_pending_count", 0))
            evidence_rate = metrics.get("evidence_binding_rate")
            metric_cols[4].metric(
                "evidence",
                f"{evidence_rate:.0%}" if isinstance(evidence_rate, float) else "N/A",
            )
            if latest_eval.unsafe_findings:
                st.warning("阻塞项：" + "；".join(latest_eval.unsafe_findings))
            st.dataframe([item.model_dump(mode="json") for item in eval_results], width="stretch", hide_index=True)
        else:
            st.info("暂无 Tree Proposal Eval 结果。")
    with tabs[5]:
        artifact_path = Path(tree_proposals_dir) / "artifacts" / proposal.proposal_id / "artifact.json"
        if artifact_path.exists():
            st.json(artifact_path.read_text(encoding="utf-8"))
        else:
            st.info("暂无 artifact 快照。")


def _proposal_label(proposal) -> str:
    updated_at = (proposal.updated_at or proposal.created_at or "").replace("T", " ")[:19]
    return f"{updated_at} · {proposal.proposal_id} · {proposal.status} · {proposal.candidate_start_symptom}"


base_settings = Settings()

with st.sidebar:
    st.header("配置")
    ttl_path = st.text_input("故障树 TTL", str(base_settings.fault_tree_ttl_path))
    raw_docs_dir = st.text_input("真实文档目录", str(base_settings.raw_docs_dir))
    chroma_dir = st.text_input("Chroma 缓存目录", str(base_settings.chroma_dir))
    runs_dir = st.text_input("Replay 目录", str(base_settings.runs_dir))
    datasets_dir = st.text_input("Datasets 目录", str(base_settings.datasets_dir))
    tree_generation_dir = st.text_input("Tree Generation 目录", str(base_settings.tree_generation_dir))
    tree_proposals_dir = st.text_input("Tree Proposals 目录", str(base_settings.tree_proposals_dir))
    collection = st.text_input("RAG collection", base_settings.rag_collection_name)
    chunk_size = st.number_input("Chunk size", min_value=200, max_value=3000, value=base_settings.rag_chunk_size)
    overlap = st.number_input("Chunk overlap", min_value=0, max_value=1000, value=base_settings.rag_chunk_overlap)
    llm_provider = st.selectbox("LLM provider", ["deepseek", "openai"], index=0)
    openai_model = st.text_input("OpenAI 备用模型", base_settings.openai_model)
    diagnosis_mode = st.selectbox("诊断模式", ["PRODUCTION", "DEVELOPMENT"], index=0)
    enable_llm = st.toggle("启用 LLM 增强", value=base_settings.llm_enable)

    col_a, col_b = st.columns(2)
    if col_a.button("重置诊断", width="stretch"):
        reset_case()
    if col_b.button("清理缓存", width="stretch"):
        st.cache_resource.clear()
        st.cache_data.clear()
        reset_case()

    if st.button("重建文档索引", width="stretch"):
        rag = get_rag(str(raw_docs_dir), str(chroma_dir), collection, int(chunk_size), int(overlap))
        count = rag.build_index()
        st.success(f"已索引 {count} 个文档块")

    try:
        count = scan_doc_count(str(raw_docs_dir), str(chroma_dir), collection, int(chunk_size), int(overlap))
        st.caption(f"当前可扫描文档块：{count}")
    except Exception as exc:
        st.warning(f"文档扫描失败：{exc}")

st.title("故障树诊断 Agent")

try:
    engine = get_engine(
        str(ttl_path),
        str(raw_docs_dir),
        str(chroma_dir),
        collection,
        int(chunk_size),
        int(overlap),
        str(runs_dir),
        str(datasets_dir),
        openai_model,
        enable_llm,
        llm_provider,
        diagnosis_mode,
    )
except Exception as exc:
    st.error(f"初始化失败：{exc}")
    st.stop()

mock_orders = load_mock_work_orders(str(raw_docs_dir))

with st.expander("树生成：批量文档预生成候选树", expanded=False):
    render_tree_generation_entry(
        str(raw_docs_dir),
        str(chroma_dir),
        collection,
        int(chunk_size),
        int(overlap),
        str(tree_generation_dir),
        str(tree_proposals_dir),
        enable_llm,
        llm_provider,
        openai_model,
    )

with st.expander("TreeProposal 审核", expanded=False):
    render_tree_proposal_review(str(tree_proposals_dir))

st.subheader("工单驱动诊断输入")
input_mode = st.radio(
    "输入方式",
    ["选择 mock 工单", "粘贴工单文本", "仅输入故障现象"],
    horizontal=True,
    key="input_mode",
)
selected_diag_mode = DiagnosisMode(diagnosis_mode)

if input_mode == "选择 mock 工单":
    labels = [f"{item['order_id']} · {item.get('title') or item['failure_phenomenon']}" for item in mock_orders]
    if labels:
        selected_idx = st.selectbox(
            "工单",
            options=list(range(len(labels))),
            format_func=lambda idx: labels[idx],
            key="mock_order_select",
        )
        selected_order = WorkOrder.model_validate(mock_orders[selected_idx])
        with st.container(border=True):
            st.markdown(f"**{selected_order.order_id} · {selected_order.title or selected_order.failure_phenomenon}**")
            st.write(selected_order.description or selected_order.failure_phenomenon)
            if selected_order.executed_checks:
                st.caption(
                    f"该 mock 工单已包含 {len(selected_order.executed_checks)} 条已执行检查，"
                    "可能会直接推进到 Gate/报告。"
                )
        with st.form("mock_order_form", clear_on_submit=False):
            submitted = st.form_submit_button("开始/重新诊断", type="primary")
            if submitted:
                new_state = engine.start_work_order(selected_order, selected_diag_mode)
                st.session_state["diag_state"] = new_state
                coverage = f"覆盖={new_state.coverage_decision.status if new_state.coverage_decision else 'N/A'}"
                st.session_state["last_start_message"] = start_message(engine, new_state, coverage)
                st.rerun()
    else:
        st.warning("未在 data/raw_docs/ 中找到 mock_work_orders_*.md")
elif input_mode == "粘贴工单文本":
    with st.form("pasted_order_form", clear_on_submit=False):
        pasted_order = st.text_area("工单文本（自由文本 / Markdown / OCR）", height=220)
        submitted = st.form_submit_button("开始/重新诊断", type="primary")
        if submitted:
            parsed = parse_pasted_work_order_text(pasted_order, settings=engine.settings)
            if parsed:
                new_state = engine.start_work_order(parsed, selected_diag_mode)
                st.session_state["diag_state"] = new_state
                coverage = f"覆盖={new_state.coverage_decision.status if new_state.coverage_decision else 'N/A'}"
                st.session_state["last_start_message"] = start_message(engine, new_state, coverage)
                st.rerun()
            else:
                st.error("粘贴内容为空，无法创建工单。")
else:
    with st.form("phenomenon_form", clear_on_submit=False):
        raw_input = st.text_area("故障现象", value="车门无法关闭", height=90)
        col1, col2, col3 = st.columns(3)
        vehicle_project = col1.text_input("车型项目", value="X01")
        factory = col2.text_input("工厂", value="A工厂")
        station = col3.text_input("工位", value="总装线-P12")
        vin_text = st.text_input("VIN 列表（逗号分隔）", value="VIN001,VIN002")
        timestamp = st.text_input("时间", value="")
        submitted = st.form_submit_button("开始/重新诊断", type="primary")
        if submitted:
            request = IntakeRequest(
                raw_input=raw_input,
                vehicle_project=vehicle_project or None,
                vin_list=[item.strip() for item in vin_text.split(",") if item.strip()],
                factory=factory or None,
                station=station or None,
                timestamp=timestamp or None,
            )
            new_state = engine.start_case(request)
            st.session_state["diag_state"] = new_state
            coverage = f"覆盖={new_state.coverage_decision.status if new_state.coverage_decision else 'N/A'}"
            st.session_state["last_start_message"] = start_message(engine, new_state, coverage)
            st.rerun()

current = state()
if not current:
    tab_start, tab_eval_idle = st.tabs(["开始诊断", "Eval"])
    with tab_start:
        st.info("填写诊断输入后开始。故障树和 RAG 资源已经缓存，不会因普通控件变化重复初始化。")
    with tab_eval_idle:
        render_eval_tab(engine, str(raw_docs_dir), str(datasets_dir))
else:
    if st.session_state.get("last_start_message"):
        st.success(st.session_state["last_start_message"])
    if current.coverage_decision and current.coverage_decision.status == CoverageStatus.UNSUPPORTED:
        if current.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY:
            st.warning("该工单不在现有故障树覆盖范围内：当前仅做开发态探索，Gate 会保持 GRAY，不可生产放行。")
        else:
            st.error("该工单故障类型不在现有故障树覆盖范围内：生产态直接 FAIL，请补充对应故障树后再诊断。")
    if current.gate_result and current.gate_result.status != "PASS":
        reasons = "；".join(current.gate_result.blocking_reasons or current.gate_result.risk_notes)
        if reasons:
            st.caption(f"Gate 未 PASS 原因：{reasons}")
    if current.planned_actions:
        st.info("Planner 已生成待人工检测项，请进入“当前节点 / HITL”页录入检测结果。")
    elif current.final_report and not (
        current.coverage_decision
        and current.coverage_decision.status == CoverageStatus.UNSUPPORTED
        and current.diagnosis_mode == DiagnosisMode.PRODUCTION
    ):
        st.info("当前工单已完成自动推进或已有足够证据，请查看“证据与报告”页的 Gate 与结论。")

    top = st.columns(6)
    top[0].metric("Case", current.case_id)
    top[1].metric("覆盖", current.coverage_decision.status if current.coverage_decision else "N/A")
    top[2].metric("活动树", current.active_tree_id or "N/A")
    top[3].metric("当前节点", node_label(engine, current.active_node_id))
    top[4].metric("候选路径", len(current.candidate_paths))
    top[5].metric("Gate", current.gate_result.status if current.gate_result else "N/A")

    tab_overview, tab_plan, tab_report, tab_replay, tab_eval = st.tabs(
        ["诊断概览", "当前节点 / HITL", "证据与报告", "Replay", "Eval"]
    )

    with tab_overview:
        left, right = st.columns([1, 1])
        with left:
            st.subheader("工单与分类")
            if current.work_order:
                st.markdown(f"**{current.work_order.order_id} · {current.work_order.title or ''}**")
                st.write(current.work_order.description or "")
                meta = {
                    "VIN": current.work_order.vin,
                    "业务域": current.work_order.business_domain,
                    "车型项目": current.work_order.vehicle_project,
                    "工厂/场景": current.work_order.station_or_scene,
                    "期望路由": current.work_order.expected_route,
                    "期望故障树": current.work_order.expected_fault_tree,
                    "抽取方式": current.work_order.extraction_method,
                }
                st.json({key: value for key, value in meta.items() if value})
                if current.work_order.extraction_quality_notes:
                    st.caption("抽取质量提示：" + "；".join(current.work_order.extraction_quality_notes))
                if current.work_order.executed_checks:
                    st.caption("工单已执行检查")
                    for check in current.work_order.executed_checks:
                        st.write(f"- {check}")
            st.json(current.classification.model_dump(mode="json") if current.classification else {})
        with right:
            st.subheader("归一化与覆盖")
            st.json(current.intake.model_dump(mode="json") if current.intake else {})
            if current.coverage_decision:
                st.json(current.coverage_decision.model_dump(mode="json"))

        with st.expander("匹配故障树与候选路径", expanded=False):
            for tree in current.matched_trees:
                st.markdown(f"**{tree.tree_id} · {tree.name}**")
                st.write(tree.description or "")
                st.caption(tree.applicable_scope or "")
            for path in current.candidate_paths:
                st.write(f"**{path.tree_id}** · score={path.score}")
                st.code(engine.repository.describe_path(path), language="text")
        if current.data_quality_notes:
            st.warning("\n".join(current.data_quality_notes))
        if current.rework_risk and current.rework_risk.is_rework_suspected:
            with st.expander("返修 / 前次误判风险", expanded=True):
                st.metric("风险置信度", f"{current.rework_risk.confidence:.2f}")
                st.json(current.rework_risk.model_dump(mode="json"))
        render_dynamic_tree_request(current)

    with tab_plan:
        left, right = st.columns([1, 1])
        with left:
            st.subheader("当前节点与可选分支")
            render_active_node(engine, current)
        with right:
            st.subheader("下一步人工检测")
            if not current.planned_actions:
                st.success("当前没有待执行人工检测。")
                st.caption("如果 Gate 已 PASS，说明工单已有检查证据足以到达叶子原因；否则请查看 Gate 的阻塞项。")
            else:
                selected_action = select_action(current.planned_actions)
                render_action(engine, current, selected_action)

    with tab_report:
        st.subheader("已执行检测")
        if current.executed_tests:
            st.dataframe(
                [item.model_dump(mode="json") for item in current.executed_tests],
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("暂无")

        st.subheader("证据链")
        if current.evidence_chain:
            st.dataframe(
                [item.model_dump(mode="json") for item in current.evidence_chain],
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("暂无")

        if current.gate_result:
            st.subheader("Gate")
            st.json(current.gate_result.model_dump(mode="json"))
        if current.final_report:
            st.subheader("Markdown 报告")
            st.markdown(current.final_report.markdown)
            with st.expander("JSON 报告", expanded=False):
                st.json(current.final_report.model_dump(mode="json"))

    with tab_replay:
        st.subheader("Replay Trace")
        st.caption("每次开始诊断或提交人工检测都会写入 runs/*.jsonl，并同步保存在当前 session。")
        st.json([record.model_dump(mode="json") for record in current.replay_trace])
        render_dynamic_tree_cluster_history(str(runs_dir))

    with tab_eval:
        render_eval_tab(engine, str(raw_docs_dir), str(datasets_dir))
