from __future__ import annotations

from copy import deepcopy
from hashlib import sha1

from ft_diag_agent.case_only_planner import CaseOnlyPlanner
from ft_diag_agent.classifier import WorkOrderClassifier
from ft_diag_agent.dynamic_tree import DynamicFaultTreeRequestBuilder
from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.gate import Gate
from ft_diag_agent.intake import normalize_intake
from ft_diag_agent.models import (
    CoverageStatus,
    DiagnosisMode,
    DiagnosticAction,
    DiagnosticState,
    DiagnosticWorkflowPhase,
    EvidenceItem,
    ExecutedTest,
    ExploratoryFinding,
    IntakeRequest,
    TreeChangeType,
    TreeProposal,
    TreeProposalKind,
    TreeProposalStatus,
    WorkOrder,
)
from ft_diag_agent.planner import Planner
from ft_diag_agent.rag import DocumentRag
from ft_diag_agent.replay import ReplayStore
from ft_diag_agent.report import ReportGenerator
from ft_diag_agent.rework_guard import ReworkGuard
from ft_diag_agent.settings import Settings
from ft_diag_agent.tools import ToolRegistry, build_default_registry
from ft_diag_agent.work_orders import work_order_to_intake_text


class DiagnosticEngine:
    def __init__(
        self,
        repository: RdfFaultTreeRepository,
        rag: DocumentRag,
        settings: Settings | None = None,
        registry: ToolRegistry | None = None,
    ):
        self.repository = repository
        self.rag = rag
        self.settings = settings or Settings()
        self.registry = registry or build_default_registry(repository, rag)
        self.planner = Planner(repository)
        self.case_only_planner = CaseOnlyPlanner(self.settings)
        self.dynamic_tree_builder = DynamicFaultTreeRequestBuilder()
        self.classifier = WorkOrderClassifier(repository, rag, self.settings)
        self.rework_guard = ReworkGuard()
        self.gate = Gate()
        self.reporter = ReportGenerator(repository, self.settings)
        self.replay_store = ReplayStore(self.settings.runs_dir)
        self._graph = build_langgraph_app(self)
        self._graph_replay_before: DiagnosticState | None = None

    def start_case(self, request: IntakeRequest) -> DiagnosticState:
        state = DiagnosticState(intake_request=request)
        state.work_order = WorkOrder(
            order_id=state.case_id,
            title=request.raw_input[:60],
            failure_phenomenon=request.raw_input,
            vin=",".join(request.vin_list) if request.vin_list else None,
            vehicle_project=request.vehicle_project,
            source=request.factory,
            station_or_scene=request.station,
            description=request.raw_input,
            raw_text=request.raw_input,
            extraction_method="SIMPLE_INPUT",
        )
        return self.run_until_hitl(state)

    def start_work_order(
        self,
        work_order: WorkOrder,
        diagnosis_mode: DiagnosisMode | None = None,
    ) -> DiagnosticState:
        state = DiagnosticState(
            case_id=work_order.order_id,
            work_order=work_order,
            diagnosis_mode=diagnosis_mode or _diagnosis_mode(self.settings.diagnosis_mode),
            intake_request=IntakeRequest(
                raw_input=work_order.failure_phenomenon,
                vehicle_project=work_order.vehicle_project,
                factory=work_order.source,
                station=work_order.station_or_scene,
                extra_context={
                    "work_order_id": work_order.order_id,
                    "vin": work_order.vin,
                    "created_time": work_order.created_time,
                    "business_domain": work_order.business_domain,
                    "severity": work_order.severity,
                    "description": work_order.description,
                    "executed_checks": work_order.executed_checks,
                    "expected_route": work_order.expected_route,
                    "expected_fault_tree": work_order.expected_fault_tree,
                },
            ),
        )
        return self.run_until_hitl(state)

    def run_until_hitl(self, state: DiagnosticState) -> DiagnosticState:
        before = deepcopy(state)
        if self._graph:
            try:
                self._graph_replay_before = before
                result = self._graph.invoke(state)
                return DiagnosticState.model_validate(result)
            finally:
                self._graph_replay_before = None
        return self._run_until_hitl_direct(state, before)

    def _run_until_hitl_direct(self, state: DiagnosticState, before: DiagnosticState | None = None) -> DiagnosticState:
        before = before or deepcopy(state)
        if not state.intake and state.intake_request:
            state.intake = normalize_intake(state.intake_request, self.settings)
        if state.work_order and not state.classification:
            self.classify_work_order(state)
        self._mark_workflow_phase(state, DiagnosticWorkflowPhase.CLASSIFIED)
        if state.coverage_decision and state.coverage_decision.status == CoverageStatus.UNSUPPORTED:
            if state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY:
                self._mark_workflow_phase(state, DiagnosticWorkflowPhase.RETRIEVING_CONTEXT)
                self.retrieve_evidence(state)
                self.assess_rework_risk(state)
                self._mark_workflow_phase(state, DiagnosticWorkflowPhase.PLANNING)
                self.plan(state)
                self.plan_case_only_exploration(state)
                self.propose_dynamic_tree_request(state)
        else:
            self._mark_workflow_phase(state, DiagnosticWorkflowPhase.RETRIEVING_CONTEXT)
            self.retrieve_tree(state)
            self.retrieve_evidence(state)
            self.apply_existing_work_order_checks(state)
            self.assess_rework_risk(state)
            self._mark_workflow_phase(state, DiagnosticWorkflowPhase.PLANNING)
            self.plan(state)
            self.plan_case_only_exploration(state)
            self.propose_dynamic_tree_request(state)
            self.propose_tree_change_candidate(state)
        self.evaluate_gate(state)
        self.annotate_after_gate(state)
        self.generate_report(state)
        self._record_replay(before, state)
        state.touch()
        return state

    def work_order_intake(self, state: DiagnosticState) -> None:
        if state.work_order or not state.intake_request:
            return
        request = state.intake_request
        state.work_order = WorkOrder(
            order_id=state.case_id,
            title=request.raw_input[:60],
            failure_phenomenon=request.raw_input,
            vin=",".join(request.vin_list) if request.vin_list else None,
            vehicle_project=request.vehicle_project,
            source=request.factory,
            station_or_scene=request.station,
            description=request.raw_input,
            raw_text=request.raw_input,
            extraction_method="SIMPLE_INPUT",
        )

    def normalize_state_intake(self, state: DiagnosticState) -> None:
        if not state.intake and state.intake_request:
            state.intake = normalize_intake(state.intake_request, self.settings)

    def retrieve_tree(self, state: DiagnosticState) -> None:
        if state.coverage_decision and state.coverage_decision.status == CoverageStatus.UNSUPPORTED:
            state.matched_trees = []
            state.candidate_paths = []
            state.candidate_causes = []
            return
        if not state.intake:
            return
        if state.active_tree_id:
            tree = self.repository.trees.get(state.active_tree_id)
            matches = (
                [(tree, state.classification.confidence if state.classification else 1.0, ["classification"])]
                if tree
                else []
            )
        else:
            matches = self.repository.search_trees(state.intake.phenomenon)
        state.matched_trees = [tree for tree, _, _ in matches]
        paths = []
        for tree, score, reasons in matches:
            for path in self.repository.enumerate_paths(tree.tree_id):
                path.score = score
                path.match_reasons = reasons
                paths.append(path)
        state.candidate_paths = paths
        state.candidate_causes = self.repository.make_candidate_causes(paths)
        state.data_quality_notes = list(dict.fromkeys(self.repository.data_quality_notes))
        if state.active_tree_id and not state.active_node_id:
            state.active_node_id = self.repository.start_node_id(state.active_tree_id)

    def classify_work_order(self, state: DiagnosticState) -> None:
        if not state.work_order:
            return
        classification = self.classifier.classify(state.work_order, state.diagnosis_mode)
        state.classification = classification
        state.coverage_decision = self.classifier.coverage_decision(classification)
        state.diagnosis_mode = classification.diagnosis_mode
        if classification.coverage_status == CoverageStatus.COVERED and classification.tree_id:
            state.active_tree_id = classification.tree_id
            state.active_node_id = self.repository.start_node_id(classification.tree_id)
        self._mark_workflow_phase(state, DiagnosticWorkflowPhase.CLASSIFIED)

    def retrieve_evidence(self, state: DiagnosticState) -> None:
        if not state.intake:
            return
        self._mark_workflow_phase(state, DiagnosticWorkflowPhase.RETRIEVING_CONTEXT)
        if any(e.source_type == "RAG" for e in state.evidence_chain):
            return
        if state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY and state.work_order:
            query = work_order_to_intake_text(state.work_order)
            evidence = self._case_only_rag_evidence(query)
        else:
            evidence = self.rag.search(state.intake.phenomenon, top_k=5)
        for item in evidence:
            # Keep RAG evidence as contextual until a human/tool confirms exact node/cause support.
            state.evidence_chain.append(item)

    def _case_only_rag_evidence(self, query: str) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        evidence.extend(self.rag.search(query, top_k=4, filters={"doc_type": "WORK_ORDER"}))
        evidence.extend(self.rag.search(query, top_k=2, filters={"doc_type": "FMEA"}))
        evidence.extend(self.rag.search(query, top_k=2, filters={"doc_type": "SOP"}))
        evidence.extend(self.rag.search(query, top_k=4))
        deduped = []
        seen: set[str] = set()
        for item in evidence:
            if item.evidence_id in seen or item.source_id in seen:
                continue
            seen.add(item.evidence_id)
            seen.add(item.source_id)
            deduped.append(item)
            if len(deduped) >= 8:
                break
        return deduped

    def plan(self, state: DiagnosticState) -> None:
        self._mark_workflow_phase(state, DiagnosticWorkflowPhase.PLANNING)
        actions = self.planner.plan(state)
        counter_actions = self._rework_counter_actions(state)
        state.planned_actions = _dedupe_actions([*counter_actions, *actions])
        if state.rework_risk and state.rework_risk.risk_notes:
            for action in state.planned_actions:
                action.risk_notes = list(dict.fromkeys([*action.risk_notes, *state.rework_risk.risk_notes]))

    def _rework_counter_actions(self, state: DiagnosticState) -> list[DiagnosticAction]:
        if not state.rework_risk or not state.rework_risk.recommended_checks:
            return []
        executed = {test.test_id for test in state.executed_tests}
        source_refs = list(
            dict.fromkeys(ref for case in state.rework_risk.similar_cases for ref in case.source_refs)
        )
        evidence_ids = [
            case.evidence_id for case in state.rework_risk.similar_cases if case.evidence_id
        ]
        risk_notes = list(
            dict.fromkeys(
                [
                    *state.rework_risk.risk_notes,
                    *[f"避免重复无效动作：{item}" for item in state.rework_risk.avoided_repeat_actions],
                ]
            )
        )
        actions: list[DiagnosticAction] = []
        for index, check in enumerate(state.rework_risk.recommended_checks, start=1):
            test_id = f"REWORK_COUNTER_{index:02d}"
            if test_id in executed:
                continue
            actions.append(
                DiagnosticAction(
                    action_type="REWORK_COUNTER_CHECK",
                    test_id=test_id,
                    tool_name="human_input",
                    priority=index,
                    blocking=True,
                    expected_result_schema={
                        "result": "str",
                        "value": "str|number|bool|null",
                        "passed": "bool|null",
                        "notes": "str|null",
                    },
                    reason=(
                        f"返修/误判反证检查：{check}。"
                        "目的：先验证前次处置无效或相邻根因风险，避免重复发布低置信结论。"
                    ),
                    source_refs=source_refs,
                    planner_source="REWORK_GUARD",
                    evidence_ids=evidence_ids,
                    confidence=max(state.rework_risk.confidence, 0.5),
                    risk_notes=risk_notes,
                )
            )
        return actions

    def assess_rework_risk(self, state: DiagnosticState) -> None:
        state.rework_risk = self.rework_guard.assess(state, self.rag)

    def plan_case_only_exploration(self, state: DiagnosticState) -> None:
        if state.diagnosis_mode != DiagnosisMode.CASE_ONLY_EXPLORATORY:
            return
        result = self.case_only_planner.plan(state)
        state.case_only_hypotheses = result.hypotheses
        state.case_only_plan = result.plan
        state.planned_actions = _dedupe_actions([*state.planned_actions, *result.actions])

    def propose_dynamic_tree_request(self, state: DiagnosticState) -> None:
        request = self.dynamic_tree_builder.build(state)
        state.fault_tree_generation_request = request
        state.fault_tree_request_cluster = self.dynamic_tree_builder.build_cluster(state, request)

    def propose_tree_change_candidate(self, state: DiagnosticState) -> None:
        if not _eligible_for_tree_change(state):
            state.tree_change_proposal = None
            return
        change_types = _tree_change_types(state)
        candidate_tests = _tree_change_candidate_tests(state)
        root_families = _tree_change_root_families(state)
        source_case_ids = list(
            dict.fromkeys(
                [
                    state.case_id,
                    state.work_order.order_id if state.work_order else None,
                ]
            )
        )
        source_case_ids = [item for item in source_case_ids if item]
        proposal_id = _tree_change_proposal_id(state.active_tree_id or "UNKNOWN_TREE", source_case_ids, change_types)
        start_name = _tree_start_name(self.repository, state.active_tree_id) or (
            state.work_order.failure_phenomenon if state.work_order else state.case_id
        )
        drift_signals = _tree_change_drift_signals(state)
        state.tree_change_proposal = TreeProposal(
            proposal_id=proposal_id,
            proposal_kind=TreeProposalKind.TREE_CHANGE,
            status=TreeProposalStatus.DRAFT_TREE,
            source_type="COVERED_TREE_DRIFT",
            target_tree_id=state.active_tree_id,
            target_tree_version=_tree_version(self.repository, state.active_tree_id),
            change_types=change_types,
            change_summary="；".join(drift_signals[:5]) or "covered case 出现已有树变更候选信号。",
            change_patch={
                "mode": "review_patch_only",
                "target_tree_id": state.active_tree_id,
                "active_node_id": state.active_node_id,
                "candidate_tests": candidate_tests,
                "root_cause_families": root_families,
                "drift_signals": drift_signals,
            },
            drift_signals=drift_signals,
            phenomenon_bucket=_normalize_tree_change_bucket(start_name),
            candidate_start_symptom=start_name,
            candidate_failure_domain=state.classification.matched_phenomenon if state.classification else None,
            root_cause_families=root_families,
            candidate_tests=candidate_tests,
            candidate_transitions=[
                f"PATCH {state.active_tree_id or 'UNKNOWN_TREE'} / {item.value}"
                for item in change_types
            ],
            source_case_ids=source_case_ids,
            evidence_ids=[item.evidence_id for item in state.evidence_chain],
            source_refs=_tree_change_source_refs(state),
            confidence_summary=(
                "由已覆盖诊断中的反证、返修/误判或工艺漂移信号生成的 TREE_CHANGE proposal；"
                "仅作为版本化 patch 审核输入，不会直接修改生产 TTL。"
            ),
            risk_notes=[
                "TREE_CHANGE proposal 不能让 Gate PASS，也不能直接改写 Released Tree。",
                "必须经过 change eval、专家审核、shadow/release 材料和 TTL 审计后才能发布。",
            ],
            allowed_next_statuses=[TreeProposalStatus.CANDIDATE_TREE, TreeProposalStatus.REJECTED],
        )

    def apply_existing_work_order_checks(self, state: DiagnosticState) -> None:
        if not state.work_order or not state.active_tree_id:
            return
        existing = {item.test_id for item in state.executed_tests}
        for check in state.work_order.executed_checks:
            test_id = _extract_test_id(check)
            semantic_node_id = _semantic_node_from_check(check, state.active_tree_id)
            if not test_id and semantic_node_id and f"SEMANTIC_{semantic_node_id}" not in existing:
                from ft_diag_agent.models import EvidenceItem

                state.executed_tests.append(
                    ExecutedTest(
                        test_id=f"SEMANTIC_{semantic_node_id}",
                        result=check,
                        passed=True,
                        notes="来自工单已执行检查的语义根因证据",
                    )
                )
                state.evidence_chain.append(
                    EvidenceItem(
                        source_type="WORK_ORDER",
                        source_id=state.work_order.order_id,
                        claim=check,
                        supports_node_id=semantic_node_id,
                        supports_cause_id=semantic_node_id,
                        strength=0.65,
                        raw_payload={"work_order_id": state.work_order.order_id, "semantic_node_id": semantic_node_id},
                    )
                )
                state.active_node_id = semantic_node_id
                existing.add(f"SEMANTIC_{semantic_node_id}")
                continue
            if not test_id or test_id in existing:
                continue
            positive = _check_supports_branch(check)
            transition = self.repository.transition_for_test(test_id, state.active_node_id, state.active_tree_id)
            if positive and not transition:
                transition = self.repository.transition_for_test(test_id, tree_id=state.active_tree_id)
            supports_node_id = transition.target_id if transition and positive else None
            supports_cause_id = supports_node_id if positive and _is_root(self.repository, supports_node_id) else None
            state.executed_tests.append(
                ExecutedTest(
                    test_id=test_id,
                    result=check,
                    passed=positive,
                    notes="来自工单已执行检查",
                )
            )
            from ft_diag_agent.models import EvidenceItem

            state.evidence_chain.append(
                EvidenceItem(
                    source_type="WORK_ORDER",
                    source_id=state.work_order.order_id,
                    claim=check,
                    supports_node_id=supports_node_id,
                    supports_cause_id=supports_cause_id,
                    strength=0.65 if positive else 0.25,
                    raw_payload={"work_order_id": state.work_order.order_id},
                )
            )
            if transition and positive:
                state.active_node_id = transition.target_id
            existing.add(test_id)

    def evaluate_gate(self, state: DiagnosticState) -> None:
        state.gate_result = self.gate.evaluate(state)

    def annotate_after_gate(self, state: DiagnosticState) -> None:
        if not state.gate_result:
            return
        pending = _pending_human_actions(state)
        state.waiting_action_ids = [action.action_id for action in pending]
        state.waiting_for_human = bool(pending)
        if pending:
            self._mark_workflow_phase(
                state,
                DiagnosticWorkflowPhase.WAITING_HITL,
                f"等待 {len(pending)} 个 HITL 人工检查结果。",
            )
            return
        phase = {
            "PASS": DiagnosticWorkflowPhase.GATE_PASS,
            "GRAY": DiagnosticWorkflowPhase.GATE_GRAY,
            "FAIL": DiagnosticWorkflowPhase.GATE_FAIL,
        }.get(str(state.gate_result.status), DiagnosticWorkflowPhase.GATE_GRAY)
        self._mark_workflow_phase(state, phase)

    def generate_report(self, state: DiagnosticState) -> None:
        state.final_report = self.reporter.generate(state)

    def apply_human_test(
        self,
        state: DiagnosticState,
        test_payload: dict,
        accepted: bool | None = True,
    ) -> DiagnosticState:
        before = deepcopy(state)
        call = self.registry.call("human_input", test_payload)
        state.tool_calls.append(call)
        output_test = call.output_payload.get("executed_test")
        if output_test:
            from ft_diag_agent.models import ExecutedTest

            state.executed_tests.append(ExecutedTest.model_validate(output_test))
        state.evidence_chain.extend(call.evidence_items)
        state.human_feedback.append(test_payload)
        if state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY:
            state.case_only_findings.append(
                ExploratoryFinding(
                    action_id=str(test_payload.get("action_id") or ""),
                    test_id=str(test_payload.get("test_id") or "CASE_ONLY_FINDING"),
                    result=str(test_payload.get("result") or ""),
                    supports_hypothesis_ids=[
                        str(test_payload["supports_cause_id"])
                    ]
                    if test_payload.get("passed") is True and test_payload.get("supports_cause_id")
                    else [],
                    refutes_hypothesis_ids=[
                        str(test_payload["supports_cause_id"])
                    ]
                    if test_payload.get("passed") is False and test_payload.get("supports_cause_id")
                    else [],
                    evidence_id=call.evidence_items[0].evidence_id if call.evidence_items else None,
                    notes=test_payload.get("notes"),
                )
            )
        if self._graph:
            try:
                self._graph_replay_before = before
                result = self._graph.invoke(state)
                return DiagnosticState.model_validate(result)
            finally:
                self._graph_replay_before = None
        self.assess_rework_risk(state)
        self.plan(state)
        self.plan_case_only_exploration(state)
        self.propose_dynamic_tree_request(state)
        self.evaluate_gate(state)
        self.annotate_after_gate(state)
        self.generate_report(state)
        self._record_replay(before, state, accepted=accepted, human_decision=test_payload)
        state.touch()
        return state

    def execute_auto_actions(self, state: DiagnosticState) -> None:
        # Reserved for future model/tool executed tests. Current product policy treats every
        # fault-tree test as HITL until the upstream fault-tree generator annotates executor type.
        return None

    def _mark_workflow_phase(
        self,
        state: DiagnosticState,
        phase: DiagnosticWorkflowPhase,
        note: str | None = None,
    ) -> None:
        state.workflow_phase = phase
        if note:
            state.workflow_notes = list(dict.fromkeys([*state.workflow_notes, note]))

    def _record_replay(
        self,
        before: DiagnosticState,
        after: DiagnosticState,
        accepted: bool | None = None,
        human_decision: dict | None = None,
    ) -> None:
        record = self.replay_store.snapshot(
            state_before=before,
            state_after=after,
            accepted=accepted,
            human_decision=human_decision,
        )
        after.replay_trace.append(record)
        self.replay_store.append(after.case_id, record)


def build_langgraph_app(engine: DiagnosticEngine):
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return None

    def work_order_intake(state: DiagnosticState) -> DiagnosticState:
        engine.work_order_intake(state)
        return state

    def normalize_intake_node(state: DiagnosticState) -> DiagnosticState:
        engine.normalize_state_intake(state)
        return state

    def classify_work_order(state: DiagnosticState) -> DiagnosticState:
        if state.work_order and not state.classification:
            engine.classify_work_order(state)
        return state

    def route_by_coverage(state: DiagnosticState) -> DiagnosticState:
        return state

    def retrieve_tree(state: DiagnosticState) -> DiagnosticState:
        engine.retrieve_tree(state)
        return state

    def retrieve_evidence(state: DiagnosticState) -> DiagnosticState:
        engine.retrieve_evidence(state)
        return state

    def plan(state: DiagnosticState) -> DiagnosticState:
        engine.plan(state)
        return state

    def plan_case_only(state: DiagnosticState) -> DiagnosticState:
        engine.plan_case_only_exploration(state)
        return state

    def propose_dynamic_tree_request(state: DiagnosticState) -> DiagnosticState:
        engine.propose_dynamic_tree_request(state)
        return state

    def propose_tree_change_candidate(state: DiagnosticState) -> DiagnosticState:
        engine.propose_tree_change_candidate(state)
        return state

    def assess_rework_risk(state: DiagnosticState) -> DiagnosticState:
        engine.assess_rework_risk(state)
        return state

    def gate(state: DiagnosticState) -> DiagnosticState:
        engine.evaluate_gate(state)
        return state

    def wait_hitl(state: DiagnosticState) -> DiagnosticState:
        engine.annotate_after_gate(state)
        return state

    def gate_pass(state: DiagnosticState) -> DiagnosticState:
        engine._mark_workflow_phase(state, DiagnosticWorkflowPhase.GATE_PASS)
        state.waiting_for_human = False
        state.waiting_action_ids = []
        return state

    def gate_gray(state: DiagnosticState) -> DiagnosticState:
        engine._mark_workflow_phase(state, DiagnosticWorkflowPhase.GATE_GRAY)
        state.waiting_for_human = False
        state.waiting_action_ids = []
        return state

    def gate_fail(state: DiagnosticState) -> DiagnosticState:
        engine._mark_workflow_phase(state, DiagnosticWorkflowPhase.GATE_FAIL)
        state.waiting_for_human = False
        state.waiting_action_ids = []
        return state

    def report(state: DiagnosticState) -> DiagnosticState:
        engine.generate_report(state)
        return state

    def replay(state: DiagnosticState) -> DiagnosticState:
        before = engine._graph_replay_before or deepcopy(state)
        engine._record_replay(before, state)
        state.touch()
        return state

    def execute_auto_actions(state: DiagnosticState) -> DiagnosticState:
        engine.execute_auto_actions(state)
        return state

    def route_after_coverage(state: DiagnosticState) -> str:
        if state.coverage_decision and state.coverage_decision.status == CoverageStatus.UNSUPPORTED:
            if state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY:
                return "case_only"
            return "unsupported_production"
        return "covered"

    def route_after_evidence(state: DiagnosticState) -> str:
        if state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY:
            return "case_only"
        return "covered"

    def route_after_planning(state: DiagnosticState) -> str:
        if any(action.tool_name != "human_input" for action in state.planned_actions):
            return "auto_tools"
        return "gate"

    def route_after_gate(state: DiagnosticState) -> str:
        if _pending_human_actions(state):
            return "wait_hitl"
        if not state.gate_result:
            return "gray"
        if state.gate_result.status == "PASS":
            return "pass"
        if state.gate_result.status == "FAIL":
            return "fail"
        return "gray"

    graph = StateGraph(DiagnosticState)
    graph.add_node("work_order_intake", work_order_intake)
    graph.add_node("normalize_intake", normalize_intake_node)
    graph.add_node("classify_work_order", classify_work_order)
    graph.add_node("route_by_coverage", route_by_coverage)
    graph.add_node("retrieve_tree", retrieve_tree)
    graph.add_node("retrieve_evidence", retrieve_evidence)
    graph.add_node("apply_existing_checks", lambda state: (engine.apply_existing_work_order_checks(state), state)[1])
    graph.add_node("plan", plan)
    graph.add_node("plan_case_only", plan_case_only)
    graph.add_node("propose_dynamic_tree_request", propose_dynamic_tree_request)
    graph.add_node("propose_tree_change_candidate", propose_tree_change_candidate)
    graph.add_node("assess_rework_risk", assess_rework_risk)
    graph.add_node("execute_auto_actions", execute_auto_actions)
    graph.add_node("gate", gate)
    graph.add_node("wait_hitl", wait_hitl)
    graph.add_node("gate_pass", gate_pass)
    graph.add_node("gate_gray", gate_gray)
    graph.add_node("gate_fail", gate_fail)
    graph.add_node("report", report)
    graph.add_node("replay", replay)
    graph.set_entry_point("work_order_intake")
    graph.add_edge("work_order_intake", "normalize_intake")
    graph.add_edge("normalize_intake", "classify_work_order")
    graph.add_edge("classify_work_order", "route_by_coverage")
    graph.add_conditional_edges(
        "route_by_coverage",
        route_after_coverage,
        {
            "covered": "retrieve_tree",
            "case_only": "retrieve_evidence",
            "unsupported_production": "gate",
        },
    )
    graph.add_edge("retrieve_tree", "retrieve_evidence")
    graph.add_conditional_edges(
        "retrieve_evidence",
        route_after_evidence,
        {
            "covered": "apply_existing_checks",
            "case_only": "assess_rework_risk",
        },
    )
    graph.add_edge("apply_existing_checks", "assess_rework_risk")
    graph.add_edge("assess_rework_risk", "plan")
    graph.add_edge("plan", "plan_case_only")
    graph.add_edge("plan_case_only", "propose_dynamic_tree_request")
    graph.add_edge("propose_dynamic_tree_request", "propose_tree_change_candidate")
    graph.add_conditional_edges(
        "propose_tree_change_candidate",
        route_after_planning,
        {
            "auto_tools": "execute_auto_actions",
            "gate": "gate",
        },
    )
    graph.add_edge("execute_auto_actions", "gate")
    graph.add_conditional_edges(
        "gate",
        route_after_gate,
        {
            "wait_hitl": "wait_hitl",
            "pass": "gate_pass",
            "gray": "gate_gray",
            "fail": "gate_fail",
        },
    )
    graph.add_edge("wait_hitl", "report")
    graph.add_edge("gate_pass", "report")
    graph.add_edge("gate_gray", "report")
    graph.add_edge("gate_fail", "report")
    graph.add_edge("report", "replay")
    graph.add_edge("replay", END)
    return graph.compile()


def _extract_test_id(text: str) -> str | None:
    import re

    match = re.search(r"(?<![A-Z0-9])T\d{3}(?![A-Z0-9])", text)
    return match.group(0) if match else None


def _dedupe_actions(actions: list[DiagnosticAction]) -> list[DiagnosticAction]:
    deduped: list[DiagnosticAction] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for action in actions:
        key = (action.action_type, action.test_id, action.target_cause_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    return deduped


def _pending_human_actions(state: DiagnosticState) -> list[DiagnosticAction]:
    executed_test_ids = {item.test_id for item in state.executed_tests}
    pending: list[DiagnosticAction] = []
    for action in state.planned_actions:
        if action.tool_name != "human_input" or not action.blocking:
            continue
        if action.test_id and action.test_id in executed_test_ids:
            continue
        pending.append(action)
    return pending


def _check_supports_branch(text: str) -> bool:
    explicit_positive_markers = [
        "无输出",
        "输出0v",
        "输出0V",
        "无响应",
        "无动作",
        "动作无力",
        "无完整锁止",
        "超差",
        "松脱",
        "退针",
        "缺失",
        "损坏",
        "失效",
        "不稳定",
        "不到位",
        "不一致",
        "接触不良",
        "开路",
        "短路",
        "卡滞",
        "抖动",
        "压降",
        "不合格",
        "数据线异常",
        "虚焊",
    ]
    if any(marker in text for marker in explicit_positive_markers):
        return True
    hard_negative_markers = [
        "未超",
        "合格",
        "无明显",
        "无异常",
        "未读取到",
        "无变形",
        "无明显干涉",
        "无干涉",
        "标准内",
        "导通正常",
    ]
    negative_markers = [
        "正常",
        "通过",
        "稳定",
    ]
    positive_markers = [
        "异常",
        "失效",
        "损坏",
        "缺失",
        "偏差",
        "偏低",
        "偏高",
        "不一致",
        "不稳定",
        "无力",
        "无法",
        "未锁止",
        "不匹配",
        "报",
        "提示",
        "不可见",
        "变形",
        "0v",
        "0V",
    ]
    if any(marker in text for marker in hard_negative_markers):
        return False
    if any(marker in text for marker in positive_markers):
        return True
    if any(marker in text for marker in negative_markers):
        return False
    return True


def _semantic_node_from_check(text: str, tree_id: str | None) -> str | None:
    normalized = text.lower()
    if tree_id == "FT_001":
        if any(marker in normalized for marker in ["显示ic", "显示屏模组", "原屏", "屏幕模组"]):
            return "S008"
        if any(marker in normalized for marker in ["压接不良", "退针", "线束针脚", "b+压降"]):
            return "S007"
    if tree_id == "FT_002":
        if "门框" in text and any(marker in text for marker in ["变形", "超差"]):
            return "S110"
        if "执行器" in text and any(marker in text for marker in ["异常", "无力", "无动作"]):
            return "S105"
    return None


def _is_root(repository: RdfFaultTreeRepository, node_id: str | None) -> bool:
    if not node_id:
        return False
    node = repository.get_symptom(node_id)
    return bool(node and node.level == "root")


def _diagnosis_mode(value: str) -> DiagnosisMode:
    try:
        return DiagnosisMode(value.upper())
    except ValueError:
        return DiagnosisMode.PRODUCTION


def _eligible_for_tree_change(state: DiagnosticState) -> bool:
    if state.diagnosis_mode == DiagnosisMode.CASE_ONLY_EXPLORATORY:
        return False
    if not state.active_tree_id or not state.work_order:
        return False
    return bool(_tree_change_drift_signals(state))


def _tree_change_types(state: DiagnosticState) -> list[TreeChangeType]:
    text = _tree_change_text(state)
    types: list[TreeChangeType] = []
    if any(marker in text for marker in ["工艺变更", "工艺调整", "标准更新", "适用范围", "车型切换"]):
        types.append(TreeChangeType.UPDATE_SCOPE)
    if any(marker in text for marker in ["阈值", "限值", "标准值", "超差标准"]):
        types.append(TreeChangeType.UPDATE_THRESHOLD)
    if any(marker in text for marker in ["检测项", "检查项", "无法执行", "不适用", "替代检测"]):
        types.append(TreeChangeType.UPDATE_TEST)
    if any(marker in text for marker in ["条件", "判定", "误判", "不支持", "排除"]):
        types.append(TreeChangeType.UPDATE_TRANSITION_CONDITION)
    if any(marker in text for marker in ["废弃", "取消", "禁用"]):
        types.append(TreeChangeType.DEPRECATE_TEST)
    if any(marker in text for marker in ["新增分支", "新增根因", "新根因"]):
        types.append(TreeChangeType.ADD_BRANCH)
    if not types and state.rework_risk:
        types.append(TreeChangeType.UPDATE_TRANSITION_CONDITION)
        if state.rework_risk.recommended_checks:
            types.append(TreeChangeType.UPDATE_TEST)
    return list(dict.fromkeys(types or [TreeChangeType.UPDATE_TEST]))


def _tree_change_drift_signals(state: DiagnosticState) -> list[str]:
    text = _tree_change_text(state)
    signals: list[str] = []
    marker_groups = {
        "工艺/适用范围变化": ["工艺变更", "工艺调整", "标准更新", "适用范围", "车型切换"],
        "检测项变化或不可执行": ["检测项", "检查项", "无法执行", "不适用", "替代检测"],
        "阈值/判定标准变化": ["阈值", "限值", "标准值", "超差标准"],
        "分支反证或误判": ["误判", "不支持", "排除", "无效", "仍复现", "无改善"],
        "新分支或新根因": ["新增分支", "新增根因", "新根因"],
    }
    for label, markers in marker_groups.items():
        if any(marker in text for marker in markers):
            signals.append(label)
    if state.rework_risk:
        if state.rework_risk.is_rework_suspected:
            signals.append("返修/复现风险")
        if state.rework_risk.is_prior_misdiagnosis_suspected:
            signals.append("前次误判或处置无效")
        signals.extend(state.rework_risk.risk_notes)
    return list(dict.fromkeys(signals))


def _tree_change_candidate_tests(state: DiagnosticState) -> list[str]:
    tests = [
        *(state.rework_risk.recommended_checks if state.rework_risk else []),
        *(
            action.reason
            for action in state.planned_actions
            if action.action_type in {"REWORK_COUNTER_CHECK", "CONFIRMATION_CHECK"}
        ),
        *(test.result for test in state.executed_tests if _looks_like_tree_change_signal(test.result)),
    ]
    return [item[:160] for item in list(dict.fromkeys(item for item in tests if item))][:12]


def _tree_change_root_families(state: DiagnosticState) -> list[str]:
    roots = [
        cause.name
        for cause in state.candidate_causes
        if cause.name and (cause.cause_id == state.active_node_id or cause.score >= 0.5)
    ]
    if state.active_node_id:
        roots.append(state.active_node_id)
    return list(dict.fromkeys(roots))[:12]


def _tree_change_text(state: DiagnosticState) -> str:
    parts = []
    if state.work_order:
        parts.extend(
            [
                state.work_order.title or "",
                state.work_order.failure_phenomenon,
                state.work_order.description or "",
                " ".join(state.work_order.executed_checks),
                state.work_order.repair_action or "",
            ]
        )
    parts.extend(test.result + " " + (test.notes or "") for test in state.executed_tests)
    if state.rework_risk:
        parts.extend(
            [
                *state.rework_risk.prior_actions,
                *state.rework_risk.ineffective_actions,
                *state.rework_risk.avoided_repeat_actions,
                *state.rework_risk.recommended_checks,
                *state.rework_risk.risk_notes,
            ]
        )
    return "\n".join(parts)


def _looks_like_tree_change_signal(text: str) -> bool:
    return any(
        marker in text
        for marker in ["工艺变更", "阈值", "误判", "不支持", "排除", "无效", "仍复现", "无法执行", "不适用"]
    )


def _tree_change_source_refs(state: DiagnosticState) -> list[str]:
    refs: list[str] = []
    refs.extend(ref for item in state.evidence_chain for ref in item.source_refs)
    if state.work_order and state.work_order.source_path:
        refs.append(state.work_order.source_path)
    return list(dict.fromkeys(refs))


def _tree_start_name(repository: RdfFaultTreeRepository, tree_id: str | None) -> str | None:
    if not tree_id:
        return None
    start_id = repository.start_node_id(tree_id)
    symptom = repository.get_symptom(start_id) if start_id else None
    return symptom.name if symptom else None


def _tree_version(repository: RdfFaultTreeRepository, tree_id: str | None) -> str | None:
    if not tree_id:
        return None
    tree = repository.trees.get(tree_id)
    return tree.version if tree else None


def _tree_change_proposal_id(
    target_tree_id: str,
    source_case_ids: list[str],
    change_types: list[TreeChangeType],
) -> str:
    basis = "|".join(
        [
            target_tree_id,
            ",".join(sorted(source_case_ids)),
            ",".join(sorted(item.value for item in change_types)),
        ]
    )
    return f"TP-CHG-{sha1(basis.encode()).hexdigest()[:10]}"


def _normalize_tree_change_bucket(value: str) -> str:
    return "".join(value.lower().split())[:48] or "unknown"
