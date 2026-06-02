from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> None:
    from ft_diag_agent.models import TreeGenerationArtifact
    from ft_diag_agent.tree_generation import render_tree_generation_mermaid

    parser = argparse.ArgumentParser(description="Render a TreeGenerationArtifact as a Mermaid tree.")
    parser.add_argument("artifact", help="Path to data/tree_generation/artifacts/<job_id>/artifact.json")
    parser.add_argument("--output", "-o", help="Optional Markdown output path.")
    args = parser.parse_args()

    artifact_path = Path(args.artifact)
    artifact = TreeGenerationArtifact.model_validate_json(artifact_path.read_text(encoding="utf-8"))
    mermaid = render_tree_generation_mermaid(artifact)
    if args.output:
        Path(args.output).write_text(mermaid + "\n", encoding="utf-8")
    else:
        print(mermaid)


if __name__ == "__main__":
    main()
