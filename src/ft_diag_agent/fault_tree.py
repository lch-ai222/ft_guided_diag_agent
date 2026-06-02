from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterable
from difflib import SequenceMatcher
from pathlib import Path

from ft_diag_agent.models import (
    CandidateCause,
    DiagnosticPath,
    DiagnosticTest,
    FaultTree,
    Measure,
    SymptomNode,
    Transition,
)

QTL = "http://lianshan.ai/ontology/qlt_fta#"


class GraphRepository(ABC):
    @abstractmethod
    def search_trees(self, phenomenon: str, top_k: int = 5) -> list[tuple[FaultTree, float, list[str]]]:
        raise NotImplementedError

    @abstractmethod
    def enumerate_paths(self, tree_id: str) -> list[DiagnosticPath]:
        raise NotImplementedError

    @abstractmethod
    def get_test(self, test_id: str) -> DiagnosticTest | None:
        raise NotImplementedError

    @abstractmethod
    def get_symptom(self, symptom_id: str) -> SymptomNode | None:
        raise NotImplementedError


class RdfFaultTreeRepository(GraphRepository):
    def __init__(self, ttl_path: str | Path):
        try:
            from rdflib import Graph, Namespace
            from rdflib.namespace import RDF
        except ImportError as exc:
            raise RuntimeError(
                "rdflib is required for TTL parsing. Install project dependencies first."
            ) from exc

        self.ttl_path = Path(ttl_path)
        self.graph = Graph()
        self.graph.parse(self.ttl_path, format="turtle")
        self.qtl = Namespace(QTL)
        self.rdf = RDF

        self.symptoms: dict[str, SymptomNode] = {}
        self.symptoms_by_uri: dict[str, SymptomNode] = {}
        self.tests: dict[str, DiagnosticTest] = {}
        self.tests_by_uri: dict[str, DiagnosticTest] = {}
        self.measures: dict[str, Measure] = {}
        self.measures_by_uri: dict[str, Measure] = {}
        self.transitions: dict[str, Transition] = {}
        self.trees: dict[str, FaultTree] = {}
        self._outgoing: dict[str, list[Transition]] = defaultdict(list)
        self._tree_paths: dict[str, list[DiagnosticPath]] = {}
        self.data_quality_notes: list[str] = []

        self._load()

    def _objects(self, subject, predicate_name: str):
        return self.graph.objects(subject, self.qtl[predicate_name])

    def _one(self, subject, predicate_name: str):
        return next(iter(self._objects(subject, predicate_name)), None)

    def _text(self, subject, predicate_name: str) -> str | None:
        value = self._one(subject, predicate_name)
        return str(value) if value is not None else None

    def _texts(self, subject, predicate_name: str) -> list[str]:
        values: list[str] = []
        for value in self._objects(subject, predicate_name):
            text = str(value)
            if text:
                values.extend([part.strip() for part in text.split(",") if part.strip()])
        return values

    def _float(self, subject, predicate_name: str) -> float | None:
        raw = self._text(subject, predicate_name)
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            self.data_quality_notes.append(f"Invalid numeric field {predicate_name}: {raw}")
            return None

    def _local_id(self, uri: object, prefix: str) -> str:
        text = str(uri)
        if "#" in text:
            text = text.rsplit("#", 1)[1]
        if text.startswith(prefix):
            return text.removeprefix(prefix)
        return text

    def _load(self) -> None:
        self._load_symptoms()
        self._load_tests()
        self._load_measures()
        self._load_transitions()
        self._load_trees()
        self._build_paths()

    def _load_symptoms(self) -> None:
        for subject in self.graph.subjects(self.rdf.type, self.qtl.FailureSymptom):
            symptom_id = self._text(subject, "symptomId") or self._local_id(subject, "FailureSymptom_")
            measure_ids = [
                self.measures_by_uri.get(str(uri), None).measure_id
                if str(uri) in self.measures_by_uri
                else self._local_id(uri, "OntologyMeasure_")
                for uri in self._objects(subject, "hasMeasure")
            ]
            node = SymptomNode(
                uri=str(subject),
                symptom_id=symptom_id,
                name=self._text(subject, "symptomName") or symptom_id,
                name_status=self._text(subject, "symptomNameStatus"),
                level=self._text(subject, "symptomLevel"),
                description=self._text(subject, "symptomDesc"),
                description_status=self._text(subject, "symptomDescStatus"),
                chunk_ids=self._texts(subject, "symptomChunkIds"),
                measure_ids=measure_ids,
            )
            self.symptoms[node.symptom_id] = node
            self.symptoms_by_uri[node.uri] = node

    def _load_tests(self) -> None:
        for subject in self.graph.subjects(self.rdf.type, self.qtl.OntologyTest):
            test_id = self._text(subject, "testId") or self._local_id(subject, "OntologyTest_")
            test = DiagnosticTest(
                uri=str(subject),
                test_id=test_id,
                name=self._text(subject, "testName"),
                name_status=self._text(subject, "testNameStatus"),
                unit=self._text(subject, "testUnit"),
                unit_status=self._text(subject, "testUnitStatus"),
                hilim=self._float(subject, "testHilim"),
                hilim_status=self._text(subject, "testHilimStatus"),
                lolim=self._float(subject, "testLolim"),
                lolim_status=self._text(subject, "testLolimStatus"),
                rule=self._text(subject, "testRule"),
                rule_status=self._text(subject, "testRuleStatus"),
                target=self._text(subject, "testTarget"),
                target_status=self._text(subject, "testTargetStatus"),
                description=self._text(subject, "testDesc"),
                description_status=self._text(subject, "testDescStatus"),
                chunk_ids=self._texts(subject, "testChunkIds"),
            )
            if not test.name:
                self.data_quality_notes.append(f"Test {test.test_id} has no testName")
            self.tests[test.test_id] = test
            self.tests_by_uri[test.uri] = test

    def _load_measures(self) -> None:
        for subject in self.graph.subjects(self.rdf.type, self.qtl.OntologyMeasure):
            measure_id = self._text(subject, "measureId") or self._local_id(subject, "OntologyMeasure_")
            measure = Measure(
                uri=str(subject),
                measure_id=measure_id,
                name=self._text(subject, "measureName"),
                name_status=self._text(subject, "measureNameStatus"),
                description=self._text(subject, "measureDesc"),
                description_status=self._text(subject, "measureDescStatus"),
                chunk_ids=self._texts(subject, "measureChunkIds"),
            )
            self.measures[measure.measure_id] = measure
            self.measures_by_uri[measure.uri] = measure

        for node in self.symptoms.values():
            node.measure_ids[:] = [
                measure_id for measure_id in node.measure_ids if measure_id in self.measures
            ]

    def _load_transitions(self) -> None:
        for subject in self.graph.subjects(self.rdf.type, self.qtl.SymptomTransition):
            source_uri = self._one(subject, "transitionSource")
            target_uri = self._one(subject, "transitionTarget")
            test_uri = self._one(subject, "testId")
            source = self.symptoms_by_uri.get(str(source_uri)) if source_uri else None
            target = self.symptoms_by_uri.get(str(target_uri)) if target_uri else None
            test = self.tests_by_uri.get(str(test_uri)) if test_uri else None
            transition_id = self._local_id(subject, "SymptomTransition_")
            if not source or not target or not test:
                self.data_quality_notes.append(f"Transition {transition_id} has missing endpoints/test")
                continue
            transition = Transition(
                uri=str(subject),
                transition_id=transition_id,
                source_id=source.symptom_id,
                target_id=target.symptom_id,
                test_id=test.test_id,
                condition=self._text(subject, "condition"),
                condition_status=self._text(subject, "conditionStatus"),
                description=self._text(subject, "transitionDesc"),
                description_status=self._text(subject, "transitionDescStatus"),
                chunk_ids=self._texts(subject, "transitionChunkIds"),
            )
            if not transition.condition:
                self.data_quality_notes.append(f"Transition {transition.transition_id} has no condition")
            self.transitions[transition.transition_id] = transition
            self._outgoing[transition.source_id].append(transition)

    def _load_trees(self) -> None:
        for subject in self.graph.subjects(self.rdf.type, self.qtl.FaultTree):
            tree_id = self._text(subject, "treeId") or self._local_id(subject, "FaultTree_")
            symptom_ids = []
            for symptom_uri in self._objects(subject, "hasSymptom"):
                symptom = self.symptoms_by_uri.get(str(symptom_uri))
                if symptom:
                    symptom_ids.append(symptom.symptom_id)
            tree = FaultTree(
                uri=str(subject),
                tree_id=tree_id,
                name=self._text(subject, "treeName") or tree_id,
                description=self._text(subject, "treeDesc"),
                applicable_scope=self._text(subject, "applicableScope"),
                version=self._text(subject, "version"),
                symptom_ids=symptom_ids,
            )
            self.trees[tree.tree_id] = tree

    def _build_paths(self) -> None:
        for tree in self.trees.values():
            tree_symptoms = set(tree.symptom_ids)
            starts = [
                node
                for node in self.symptoms.values()
                if node.symptom_id in tree_symptoms and node.level == "start"
            ]
            paths: list[DiagnosticPath] = []
            for start in starts:
                self._dfs_paths(
                    tree_id=tree.tree_id,
                    tree_symptoms=tree_symptoms,
                    node_id=start.symptom_id,
                    node_ids=[start.symptom_id],
                    transitions=[],
                    paths=paths,
                )
            self._tree_paths[tree.tree_id] = paths

    def _dfs_paths(
        self,
        tree_id: str,
        tree_symptoms: set[str],
        node_id: str,
        node_ids: list[str],
        transitions: list[Transition],
        paths: list[DiagnosticPath],
    ) -> None:
        node = self.symptoms[node_id]
        outgoing = [t for t in self._outgoing.get(node_id, []) if t.target_id in tree_symptoms]
        if node.level == "root" or not outgoing:
            paths.append(
                DiagnosticPath(
                    tree_id=tree_id,
                    node_ids=list(node_ids),
                    transition_ids=[t.transition_id for t in transitions],
                    test_ids=[t.test_id for t in transitions],
                    root_cause_id=node_id if node.level == "root" else None,
                )
            )
            return
        for transition in outgoing:
            if transition.target_id in node_ids:
                self.data_quality_notes.append(f"Cycle skipped at {transition.transition_id}")
                continue
            self._dfs_paths(
                tree_id=tree_id,
                tree_symptoms=tree_symptoms,
                node_id=transition.target_id,
                node_ids=[*node_ids, transition.target_id],
                transitions=[*transitions, transition],
                paths=paths,
            )

    def search_trees(self, phenomenon: str, top_k: int = 5) -> list[tuple[FaultTree, float, list[str]]]:
        query = phenomenon.strip().lower()
        scored: list[tuple[FaultTree, float, list[str]]] = []
        for tree in self.trees.values():
            reasons: list[str] = []
            score = 0.0
            start_nodes = [
                self.symptoms[sid]
                for sid in tree.symptom_ids
                if sid in self.symptoms and self.symptoms[sid].level == "start"
            ]
            candidates = [tree.name, tree.description or "", tree.applicable_scope or ""]
            candidates.extend(node.name for node in start_nodes)
            candidates.extend(node.description or "" for node in start_nodes)
            for text in candidates:
                text_lower = text.lower()
                if query and query in text_lower:
                    score = max(score, 1.0)
                    reasons.append(f"contains:{text}")
                score = max(score, SequenceMatcher(None, query, text_lower).ratio())
            if score > 0.2:
                scored.append((tree, round(score, 4), reasons[:3] or ["fuzzy_match"]))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def enumerate_paths(self, tree_id: str) -> list[DiagnosticPath]:
        return list(self._tree_paths.get(tree_id, []))

    def start_node_id(self, tree_id: str) -> str | None:
        tree = self.trees.get(tree_id)
        if not tree:
            return None
        for symptom_id in tree.symptom_ids:
            node = self.symptoms.get(symptom_id)
            if node and node.level == "start":
                return node.symptom_id
        return None

    def outgoing_transitions(self, node_id: str, tree_id: str | None = None) -> list[Transition]:
        transitions = list(self._outgoing.get(node_id, []))
        if not tree_id:
            return transitions
        tree = self.trees.get(tree_id)
        if not tree:
            return []
        allowed = set(tree.symptom_ids)
        return [transition for transition in transitions if transition.target_id in allowed]

    def transition_for_test(
        self,
        test_id: str,
        source_node_id: str | None = None,
        tree_id: str | None = None,
    ) -> Transition | None:
        transitions = (
            self.outgoing_transitions(source_node_id, tree_id)
            if source_node_id
            else self.transitions.values()
        )
        for transition in transitions:
            if transition.test_id == test_id:
                return transition
        return None

    def make_candidate_causes(self, paths: Iterable[DiagnosticPath]) -> list[CandidateCause]:
        causes: list[CandidateCause] = []
        for path in paths:
            if not path.root_cause_id:
                continue
            node = self.symptoms.get(path.root_cause_id)
            if not node:
                continue
            causes.append(
                CandidateCause(
                    cause_id=node.symptom_id,
                    name=node.name,
                    path=path,
                    measure_ids=node.measure_ids,
                    score=path.score,
                    reasons=list(path.match_reasons),
                )
            )
        causes.sort(key=lambda cause: cause.score, reverse=True)
        return causes

    def get_test(self, test_id: str) -> DiagnosticTest | None:
        return self.tests.get(test_id)

    def get_symptom(self, symptom_id: str) -> SymptomNode | None:
        return self.symptoms.get(symptom_id)

    def get_measure(self, measure_id: str) -> Measure | None:
        return self.measures.get(measure_id)

    def get_transition(self, transition_id: str) -> Transition | None:
        return self.transitions.get(transition_id)

    def describe_path(self, path: DiagnosticPath) -> str:
        names = [self.symptoms[node_id].name for node_id in path.node_ids if node_id in self.symptoms]
        return " -> ".join(names)
