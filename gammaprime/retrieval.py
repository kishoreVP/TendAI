"""Structural retrieval over document-tree nodes.

Each node is indexed with its section path (parent-context expansion), so a table row
"2024-25 | 26.40" is findable by its section "CHARTERED ACCOUNTANT CERTIFICATE - ANNUAL
TURNOVER". Lexical BM25 + an exact-number boost. A fixed-size-chunk baseline is provided
for the retrieval evaluation (scripts/retrieval_eval.py). No vector index: add one only
when the evaluation shows lexical retrieval is the limitation.
"""
import math
import re
from collections import Counter

TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")


def tokenize(text):
    return TOKEN.findall(text.lower())


def section_paths(nodes):
    """{node_id: 'Section / Sub-section / clause...'} following parent links."""
    by_id = {n.id: n for n in nodes}
    out = {}
    for n in nodes:
        path, p = [], by_id.get(n.parent_id)
        while p is not None:
            path.append(p.text[:120])
            p = by_id.get(p.parent_id)
        out[n.id] = " / ".join(reversed(path))
    return out


class BM25:
    def __init__(self, texts, k1=1.5, b=0.75):
        self.docs = [Counter(tokenize(t)) for t in texts]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.len) / max(len(self.len), 1)
        df = Counter(w for d in self.docs for w in d)
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.k1, self.b = k1, b

    def scores(self, query):
        q = tokenize(query)
        out = []
        for d, ln in zip(self.docs, self.len):
            s = 0.0
            for w in q:
                f = d.get(w, 0)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / self.avg))
            numbers = [w for w in q if w[0].isdigit()]
            if numbers and all(w in d for w in numbers):
                s *= 1.5  # exact numeric / identifier match
            out.append(s)
        return out


class NodeIndex:
    """Index over located nodes (clauses, paragraphs, table rows, sheet rows)."""
    KINDS = ("clause", "paragraph", "row", "section")

    def __init__(self, nodes):
        self.paths = section_paths(nodes)
        self.nodes = [n for n in nodes if n.kind in self.KINDS and n.text.strip()]
        self.bm25 = BM25([self.paths[n.id] + " " + n.text for n in self.nodes])

    def search(self, query, k=5, where=None):
        """-> [(score, node, section_path)] best first. where: optional predicate(node, path)."""
        scored = [(s, n) for s, n in zip(self.bm25.scores(query), self.nodes) if s > 0]
        if where:
            scored = [(s, n) for s, n in scored if where(n, self.paths[n.id])]
        scored.sort(key=lambda x: -x[0])
        return [(s, n, self.paths[n.id]) for s, n in scored[:k]]


class ChunkIndex:
    """Baseline: fixed-size character chunks of the page text (conventional RAG)."""

    def __init__(self, pdf_path, size=800, overlap=100):
        import pymupdf
        self.chunks = []  # (page, text)
        for pno, page in enumerate(pymupdf.open(pdf_path), start=1):
            t = page.get_text()
            for i in range(0, max(len(t), 1), size - overlap):
                self.chunks.append((pno, t[i:i + size]))
        self.bm25 = BM25([c[1] for c in self.chunks])

    def search(self, query, k=5):
        scored = sorted(zip(self.bm25.scores(query), self.chunks), key=lambda x: -x[0])
        return [(s, page, text) for s, (page, text) in scored[:k] if s > 0]
