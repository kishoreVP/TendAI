"""Retrieval evaluation on the M.246 cable tender: hierarchical node retrieval vs a fixed-size
chunk baseline. Expected pages were verified manually against the PDF text.

    python scripts/retrieval_eval.py
Metrics: page recall@k (any expected page in top k), page precision@k (share of top-k hits on an
expected page), and row hit@k for questions whose answer is a specific table row.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gammaprime import ingest  # noqa: E402
from gammaprime.db import Node, make_session_factory  # noqa: E402
from gammaprime.retrieval import ChunkIndex, NodeIndex  # noqa: E402

PDF = Path(__file__).resolve().parents[1] / "data/samples/cable_m246/M246Spec.pdf"
QUESTIONS = [  # (question, expected pages, expected table row "Sl.No|text fragment" or None)
    ("What annual turnover must the tenderer have?", {17}, None),
    ("How much is the earnest money deposit?", {7, 13}, None),
    ("Are micro and small enterprises exempted from EMD?", {14}, None),
    ("Minimum quantity of cable supplied in the previous ten years", {17}, None),
    ("Is a BIS license required for the tendered cable?", {18}, None),
    ("Who qualifies as a new entrant?", {18}, None),
    ("Maximum DC resistance of conductor at 20 degrees", {57, 63, 65}, "6|0.0469"),
    ("Number of strands in each core", {57, 63}, "7|91"),
    ("Minimum thickness of insulation", {63}, "13|7.8"),
    ("AC resistance at 90 degrees maximum", {65}, "26|0.065"),
    ("Standard drum length of cable", {57, 64}, "21|500"),
    ("Price basis FOR destination", {26}, None),
    ("When will the delivery schedule be indicated?", {37}, None),
]


def main(k=3):
    s = make_session_factory("sqlite://")()
    doc = ingest.ingest(s, ingest.get_case(s, "eval"), PDF, "tender")
    nodes = s.query(Node).filter_by(doc_id=doc.id).order_by(Node.seq).all()
    tree, chunks = NodeIndex(nodes), ChunkIndex(PDF)
    rows = {"tree": [0, 0, 0, 0], "chunks": [0, 0, 0, 0]}  # recall hits, precision sum, row hits, row questions
    print(f"{'question':58} tree-pages        chunk-pages")
    for q, pages, row in QUESTIONS:
        t = tree.search(q, k=k, where=lambda n, p: n.kind != "section")
        c = chunks.search(q, k=k)
        tp, cp = [n.page for _, n, _ in t], [p for _, p, _ in c]
        for name, got in (("tree", tp), ("chunks", cp)):
            rows[name][0] += bool(set(got) & pages)
            rows[name][1] += sum(p in pages for p in got) / max(len(got), 1)
        if row:
            sl, frag = row.split("|")
            rows["tree"][3] += 1
            rows["tree"][2] += any(n.kind == "row" and (n.ref or "").strip() == sl and frag in n.text for _, n, _ in t)
        print(f"{q[:58]:58} {str(tp):17} {cp}")
    n = len(QUESTIONS)
    print(f"\nk={k}, {n} questions")
    print(f"tree   : page recall@{k} {rows['tree'][0] / n:.2f}  precision@{k} {rows['tree'][1] / n:.2f}  "
          f"row hit@{k} {rows['tree'][2]}/{rows['tree'][3]}")
    print(f"chunks : page recall@{k} {rows['chunks'][0] / n:.2f}  precision@{k} {rows['chunks'][1] / n:.2f}  "
          f"row hit@{k} n/a (chunks have no row structure)")


if __name__ == "__main__":
    main()
