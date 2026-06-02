from __future__ import annotations

from ft_diag_agent.fault_tree import RdfFaultTreeRepository
from ft_diag_agent.models import (
    DiagnosticAction,
    DiagnosticPath,
    DiagnosticState,
    ExecutorType,
    TestExecutionSpec,
)


class Planner:
    def __init__(self, repository: RdfFaultTreeRepository):
        self.repository = repository

    def plan(self, state: DiagnosticState, limit: int = 5) -> list[DiagnosticAction]:
        if state.active_node_id and state.active_tree_id:
            return self._plan_from_active_node(state, limit)
        return self._plan_from_candidate_paths(state, limit)

    def execution_spec_for_test(self, test_id: str) -> TestExecutionSpec:
        return TestExecutionSpec(
            test_id=test_id,
            executor_type=ExecutorType.HUMAN,
            tool_name="human_input",
            input_schema={"test_id": "str", "context": "dict"},
            result_schema={
                "result": "str",
                "value": "str|number|bool|null",
                "passed": "bool|null",
                "notes": "str|null",
            },
        )

    def _plan_from_active_node(self, state: DiagnosticState, limit: int) -> list[DiagnosticAction]:
        executed = {test.test_id for test in state.executed_tests}
        transitions = self.repository.outgoing_transitions(state.active_node_id, state.active_tree_id)
        actions: list[DiagnosticAction] = self._confirmation_actions_for_active_node(state, executed)
        for index, transition in enumerate(transitions):
            if transition.test_id in executed:
                continue
            test = self.repository.get_test(transition.test_id)
            spec = self.execution_spec_for_test(transition.test_id)
            actions.append(
                DiagnosticAction(
                    action_type="TEST",
                    target_node_id=transition.target_id,
                    target_cause_id=transition.target_id,
                    test_id=transition.test_id,
                    tool_name=spec.tool_name,
                    priority=10 + index,
                    blocking=True,
                    expected_result_schema=spec.result_schema,
                    reason=(
                        f"当前节点 {state.active_node_id} 通过检测 "
                        f"{test.display_name if test else transition.test_id} "
                        f"判断是否流转到 {transition.target_id}；条件：{transition.condition or '缺失'}"
                    ),
                )
            )
        actions.sort(key=lambda item: (item.priority, item.test_id or ""))
        return actions[:limit]

    def _confirmation_actions_for_active_node(
        self,
        state: DiagnosticState,
        executed: set[str],
    ) -> list[DiagnosticAction]:
        if not state.active_node_id:
            return []
        confirmation = _CONFIRMATION_CHECKS_BY_NODE.get(state.active_node_id)
        if not confirmation:
            return []
        test_id = f"CONFIRM_{state.active_node_id}"
        if test_id in executed:
            return []
        node = self.repository.get_symptom(state.active_node_id)
        support_claims = [
            evidence.claim
            for evidence in state.evidence_chain
            if evidence.supports_node_id == state.active_node_id or evidence.supports_cause_id == state.active_node_id
        ][:3]
        spec = self.execution_spec_for_test(test_id)
        reason_bits = [
            f"当前已定位到 {state.active_node_id} · {node.name if node else state.active_node_id}，建议做发布前补证",
            confirmation,
        ]
        if support_claims:
            reason_bits.append(f"已有支持证据：{'；'.join(support_claims)}")
        return [
            DiagnosticAction(
                action_type="CONFIRMATION_CHECK",
                target_node_id=state.active_node_id,
                target_cause_id=state.active_node_id,
                test_id=test_id,
                tool_name=spec.tool_name,
                priority=8,
                blocking=False,
                expected_result_schema=spec.result_schema,
                reason="；".join(reason_bits),
                planner_source="LEAF_CONFIRMATION",
                confidence=0.72,
                risk_notes=["已到达疑似根因节点，建议补充确认性检测以降低返修和误判风险。"],
            )
        ]

    def _plan_from_candidate_paths(self, state: DiagnosticState, limit: int) -> list[DiagnosticAction]:
        executed = {test.test_id for test in state.executed_tests}
        evidence_by_cause: dict[str, float] = {}
        for evidence in state.evidence_chain:
            if evidence.supports_cause_id:
                evidence_by_cause[evidence.supports_cause_id] = (
                    evidence_by_cause.get(evidence.supports_cause_id, 0.0) + evidence.strength
                )

        actions: list[DiagnosticAction] = []
        for path in state.candidate_paths:
            root_id = path.root_cause_id
            for depth, test_id in enumerate(path.test_ids):
                if test_id in executed:
                    continue
                test = self.repository.get_test(test_id)
                target_node_id = _target_node_for_test(path, test_id)
                spec = self.execution_spec_for_test(test_id)
                priority = self._priority(
                    path=path,
                    depth=depth,
                    root_id=root_id,
                    evidence_boost=evidence_by_cause.get(root_id, 0.0) if root_id else 0.0,
                )
                reason_bits = [
                    f"验证路径 {self.repository.describe_path(path)}",
                    f"当前未完成检测 {test.display_name if test else test_id}",
                ]
                cause_evidence = evidence_by_cause.get(root_id, 0.0) if root_id else 0.0
                if cause_evidence:
                    reason_bits.append(f"已有证据强度 {cause_evidence:.2f}")
                actions.append(
                    DiagnosticAction(
                        action_type="TEST",
                        target_node_id=target_node_id,
                        target_cause_id=root_id,
                        test_id=test_id,
                        tool_name=spec.tool_name,
                        priority=priority,
                        blocking=True,
                        expected_result_schema=spec.result_schema,
                        reason="；".join(reason_bits),
                    )
                )
                break

        actions.sort(key=lambda item: (item.priority, item.test_id or ""))
        deduped: list[DiagnosticAction] = []
        seen: set[tuple[str | None, str | None]] = set()
        for action in actions:
            key = (action.test_id, action.target_cause_id)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(action)
            if len(deduped) >= limit:
                break
        return deduped

    def _priority(
        self,
        path: DiagnosticPath,
        depth: int,
        root_id: str | None,
        evidence_boost: float,
    ) -> int:
        remaining_depth = max(0, len(path.test_ids) - depth)
        root_bonus = 0 if root_id else 3
        evidence_bonus = int(min(evidence_boost * 2, 3))
        return max(1, 10 + depth + remaining_depth + root_bonus - evidence_bonus)


def _target_node_for_test(path: DiagnosticPath, test_id: str) -> str | None:
    try:
        index = path.test_ids.index(test_id)
    except ValueError:
        return None
    if index + 1 < len(path.node_ids):
        return path.node_ids[index + 1]
    return path.root_cause_id


_CONFIRMATION_CHECKS_BY_NODE = {
    "S005": "导出完整日志；关联软件版本与崩溃栈；补充镜像hash，排除系统镜像损坏分支。",
    "S006": "补充PMIC输出电压测量；复核EN脚、3V3/1V8输出；隔离软件启动分支。",
    "S007": "测B+/ACC输入；测量负载下压降；检查连接器二次锁、端子退针和端子拉脱力。",
    "S008": "确认背光是否正常；检查显示IC数据链路；在更换显示屏前复核显示链路证据。",
    "S009": "检查BL_EN与背光驱动输出；复测亮度调节，确认不是显示屏数据链路问题。",
    "S010": "执行屏线摇摆测试；检查端子锁止状态；复核LVDS屏线连接和接触稳定性。",
    "S011": "测量主板阻抗；复核主板关键供电网络对地阻抗；避免仅按黑屏现象更换屏幕。",
    "S012": "读取启动日志；校验镜像hash；导出完整日志并关联软件版本与崩溃栈。",
    "S105": "外部驱动门锁执行器；确认二道锁信号；采集执行器电流波形；排除锁扣偏移。",
    "S106": "测量锁扣位置；做涂色啮合验证，确认锁扣位置偏离装配基准。",
    "S107": "测量门间隙面差；检查铰链变形，确认门体下沉或姿态异常。",
    "S108": "检查密封条入槽；检查密封条压缩量；排除锁扣偏移；识别前次维修无效风险。",
    "S109": "读取门锁状态信号；区分机械锁止与信号异常，避免把传感器问题误判为机械问题。",
    "S110": "测量门框基准点；排除密封条干涉，确认车门控制模块或门框基准异常。",
    "S111": "检查外把手回位；区分执行器损坏；复核门锁和传感器线束接触状态。",
}
