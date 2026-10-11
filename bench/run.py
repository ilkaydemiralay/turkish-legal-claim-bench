"""Mevzuat iddia testi: her iddia + resmi pasaj -> destekliyor / celisiyor / sessiz.

Kullanım: python3 bench/run.py <model> [--limit N]
Modeller: jev, clef, clef-flash, gemma4-12b, gemma4-26b
Çıktı: runs/<model>.jsonl (kaldığı yerden devam eder)

Anahtarlar ortamdan: TYPESAFE_KEY (jev), CF_ACCOUNT_ID + CF_API_TOKEN (clef).
"""
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EV = json.loads((ROOT / "bench/evidence.json").read_text())
ITEMS = [json.loads(l) for l in (ROOT / "bench/items.jsonl").read_text().splitlines() if l.strip()]

LABELS = {
    "destekliyor": "Resmi metin iddiayı açıkça doğruluyor; iddiadaki kural, şart, süre ve sayılar metinle uyumlu.",
    "celisiyor": "Resmi metin iddiayla çelişiyor; metne göre iddia yanlış (farklı şart, süre, sayı, madde numarası, kapsam ya da yürürlük durumu).",
    "sessiz": "Resmi metin bu konuda hüküm içermiyor; iddia ne doğrulanabiliyor ne çürütülebiliyor.",
}
INSTR = ("Aşağıda Türk mevzuatından resmi bir pasaj ve bir yazıdan alınmış bir iddia var. "
         "Yalnızca verilen resmi pasaja dayanarak iddiayı değerlendir; genel bilgini kullanma.")
Q_VERDICT = "Resmi pasaj bu iddia hakkında ne söylüyor?"
Q_SUPPORT = "Resmi pasaj bu iddiayı açıkça destekliyor mu?"

# İngilizce talimat varyantı (pasaj ve iddia Türkçe kalır): INSTR_LANG=en
if os.environ.get("INSTR_LANG") == "en":
    LABELS = {
        "destekliyor": "The official passage clearly confirms the claim; its rule, conditions, durations and numbers match the text.",
        "celisiyor": "The official passage contradicts the claim; according to the text the claim is wrong (different condition, duration, number, article number, scope or in-force status).",
        "sessiz": "The official passage says nothing on this point; the claim can be neither confirmed nor refuted from it.",
    }
    INSTR = ("Below is an official passage from Turkish legislation (in Turkish) and a claim taken from an article (in Turkish). "
             "Judge the claim using only the given official passage; do not use general knowledge.")
    Q_VERDICT = "What does the official passage say about this claim?"
    Q_SUPPORT = "Does the official passage clearly support this claim?"


def state_of(it):
    e = EV[it["ev"]]
    return {"resmi_pasaj_kaynagi": e["source"], "resmi_pasaj": e["text"], "iddia": it["claim"]}


def prompt_of(it):
    s = state_of(it)
    opts = "\n".join(f"- {k}: {v}" for k, v in LABELS.items())
    return (f"{INSTR}\n\nKaynak: {s['resmi_pasaj_kaynagi']}\nResmi pasaj:\n{s['resmi_pasaj']}\n\n"
            f"İddia: {s['iddia']}\n\n{Q_VERDICT} Seçenekler:\n{opts}\n\n"
            'Yalnızca şu JSON ile cevap ver: {"karar": "destekliyor|celisiyor|sessiz"}')


# --- System One uyumlu karar modelleri (Jev, Clef) ---
def systemone(it, model):
    qs = {
        "verdict": {"type": "choice", "instructions": f"{INSTR} {Q_VERDICT}", "criteria": LABELS},
        "supported": {"type": "noul", "instructions": f"{INSTR} {Q_SUPPORT}"},
    }
    if model == "jev":
        url, key, body = "https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_KEY"], {"model": "jev-latest"}
    else:
        acc = os.environ["CF_ACCOUNT_ID"]
        url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/cloudflare/{model}"
        key, body = os.environ["CF_API_TOKEN"], {"model": model}
    body.update(state=state_of(it), questions=qs)
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    out = out.get("result", out)  # Cloudflare sarmalayıcısı
    a = out["answers"]
    return {"pred": a["verdict"].get("choice"), "conf": a["verdict"].get("confidence"),
            "probs": a["verdict"].get("probabilities") or a["verdict"].get("scores"),
            "p_supported": a["supported"].get("noul"), "usage": out.get("usage"), "raw": a}


# --- OpenAI Decisions (/v1/decisions, gpt-6-luna) ---
def decisions(it):
    s = state_of(it)
    body = {"model": "gpt-6-luna",
            "input": f"Kaynak: {s['resmi_pasaj_kaynagi']}\nResmi pasaj:\n{s['resmi_pasaj']}\n\nİddia: {s['iddia']}",
            "questions": [
                {"type": "choice", "name": "verdict", "instructions": f"{INSTR} {Q_VERDICT}",
                 "choices": [{"value": k, "description": v} for k, v in LABELS.items()]},
                {"type": "predicate", "name": "supported", "instructions": f"{INSTR} {Q_SUPPORT}"},
            ]}
    req = urllib.request.Request("https://api.openai.com/v1/decisions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}")
    a = {x.get("name", i): x for i, x in enumerate(out["answers"])}
    v, sup = a.get("verdict", {}), a.get("supported", {})
    return {"pred": v.get("choice"), "conf": v.get("confidence"), "probs": v.get("probabilities"),
            "p_supported": sup.get("probability"), "refusal": v.get("refusal"),
            "usage": out.get("usage"), "raw": out["answers"]}


# --- yerel Gemma (Ollama, şema zorlamalı) ---
def ollama(it, model, think=False):
    body = {"model": model, "stream": False, "think": think,
            "options": {"temperature": 0, "num_predict": int(os.environ.get("THINK_BUDGET", 2048)) if think else 64},
            "messages": [{"role": "user", "content": prompt_of(it)}],
            "format": {"type": "object", "properties": {"karar": {"type": "string", "enum": list(LABELS)}},
                       "required": ["karar"]}}
    req = urllib.request.Request("http://localhost:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        out = json.load(r)
    return {"pred": json.loads(out["message"]["content"])["karar"],
            "usage": {"input_tokens": out.get("prompt_eval_count"), "output_tokens": out.get("eval_count")}}


# Claude: CLI her çağrıda ~760 bin token bağlam yüklüyor (2026-10-10 ölçümü); bu yüzden
# Claude modelleri bench/llm_input.md ile alt ajanda toplu koşulur, sonuç runs/<model>.jsonl.


BACKENDS = {
    "jev": lambda it: systemone(it, "jev"),
    "clef": lambda it: systemone(it, "clef"),
    "clef-flash": lambda it: systemone(it, "clef-flash"),
    "gpt-6-luna-decisions": decisions,
    "gemma4-12b": lambda it: ollama(it, "gemma4:12b"),
    "gemma4-26b": lambda it: ollama(it, "gemma4:26b-a4b-it-qat"),
    "gemma4-12b-think": lambda it: ollama(it, "gemma4:12b", think=True),
    "gemma4-12b-think8k": lambda it: ollama(it, "gemma4:12b", think=True),  # THINK_BUDGET=8192, yalnız 2k'da biten iddialar
    "gemma4-26b-think": lambda it: ollama(it, "gemma4:26b-a4b-it-qat", think=True),
}


def main():
    name = sys.argv[1]
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    fn = BACKENDS[name]
    out_path = ROOT / "runs" / f"{name}{'-en' if os.environ.get('INSTR_LANG') == 'en' else ''}.jsonl"
    done = set()
    if out_path.exists():
        done = {json.loads(l)["id"] for l in out_path.read_text().splitlines() if l.strip()}
    only = os.environ.get("ONLY")
    todo = [it for it in ITEMS if it["id"] not in done and (not only or it["id"] in only.split(","))][:limit]
    with out_path.open("a") as f:
        for it in todo:
            t0 = time.time()
            try:
                res = fn(it)
            except Exception as e:  # hatayı kaydet, sonraki iddiaya geç
                res = {"pred": None, "error": f"{type(e).__name__}: {str(e)[:300]}"}
            res.update(id=it["id"], label=it["label"], ms=round((time.time() - t0) * 1000))
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{it['id']:8} {it['label']:12} -> {res.get('pred')!s:12} {res['ms']} ms {res.get('error', '')}")


if __name__ == "__main__":
    main()
