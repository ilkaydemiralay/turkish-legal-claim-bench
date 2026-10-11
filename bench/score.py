"""runs/*.jsonl sonuçlarını puanla. Kullanım: python3 bench/score.py"""
import json, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ITEMS = {j["id"]: j for j in (json.loads(l) for l in (ROOT / "bench/items.jsonl").read_text().splitlines() if l.strip())}
L = ["destekliyor", "celisiyor", "sessiz"]

print(f"{'model':16} {'n':>3} {'doğru':>6} {'macroF1':>8} {'hata→destek':>11} {'eski hata yakalanan':>20} {'medyan ms':>9}")
for p in sorted((ROOT / "runs").glob("*.jsonl")):
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    rows = [dict(r, label=ITEMS[r["id"]]["label"]) for r in rows if r["id"] in ITEMS and not ITEMS[r["id"]].get("exclude")]
    if not rows:
        continue
    acc = sum(r["pred"] == r["label"] for r in rows) / len(rows)
    f1s = []
    for c in L:
        tp = sum(r["pred"] == c and r["label"] == c for r in rows)
        fp = sum(r["pred"] == c and r["label"] != c for r in rows)
        fn = sum(r["pred"] != c and r["label"] == c for r in rows)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    # en tehlikeli hata: yanlış/dayanaksız iddiaya "destekliyor" demek (kapıdan geçirir)
    danger = sum(r["pred"] == "destekliyor" and r["label"] != "destekliyor" for r in rows)
    old = [r for r in rows if ITEMS[r["id"]]["origin"] == "eski_hata"]
    caught = sum(r["pred"] in ("celisiyor", "sessiz") for r in old)
    ms = [r["ms"] for r in rows if r.get("ms")]
    print(f"{p.stem:16} {len(rows):>3} {acc:>6.0%} {statistics.mean(f1s):>8.2f} {danger:>11} {caught:>14}/{len(old):<5} "
          f"{(statistics.median(ms) if ms else 0):>9.0f}")

print("\nYanlışlar (model: id beklenen→tahmin):")
for p in sorted((ROOT / "runs").glob("*.jsonl")):
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    rows = [dict(r, label=ITEMS[r["id"]]["label"]) for r in rows if r["id"] in ITEMS and not ITEMS[r["id"]].get("exclude")]
    bad = [f"{r['id']} {r['label'][:4]}→{str(r['pred'])[:4]}" for r in rows if r["pred"] != r["label"]]
    print(f"- {p.stem}: " + ", ".join(bad))
