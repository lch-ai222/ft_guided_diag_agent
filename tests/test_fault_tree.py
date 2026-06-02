from pathlib import Path

from ft_diag_agent.fault_tree import RdfFaultTreeRepository

ROOT = Path(__file__).resolve().parents[1]
TTL = ROOT / "corrected_fault_tree_instances.ttl"


def test_parse_demo_ttl() -> None:
    repo = RdfFaultTreeRepository(TTL)

    assert len(repo.trees) == 2
    assert "FT_001" in repo.trees
    assert "FT_002" in repo.trees
    assert repo.get_symptom("S001").name == "车机黑屏"
    assert repo.get_test("T112").test_id == "T112"
    assert repo.get_measure("M101").name == "更换门锁执行器"


def test_enumerate_paths_to_root() -> None:
    repo = RdfFaultTreeRepository(TTL)

    black_paths = repo.enumerate_paths("FT_001")
    door_paths = repo.enumerate_paths("FT_002")

    assert len(black_paths) == 7
    assert len(door_paths) == 7
    assert all(path.root_cause_id for path in black_paths)
    assert any(path.root_cause_id == "S105" for path in door_paths)


def test_search_tree_by_phenomenon() -> None:
    repo = RdfFaultTreeRepository(TTL)

    matches = repo.search_trees("车门无法关闭")

    assert matches
    assert matches[0][0].tree_id == "FT_002"
